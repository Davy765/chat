"""Multi-market portfolio engine for the "Modern Turtle" rulebook.

Implements the rulebook as written (long/flat, Track B: ETFs), so it can be
tested, not because it has been shown to work. Rules, with the reading chosen
where the rulebook is ambiguous marked AMBIGUOUS:

N         max(Wilder ATR(20), 0.5 x Wilder ATR(100)), computed at each close.
Trend     long only while SMA(50) > SMA(200) at the signal close.
Entry     close > highest high of the prior 55 bars -> buy at the NEXT open.
          Every valid signal is taken (no skip filter).
Size      1 unit = floor(0.5% x sizing equity / (3N)) shares. If that is 0
          but one share risks <= 1% of sizing equity: 1 share, no adds.
          Otherwise skip.
Adds      close >= last fill + 1N -> add 1 unit at the next open; max 3 units.
          AMBIGUOUS: the add trigger and the trailing distance use the N
          frozen at the first entry ("don't resize open units"); each added
          unit is sized with the N and equity of its own signal day.
Stops     one shared stop for all units, a resting GTC stop order:
          initially fill - 3N; after an add, max(newest fill - 3N, trailing);
          trailing = highest close since entry - 3N. It only moves up.
          Intraday: gap through -> fill at the open; touch -> fill at stop.
Channel   close < lowest low of the prior 20 bars -> sell at the next open.
Caps      <= 6 units per correlated group, <= 12 long units in total, and
          heat (sum of (close - stop) x shares, floored at 0, over all open
          positions plus approved orders) + the new unit's 3N risk must stay
          <= 6% of equity. When caps bind, adds go first, then entries in
          order of breakout strength (close - channel) / N.
Drawdown  k = floor(drawdown from equity peak / 10%); sizing equity =
          equity x (1 - 0.20k) (the rulebook's linear version: 80%, 60%, ...).
          At a 25% drawdown: no new entries or adds for 10 sessions, once
          per drawdown episode (an episode ends at a new equity peak).
          AMBIGUOUS: adds are treated as "new trades" during the pause.

Account / simulator (not rulebook rules, documented assumptions):
* Gross notional is capped at ``max_leverage`` x equity at fill time;
  orders are shrunk to fit. Default 1x = cash account.
* Idle equity earns the prior day's T-bill rate; borrowed money (gross >
  equity) pays T-bill + ``margin_spread_pct``.
* Slippage in bps on every fill, against us.
* Zero-volume bars are dropped. A market may only open or add while its
  median dollar volume over the prior 60 bars is >= ``min_dollar_volume``.
* Same-bar ambiguity is resolved pessimistically: an order filled at the
  open is checked against its stop on the same bar's low.
* Information: signals use bar t's close; fills happen at bar t+1's open.
  Nothing from bar t+1 is used before its open.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .backtest import align_cash_rate
from .indicators import donchian, wilder_atr


@dataclass(frozen=True)
class ModernConfig:
    entry_len: int = 55
    exit_len: int = 20
    atr_len: int = 20
    atr_floor_len: int = 100
    atr_floor_mult: float = 0.5
    use_trend_filter: bool = True
    trend_fast: int = 50
    trend_slow: int = 200
    stop_n: float = 3.0
    use_trailing: bool = True
    trail_n: float = 3.0
    add_n: float = 1.0
    max_units: int = 3
    risk_per_unit: float = 0.005
    single_share_max_risk: float = 0.01
    group_unit_cap: int = 6
    total_long_units: int = 12
    heat_cap: float = 0.06
    dd_step: float = 0.10
    dd_cut: float = 0.20
    pause_dd: float = 0.25
    pause_days: int = 10
    max_leverage: float = 1.0
    # Optional (not a rulebook rule): cap one market's notional at this
    # fraction of equity. None = rulebook as written.
    max_position_pct: float | None = None
    slippage_bps: float = 2.0
    commission_per_share: float = 0.0
    margin_spread_pct: float = 1.5
    min_dollar_volume: float = 1e6
    initial_equity: float = 100_000.0


@dataclass
class PTrade:
    ticker: str
    group: str
    entry_date: pd.Timestamp
    exit_date: pd.Timestamp
    units: int
    shares: int
    avg_entry: float
    exit_price: float
    pnl: float
    costs: float
    r_multiple: float
    exit_reason: str
    same_bar_stop: bool


@dataclass
class _Pos:
    n: float                 # N frozen at first entry
    entry_date: pd.Timestamp
    no_adds: bool
    unit_risk: float         # 1R in $: 3N x first unit's intended shares
    shares: int = 0
    units: int = 0
    cost_basis: float = 0.0
    costs: float = 0.0
    commissions: float = 0.0
    last_fill: float = 0.0
    stop: float = -np.inf
    high_close: float = -np.inf
    same_bar_stop: bool = False


@dataclass
class _Order:
    kind: str                # "entry" | "add" | "exit"
    shares: int = 0
    n: float = 0.0
    no_adds: bool = False
    unit_risk: float = 0.0


@dataclass
class PortfolioResult:
    equity: pd.Series
    daily: pd.DataFrame
    trades: list[PTrade]
    open_positions: list[dict]
    config: ModernConfig
    log: list[str] = field(default_factory=list)
    # State after the last close, for the paper-trading report:
    pending_orders: list[dict] = field(default_factory=list)   # orders for the NEXT open
    state: dict = field(default_factory=dict)                  # equity, peak, sizing, pause
    snapshot: list[dict] = field(default_factory=list)         # per-market levels for the next session

    def trades_frame(self) -> pd.DataFrame:
        return pd.DataFrame([t.__dict__ for t in self.trades])


def sizing_equity(cfg: ModernConfig, equity: float, peak: float) -> float:
    """Rulebook drawdown rule: equity x (1 - 0.2k), k = full 10% steps down."""
    if equity >= peak:
        return equity
    k = math.floor((1 - equity / peak) / cfg.dd_step + 1e-12)
    return equity * max(1 - cfg.dd_cut * k, 0.0)


class _Market:
    """One market's bars and indicators, on its own (gap-free) calendar."""

    def __init__(self, ticker: str, group: str, df: pd.DataFrame, cfg: ModernConfig):
        df = df[df["Volume"] > 0] if "Volume" in df.columns else df
        self.ticker, self.group = ticker, group
        self.dates = df.index
        self.o, self.h, self.l, self.c = (df[k].to_numpy(float) for k in ("Open", "High", "Low", "Close"))
        n20 = wilder_atr(df, cfg.atr_len).to_numpy()
        n100 = wilder_atr(df, cfg.atr_floor_len).to_numpy()
        self.n = np.maximum(n20, cfg.atr_floor_mult * n100)       # NaN until both exist
        self.ent_hi = donchian(df, cfg.entry_len)[0].to_numpy()
        self.ex_lo = donchian(df, cfg.exit_len)[1].to_numpy()
        close = df["Close"]
        fast = close.rolling(cfg.trend_fast).mean().to_numpy()
        slow = close.rolling(cfg.trend_slow).mean().to_numpy()
        self.trend_ok = (fast > slow) if cfg.use_trend_filter else np.ones(len(df), bool)
        if "Volume" in df.columns:
            dv = (df["Close"] * df["Volume"]).rolling(60, min_periods=60).median().shift(1)
            self.liquid = (dv >= cfg.min_dollar_volume).to_numpy()
        else:
            self.liquid = np.ones(len(df), bool)
        self.pos: _Pos | None = None
        self.pending: list[_Order] = []


