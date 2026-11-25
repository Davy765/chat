"""Daily-bar Turtle simulator for a single instrument (SPY).

One account (cash, equity, drawdown rule, leverage cap) holds one "book" per
system traded: S1, S2, or both. Each book has its own channels, position and
S1 skip-filter shadow trade.

Fill model (all orders are resting STOP orders, like the original Turtles):

* Entry levels (Donchian channel of the previous L days), the protective stop
  and the exit channel are all known BEFORE the bar opens.
* If the bar OPENS beyond a level, the fill is at the open (gap), never at the
  better stop level.
* Otherwise, if the bar's range touches the level, the fill is at the level.
* Slippage (bps) and commission are applied to every fill, always against us.

Information available on day t: OHLC of bars 0..t-1, N of day t-1, equity at
the close of day t-1, the cash rate published by day t-1, and the intraday
path of bar t only through stop levels that were already set before the open.

Daily bars do not tell us the ORDER of the high and the low. Wherever the
order matters we take the pessimistic path:

1. Existing position: gap-exit check at the open; then pyramid adds (high
   side); then the stop / exit-channel check (low side) with the raised stop.
   That is, "price went up, we added, then it reversed and stopped us out".
2. Entry day: after the entry fill, the same bar's low/high is checked against
   the new 2N stop. If touched, we assume we were stopped out the same day.
3. A bar that breaks BOTH the upper and lower entry channel is skipped.
4. No re-entry on the same bar as an exit.
5. With several books, every book manages its open position (exits, adds)
   before any book opens a new one.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .config import TurtleConfig
from .indicators import donchian, wilder_atr


@dataclass
class Trade:
    direction: int
    entry_date: pd.Timestamp
    exit_date: pd.Timestamp
    units: int
    shares: float
    avg_entry: float
    exit_price: float
    pnl: float
    costs: float
    r_multiple: float
    exit_reason: str
    entry_reason: str
    same_bar_stop: bool
    system: str = ""


@dataclass
class _Position:
    direction: int
    n: float                     # N frozen at the first entry (original rule)
    entry_date: pd.Timestamp
    entry_reason: str
    unit_shares: float
    shares: float = 0.0
    cost_basis: float = 0.0      # sum(shares * fill incl. slippage)
    costs: float = 0.0           # slippage + commission paid, $
    commissions: float = 0.0
    units: int = 0
    last_fill: float = 0.0       # raw (pre-slippage) fill of the last unit
    stop: float = 0.0
    risk_dollars: float = 0.0    # 2N * one full unit's shares * point value
    same_bar_stop: bool = False


@dataclass
class _Hypothetical:
    """Single-unit shadow trade used only by the S1 'skip after winner' filter.
    Tracks every 20-day breakout whether or not it was actually taken."""
    direction: int = 0
    entry: float = 0.0
    stop: float = 0.0
    last_was_winner: bool | None = None


@dataclass
class _Book:
    """One system's state inside the shared account."""
    cfg: TurtleConfig
    weight: float
    n: np.ndarray
    ent_hi: np.ndarray
    ent_lo: np.ndarray
    ex_hi: np.ndarray
    ex_lo: np.ndarray
    fs_hi: np.ndarray
    fs_lo: np.ndarray
    pos: _Position | None = None
    hyp: _Hypothetical = field(default_factory=_Hypothetical)
    exited_today: bool = False

    @property
    def name(self) -> str:
        return self.cfg.system

    def ready(self, i: int) -> bool:
        return not (np.isnan(self.n[i - 1]) or np.isnan(self.ent_hi[i]) or np.isnan(self.ex_hi[i])
                    or (self.cfg.uses_failsafe and np.isnan(self.fs_hi[i])))


@dataclass
class BacktestResult:
    equity: pd.Series
    trades: list[Trade]
    daily: pd.DataFrame
    config: TurtleConfig
    open_position: dict | None = None          # first open position (single-system convenience)
    log: list[str] = field(default_factory=list)
    open_positions: list[dict] = field(default_factory=list)

    def trades_frame(self) -> pd.DataFrame:
        return pd.DataFrame([t.__dict__ for t in self.trades])


def align_cash_rate(rate: pd.Series | None, index: pd.DatetimeIndex) -> np.ndarray:
    """Annual cash rate in percent, as KNOWN before each bar opens: the last
    value published on or before the PREVIOUS trading day. Missing history
    before the series starts counts as 0%."""
    if rate is None:
        return np.zeros(len(index))
    r = rate.dropna().sort_index()
    known = r.reindex(r.index.union(index)).ffill().reindex(index)
    return known.shift(1).fillna(0.0).to_numpy(float)


class TurtleBacktester:
    def __init__(self, config: TurtleConfig | None = None):
        self.cfg = (config or TurtleConfig()).resolved()

    # ------------------------------------------------------------------ fills
    def _slip(self, price: float, side: int) -> float:
        """side=+1 buy, -1 sell. Slippage always hurts."""
        return price * (1 + side * self.cfg.slippage_bps / 1e4)

    @staticmethod
    def _buy_stop_fill(level: float, open_: float) -> float:
        return max(level, open_)

    @staticmethod
    def _sell_stop_fill(level: float, open_: float) -> float:
        return min(level, open_)

    # ------------------------------------------------------------- channels
    def _channels(self, df: pd.DataFrame, cfg: TurtleConfig) -> tuple[np.ndarray, ...]:
        """Entry, exit and failsafe channel levels for one system, each known
        before the bar opens. Separate method so research code can substitute
        entry levels (e.g. the random-entry benchmark) without touching the
        fill logic."""
        ent_hi, ent_lo = (s.to_numpy() for s in donchian(df, cfg.entry_len))
        ex_hi, ex_lo = (s.to_numpy() for s in donchian(df, cfg.exit_len))
        if cfg.uses_failsafe:
            fs_hi, fs_lo = (s.to_numpy() for s in donchian(df, cfg.failsafe_len))
        else:
            fs_hi = fs_lo = np.full(len(df), np.nan)
        return ent_hi, ent_lo, ex_hi, ex_lo, fs_hi, fs_lo

    # ---------------------------------------------------- drawdown rule
    def _sizing_equity(self, equity: float, reference: float) -> float:
        """Original rule: every full ``dd_step`` drawdown below the reference
        cuts the notional account used for sizing by ``dd_cut``."""
        cfg = self.cfg
        if not cfg.drawdown_rule or equity >= reference:
            return equity
        k = math.floor((1 - equity / reference) / cfg.dd_step + 1e-12)
        return equity if k == 0 else reference * (1 - cfg.dd_cut) ** k

    # ------------------------------------------------------------------- run
    def run(self, df: pd.DataFrame, cash_rate: pd.Series | None = None) -> BacktestResult:
        """``cash_rate``: optional annual rate in percent (e.g. 3-month T-bill)
        paid on idle equity, i.e. equity not tied up in positions."""
        cfg = self.cfg
        pv = cfg.point_value
        o = df["Open"].to_numpy(float)
        h = df["High"].to_numpy(float)
        l = df["Low"].to_numpy(float)
        c = df["Close"].to_numpy(float)
        dates = df.index
        rate = align_cash_rate(cash_rate, dates)
        n_series = wilder_atr(df, cfg.atr_len).to_numpy()

        books = [_Book(bcfg, w, n_series, *self._channels(df, bcfg)) for bcfg, w in cfg.books()]

        cash = cfg.initial_equity
        trades: list[Trade] = []
        log: list[str] = []
        size = len(df)
        equity = np.full(size, np.nan)
        net_hist, gross_hist = np.zeros(size), np.zeros(size)
        stop_hist = np.full(size, np.nan)
        sizing_hist = np.full(size, np.nan)
        interest_hist = np.zeros(size)
        book_pos = {b.name: np.zeros(size) for b in books}
        prev_equity = cfg.initial_equity
        dd_ref = cfg.initial_equity

        def commission(shares: float) -> float:
            return abs(shares) * cfg.commission_per_share

        def gross_shares() -> float:
            return sum(b.pos.shares for b in books if b.pos is not None)

        def add_unit(b: _Book, raw_px: float, shares: float) -> None:
            nonlocal cash
            p = b.pos
            fill = self._slip(raw_px, p.direction)
            comm = commission(shares)
            cash -= p.direction * shares * fill * pv + comm
            p.shares += shares
            p.cost_basis += shares * fill
            p.costs += shares * abs(fill - raw_px) * pv + comm
            p.commissions += comm
            p.units += 1
            p.last_fill = raw_px
            # Original rule: all units' stops move to 2N from the latest unit.
            p.stop = raw_px - p.direction * b.cfg.stop_n * p.n

        def close_position(b: _Book, i: int, raw_px: float, reason: str) -> None:
            nonlocal cash
            p = b.pos
            fill = self._slip(raw_px, -p.direction)
            comm = commission(p.shares)
            cash += p.direction * p.shares * fill * pv - comm
            p.commissions += comm
            p.costs += p.shares * abs(fill - raw_px) * pv + comm
            avg = p.cost_basis / p.shares
            # Fills already include slippage; subtract commissions once.
            pnl = p.direction * (fill - avg) * p.shares * pv - p.commissions
            trades.append(Trade(
                direction=p.direction, entry_date=p.entry_date, exit_date=dates[i],
                units=p.units, shares=p.shares, avg_entry=avg, exit_price=fill,
                pnl=pnl, costs=p.costs,
                r_multiple=pnl / p.risk_dollars if p.risk_dollars else np.nan,
                exit_reason=reason, entry_reason=p.entry_reason,
                same_bar_stop=p.same_bar_stop, system=b.name,
            ))
            b.pos = None

        def exit_level(b: _Book, i: int) -> tuple[float, str]:
            p = b.pos
            if p.direction > 0:
                ch = b.ex_lo[i]
                return (p.stop, "stop_2N") if p.stop >= ch else (ch, "exit_channel")
            ch = b.ex_hi[i]
            return (p.stop, "stop_2N") if p.stop <= ch else (ch, "exit_channel")

        def pyramid(b: _Book, i: int) -> None:
            p = b.pos
            while p.units < b.cfg.max_units:
                nxt = p.last_fill + p.direction * b.cfg.pyramid_n * p.n
                hit = h[i] >= nxt if p.direction > 0 else l[i] <= nxt
                if not hit:
                    return
                raw = self._buy_stop_fill(nxt, o[i]) if p.direction > 0 else self._sell_stop_fill(nxt, o[i])
                shares = self._cap_shares(p.unit_shares, raw, gross_shares(), prev_equity)
                if shares <= 0:
                    return
                add_unit(b, raw, shares)

        def intrabar_exit(b: _Book, i: int) -> bool:
            lvl, why = exit_level(b, i)
            touched = l[i] <= lvl if b.pos.direction > 0 else h[i] >= lvl
            if touched:
                close_position(b, i, lvl, why)
                return True
            return False

        def manage(b: _Book, i: int) -> None:
            b.exited_today = False
            if b.pos is None:
                return
            lvl, why = exit_level(b, i)
            gapped = o[i] <= lvl if b.pos.direction > 0 else o[i] >= lvl
            if gapped:
                close_position(b, i, o[i], why + "_gap")
                b.exited_today = True
            else:
                pyramid(b, i)
                b.exited_today = intrabar_exit(b, i)

        def shadow_exit(b: _Book, i: int) -> bool:
            hyp = b.hyp
            if b.name != "S1" or hyp.direction == 0:
                return False
            d = hyp.direction
            ch = b.ex_lo[i] if d > 0 else b.ex_hi[i]
            lvl = max(hyp.stop, ch) if d > 0 else min(hyp.stop, ch)
            px = None
            if (o[i] <= lvl) if d > 0 else (o[i] >= lvl):
                px = o[i]
            elif (l[i] <= lvl) if d > 0 else (h[i] >= lvl):
                px = lvl
            if px is None:
                return False
            hyp.last_was_winner = d * (px - hyp.entry) > 0
            hyp.direction = 0
            return True

        def enter(b: _Book, i: int, sizing_equity: float) -> None:
            bc = b.cfg
            n_prev = b.n[i - 1]
            hyp_exited = shadow_exit(b, i)
            long_brk = h[i] > b.ent_hi[i]
            short_brk = l[i] < b.ent_lo[i] and bc.allow_short
            filter_ok = not (bc.system == "S1" and bc.use_skip_filter and b.hyp.last_was_winner)

            if b.pos is None and not b.exited_today:
                cand = []
                if long_brk and filter_ok:
                    cand.append((1, b.ent_hi[i], f"{bc.system}_{bc.entry_len}d"))
                elif bc.uses_failsafe and h[i] > b.fs_hi[i]:
                    cand.append((1, b.fs_hi[i], f"failsafe_{bc.failsafe_len}d"))
                if short_brk and filter_ok:
                    cand.append((-1, b.ent_lo[i], f"{bc.system}_{bc.entry_len}d"))
                elif bc.uses_failsafe and bc.allow_short and l[i] < b.fs_lo[i]:
                    cand.append((-1, b.fs_lo[i], f"failsafe_{bc.failsafe_len}d"))

                if len(cand) == 2:
                    log.append(f"{dates[i].date()} {b.name}: bar broke both channels, skipped (ambiguous)")
                elif len(cand) == 1:
                    d, level, reason = cand[0]
                    raw = self._buy_stop_fill(level, o[i]) if d > 0 else self._sell_stop_fill(level, o[i])
                    unit = self._unit_shares(sizing_equity * b.weight, n_prev)
                    shares = self._cap_shares(unit, raw, gross_shares(), prev_equity)
                    if shares > 0:
                        b.pos = _Position(direction=d, n=n_prev, entry_date=dates[i],
                                          entry_reason=reason, unit_shares=unit)
                        add_unit(b, raw, shares)
                        # 1R = 2N risk of one FULL unit, even if the leverage
                        # cap clipped the first fill (later units can be full).
                        b.pos.risk_dollars = bc.stop_n * n_prev * unit * pv
                        pyramid(b, i)
                        if intrabar_exit(b, i):
                            trades[-1].same_bar_stop = True
                            log.append(f"{dates[i].date()} {b.name}: entry and stop on the same bar "
                                       f"(assumed stopped out, pessimistic)")
                    else:
                        log.append(f"{dates[i].date()} {b.name}: signal skipped, size rounds to 0 shares")

            # shadow trade entry (every S1 breakout, taken or not)
            if b.name == "S1" and b.hyp.direction == 0 and not hyp_exited and long_brk != short_brk:
                d = 1 if long_brk else -1
                level = b.ent_hi[i] if d > 0 else b.ent_lo[i]
                px = self._buy_stop_fill(level, o[i]) if d > 0 else self._sell_stop_fill(level, o[i])
                b.hyp.direction, b.hyp.entry = d, px
                b.hyp.stop = px - d * bc.stop_n * n_prev
                if (l[i] <= b.hyp.stop) if d > 0 else (h[i] >= b.hyp.stop):
                    b.hyp.last_was_winner = False
                    b.hyp.direction = 0

        for i in range(1, size):
            # ---- before the open: interest and sizing basis -----------------
            if rate[i]:
                idle = max(prev_equity - gross_shares() * c[i - 1] * pv, 0.0)
                accrued = idle * rate[i] / 100 * (dates[i] - dates[i - 1]).days / 365
                cash += accrued
                interest_hist[i] = accrued
            if cfg.dd_reference == "year_start" and dates[i].year != dates[i - 1].year:
                dd_ref = prev_equity
            elif cfg.dd_reference == "peak":
                dd_ref = max(dd_ref, prev_equity)
            sizing = self._sizing_equity(prev_equity, dd_ref)
            sizing_hist[i] = sizing

            # ---- during the bar --------------------------------------------
            for b in books:
                manage(b, i)
            for b in books:
                if b.ready(i):
                    enter(b, i, sizing)

            # ---- at the close ----------------------------------------------
            net = sum(b.pos.direction * b.pos.shares for b in books if b.pos is not None)
            net_hist[i] = net
            for b in books:
                if b.pos is not None:
                    book_pos[b.name][i] = b.pos.direction * b.pos.shares
            gross_hist[i] = gross_shares()
            if books[0].pos is not None:
                stop_hist[i] = books[0].pos.stop
            equity[i] = cash + net * c[i] * pv
            prev_equity = equity[i]

        equity[0] = cfg.initial_equity
        eq = pd.Series(equity, index=dates, name="equity")
        b0 = books[0]
        daily = pd.DataFrame({"equity": eq, "position": net_hist, "gross_position": gross_hist,
                              "stop": stop_hist, "sizing_equity": sizing_hist,
                              "interest": interest_hist, "N": n_series,
                              "entry_hi": b0.ent_hi, "entry_lo": b0.ent_lo,
                              "exit_hi": b0.ex_hi, "exit_lo": b0.ex_lo,
                              **{f"pos_{k}": v for k, v in book_pos.items()}}, index=dates)
        open_positions = []
        for b in books:
            p = b.pos
            if p is not None:
                avg = p.cost_basis / p.shares
                open_positions.append({
                    "system": b.name, "direction": p.direction, "entry_date": p.entry_date,
                    "units": p.units, "shares": p.shares, "avg_entry": avg, "stop": p.stop,
                    "unrealized_pnl": p.direction * (c[-1] - avg) * p.shares * pv})
        return BacktestResult(eq, trades, daily, cfg,
                              open_positions[0] if open_positions else None, log, open_positions)

    # --------------------------------------------------------------- sizing
    def _unit_shares(self, equity: float, n: float) -> float:
        """Original rule: 1 unit = (1% of equity) / (N * $ per point)."""
        raw = self.cfg.risk_per_unit * equity / (n * self.cfg.point_value)
        return raw if self.cfg.allow_fractional else math.floor(raw)

    def _cap_shares(self, want: float, price: float, held: float, equity: float) -> float:
        """``held``: gross shares already held by ALL systems in the account."""
        room = self.cfg.max_leverage * equity / (price * self.cfg.point_value) - held
        shares = min(want, max(room, 0.0))
        return shares if self.cfg.allow_fractional else math.floor(shares)