class PortfolioBacktester:
    def __init__(self, config: ModernConfig | None = None):
        self.cfg = config or ModernConfig()

    # Research hook: entry signal and strength for bar j of market m, using
    # data up to and including bar j's close. Random-entry benchmarks
    # override this; the fills, stops, exits and caps stay identical.
    def entry_signal(self, m: _Market, j: int) -> tuple[bool, float]:
        if np.isnan(m.ent_hi[j]) or np.isnan(m.n[j]) or not m.trend_ok[j]:
            return False, 0.0
        if m.c[j] > m.ent_hi[j]:
            return True, (m.c[j] - m.ent_hi[j]) / m.n[j]
        return False, 0.0

    def _slip(self, px: float, side: int) -> float:
        return px * (1 + side * self.cfg.slippage_bps / 1e4)

    def run(self, data: dict[str, pd.DataFrame], groups: dict[str, str],
            cash_rate: pd.Series | None = None, start: str | None = None) -> PortfolioResult:
        """``start``: first session that may trade (equity = initial_equity
        there). Indicators still use all earlier history."""
        cfg = self.cfg
        mkts = [_Market(t, groups[t], df, cfg) for t, df in data.items()]
        cal = mkts[0].dates
        for m in mkts[1:]:
            cal = cal.union(m.dates)
        # position of each market's bar on the union calendar (-1 = no bar)
        bar_at = []
        for m in mkts:
            idx = np.full(len(cal), -1)
            idx[cal.get_indexer(m.dates)] = np.arange(len(m.dates))
            bar_at.append(idx)
        rate = align_cash_rate(cash_rate, cal)
        i0 = 0 if start is None else int(cal.searchsorted(pd.Timestamp(start)))

        cash = cfg.initial_equity
        peak = cfg.initial_equity
        prev_equity = cfg.initial_equity
        pause_left, paused_this_episode = 0, False
        trades: list[PTrade] = []
        log: list[str] = []
        T = len(cal)
        eq_hist = np.full(T, np.nan)
        gross_hist, heat_hist, units_hist = np.zeros(T), np.zeros(T), np.zeros(T)
        interest_hist, sizing_hist = np.zeros(T), np.full(T, np.nan)
        last_close = {m.ticker: np.nan for m in mkts}

        def gross_notional() -> float:
            return sum(m.pos.shares * last_close[m.ticker] for m in mkts if m.pos is not None)

        def commission(sh: int) -> float:
            return abs(sh) * cfg.commission_per_share

        def buy(m: _Market, raw: float, shares: int) -> None:
            nonlocal cash
            p = m.pos
            fill = self._slip(raw, +1)
            comm = commission(shares)
            cash -= shares * fill + comm
            p.shares += shares
            p.units += 1
            p.cost_basis += shares * fill
            p.costs += shares * (fill - raw) + comm
            p.commissions += comm
            p.last_fill = raw

        def sell_all(m: _Market, raw: float, reason: str, date) -> None:
            nonlocal cash
            p = m.pos
            fill = self._slip(raw, -1)
            comm = commission(p.shares)
            cash += p.shares * fill - comm
            p.costs += p.shares * (raw - fill) + comm
            p.commissions += comm
            avg = p.cost_basis / p.shares
            pnl = (fill - avg) * p.shares - p.commissions
            trades.append(PTrade(m.ticker, m.group, p.entry_date, date, p.units, p.shares, avg, fill,
                                 pnl, p.costs, pnl / p.unit_risk if p.unit_risk else np.nan,
                                 reason, p.same_bar_stop))
            m.pos = None

        def cap_shares(want: int, price: float, m: _Market | None = None) -> int:
            room = cfg.max_leverage * prev_equity - gross_notional()
            if cfg.max_position_pct is not None and m is not None:
                held = m.pos.shares * last_close[m.ticker] if m.pos is not None else 0.0
                room = min(room, cfg.max_position_pct * prev_equity - held)
            return max(0, min(want, math.floor(room / price)))

        for i in range(i0, T):
            date = cal[i]
            # ---------------- before the open: interest / margin cost
            if i > i0 and rate[i]:
                days = (cal[i] - cal[i - 1]).days
                idle = prev_equity - gross_notional()
                r = rate[i] if idle >= 0 else rate[i] + cfg.margin_spread_pct
                accr = idle * r / 100 * days / 365
                cash += accr
                interest_hist[i] = accr

            # ---------------- the open: queued orders, then stops
            for m, at in zip(mkts, bar_at):
                j = at[i]
                if j < 0:
                    continue
                o, h, l = m.o[j], m.h[j], m.l[j]
                orders, m.pending = m.pending, []
                # 1) positions held overnight: channel exit or gapped stop at the open
                if m.pos is not None:
                    if any(od.kind == "exit" for od in orders):
                        sell_all(m, o, "channel_exit", date)
                    elif o <= m.pos.stop:
                        sell_all(m, o, "stop_gap", date)
                # 2) entries and adds fill at the open
                fresh = False
                for od in orders:
                    if od.kind == "entry" and m.pos is None:
                        sh = cap_shares(od.shares, o, m)
                        if sh <= 0:
                            log.append(f"{date.date()} {m.ticker}: entry dropped, leverage cap")
                            continue
                        m.pos = _Pos(n=od.n, entry_date=date, no_adds=od.no_adds, unit_risk=od.unit_risk)
                        buy(m, o, sh)
                        m.pos.stop = o - cfg.stop_n * od.n
                        fresh = True
                    elif od.kind == "add" and m.pos is not None:
                        sh = cap_shares(od.shares, o, m)
                        if sh <= 0:
                            continue
                        buy(m, o, sh)
                        trail = m.pos.high_close - cfg.trail_n * m.pos.n if cfg.use_trailing else -np.inf
                        m.pos.stop = max(m.pos.stop, o - cfg.stop_n * m.pos.n, trail)
                        fresh = True
                # 3) intraday: the resting stop (below the open here) is touched
                if m.pos is not None and l <= m.pos.stop:
                    if fresh:
                        m.pos.same_bar_stop = True
                    sell_all(m, m.pos.stop, "stop", date)

            # ---------------- the close: mark to market
            for m, at in zip(mkts, bar_at):
                j = at[i]
                if j >= 0:
                    last_close[m.ticker] = m.c[j]
            equity = cash + gross_notional()
            eq_hist[i] = equity
            gross_hist[i] = gross_notional()
            if equity > peak:
                peak, paused_this_episode = equity, False
            dd = 1 - equity / peak
            if pause_left > 0:
                pause_left -= 1
            if dd >= cfg.pause_dd and not paused_this_episode:
                pause_left, paused_this_episode = cfg.pause_days, True
                log.append(f"{date.date()}: drawdown {dd:.1%}, new trades paused {cfg.pause_days} sessions")
            seq = sizing_equity(cfg, equity, peak)
            sizing_hist[i] = seq

            # ---------------- after the close: update stops, find signals
            heat = 0.0
            adds, entries = [], []
            for m, at in zip(mkts, bar_at):
                j = at[i]
                p = m.pos
                if p is not None and j >= 0:
                    p.high_close = max(p.high_close, m.c[j])
                    if cfg.use_trailing:
                        p.stop = max(p.stop, p.high_close - cfg.trail_n * p.n)
                    if not np.isnan(m.ex_lo[j]) and m.c[j] < m.ex_lo[j]:
                        m.pending.append(_Order("exit"))
                    elif (not p.no_adds and p.units < cfg.max_units
                          and m.c[j] >= p.last_fill + cfg.add_n * p.n):
                        adds.append((m, j))
                if p is not None:
                    heat += max(last_close[m.ticker] - p.stop, 0.0) * p.shares
                if p is None and j >= 0 and m.liquid[j]:
                    ok, strength = self.entry_signal(m, j)
                    if ok:
                        entries.append((strength, m, j))
            units_hist[i] = sum(m.pos.units for m in mkts if m.pos is not None)
            heat_hist[i] = heat / equity if equity > 0 else np.nan

            if pause_left > 0:
                prev_equity = equity
                continue
            group_units: dict[str, int] = {}
            for m in mkts:
                if m.pos is not None:
                    group_units[m.group] = group_units.get(m.group, 0) + m.pos.units
            total_units = sum(group_units.values())

            def approve(m: _Market, j: int, kind: str) -> _Order | None:
                nonlocal heat, total_units
                n = m.n[j]
                if np.isnan(n) or n <= 0 or not m.liquid[j]:
                    return None
                risk_1 = cfg.stop_n * n
                shares = math.floor(cfg.risk_per_unit * seq / risk_1)
                no_adds = False
                if shares == 0:
                    if kind == "add" or risk_1 > cfg.single_share_max_risk * seq:
                        return None
                    shares, no_adds = 1, True
                if group_units.get(m.group, 0) + 1 > cfg.group_unit_cap:
                    return None
                if total_units + 1 > cfg.total_long_units:
                    return None
                if heat + risk_1 * shares > cfg.heat_cap * equity:
                    return None
                heat += risk_1 * shares
                group_units[m.group] = group_units.get(m.group, 0) + 1
                total_units += 1
                unit_risk = risk_1 * math.floor(cfg.risk_per_unit * seq / risk_1) or risk_1 * shares
                return _Order(kind, shares, n, no_adds, unit_risk)

            for m, j in adds:
                od = approve(m, j, "add")
                if od is not None:
                    m.pending.append(od)
            for _, m, j in sorted(entries, key=lambda e: -e[0]):
                od = approve(m, j, "entry")
                if od is not None:
                    m.pending.append(od)
            prev_equity = equity

        eq = pd.Series(eq_hist, index=cal, name="equity").iloc[i0:]
        daily = pd.DataFrame({"equity": eq_hist, "gross": gross_hist, "gross_leverage": gross_hist / eq_hist,
                              "heat": heat_hist, "units": units_hist, "interest": interest_hist,
                              "sizing_equity": sizing_hist}, index=cal).iloc[i0:]
        open_pos = []
        for m in mkts:
            p = m.pos
            if p is None:
                continue
            can_add = not p.no_adds and p.units < cfg.max_units
            open_pos.append({
                "ticker": m.ticker, "group": m.group, "entry_date": p.entry_date,
                "units": p.units, "shares": p.shares, "avg_entry": p.cost_basis / p.shares,
                "last_close": last_close[m.ticker], "stop": p.stop, "n": p.n,
                "last_fill": p.last_fill, "high_close": p.high_close,
                "add_if_close_at_or_above": p.last_fill + cfg.add_n * p.n if can_add else None,
                "commissions": p.commissions,
                "unrealized_pnl": (last_close[m.ticker] - p.cost_basis / p.shares) * p.shares})
        pending = [{"ticker": m.ticker, "kind": od.kind, "shares": od.shares, "n": od.n,
                    "stop_after_fill": f"fill - {cfg.stop_n:g} x {od.n:.4g}" if od.kind == "entry" else None}
                   for m in mkts for od in m.pending]
        snapshot = []
        for m in mkts:
            j = len(m.dates) - 1
            snapshot.append({
                "ticker": m.ticker, "group": m.group, "last_date": m.dates[j], "close": m.c[j],
                "n": m.n[j], "trend_ok": bool(m.trend_ok[j]),
                "entry_level_next": float(np.max(m.h[max(0, j - cfg.entry_len + 1):j + 1])),
                "exit_level_next": float(np.min(m.l[max(0, j - cfg.exit_len + 1):j + 1])),
                "in_position": m.pos is not None})
        state = {"equity": float(eq.iloc[-1]), "peak": peak, "drawdown": 1 - float(eq.iloc[-1]) / peak,
                 "sizing_equity": float(sizing_hist[-1]), "pause_sessions_left": pause_left,
                 "heat": float(heat_hist[-1]), "gross_leverage": float(gross_hist[-1] / eq_hist[-1]),
                 "cash": cash}
        return PortfolioResult(eq, daily, trades, open_pos, cfg, log, pending, state, snapshot)
