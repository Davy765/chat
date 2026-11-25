"""Daily-bar Turtle simulator for a single instrument (SPY).

Fill model (all orders are resting STOP orders, like the original Turtles):

* Entry levels (Donchian channel of the previous L days), the protective stop
  and the exit channel are all known BEFORE the bar opens.
* If the bar OPENS beyond a level, the fill is at the open (gap), never at the
  better stop level.
* Otherwise, if the bar's range touches the level, the fill is at the level.
* Slippage (bps) and commission are applied to every fill, always against us.

Information available on day t: OHLC of bars 0..t-1, N of day t-1, equity at
the close of day t-1, and the intraday path of bar t only through stop levels
that were already set before the open.

Daily bars do not tell us the ORDER of the high and the low. Wherever the
order matters we take the pessimistic path:

1. Existing position: gap-exit check at the open; then pyramid adds (high
   side); then the stop / exit-channel check (low side) with the raised stop.
   That is, "price went up, we added, then it reversed and stopped us out".
2. Entry day: after the entry fill, the same bar's low/high is checked against
   the new 2N stop. If touched, we assume we were stopped out the same day.
3. A bar that breaks BOTH the upper and lower entry channel is skipped.
4. No re-entry on the same bar as an exit.
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
    risk_dollars: float = 0.0    # 2N * first-unit shares * point value
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
class BacktestResult:
    equity: pd.Series
    trades: list[Trade]
    daily: pd.DataFrame
    config: TurtleConfig
    open_position: dict | None = None
    log: list[str] = field(default_factory=list)

    def trades_frame(self) -> pd.DataFrame:
        return pd.DataFrame([t.__dict__ for t in self.trades])


class TurtleBacktester:
    def __init__(self, config: TurtleConfig | None = None):
        self.cfg = (config or TurtleConfig()).resolved()

    # ------------------------------------------------------------------ fills
    def _slip(self, price: float, side: int) -> float:
        """side=+1 buy, -1 sell. Slippage always hurts."""
        return price * (1 + side * self.cfg.slippage_bps / 1e4)

    def _buy_stop_fill(self, level: float, open_: float) -> float:
        return max(level, open_)

    def _sell_stop_fill(self, level: float, open_: float) -> float:
        return min(level, open_)

    # ------------------------------------------------------------------- run
    def run(self, df: pd.DataFrame) -> BacktestResult:
        cfg = self.cfg
        pv = cfg.point_value
        o = df["Open"].to_numpy(float)
        h = df["High"].to_numpy(float)
        l = df["Low"].to_numpy(float)
        c = df["Close"].to_numpy(float)
        dates = df.index

        n_series = wilder_atr(df, cfg.atr_len).to_numpy()
        ent_hi, ent_lo, ex_hi, ex_lo, fs_hi, fs_lo = self._channels(df)

        cash = cfg.initial_equity
        pos: _Position | None = None
        hyp = _Hypothetical()
        trades: list[Trade] = []
        log: list[str] = []
        equity = np.full(len(df), np.nan)
        qty_hist = np.zeros(len(df))
        stop_hist = np.full(len(df), np.nan)
        prev_equity = cfg.initial_equity

        def commission(shares: float) -> float:
            return abs(shares) * cfg.commission_per_share

        def add_unit(p: _Position, raw_px: float, shares: float) -> None:
            nonlocal cash
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
            p.stop = raw_px - p.direction * cfg.stop_n * p.n

        def close_position(p: _Position, i: int, raw_px: float, reason: str) -> None:
            nonlocal cash, pos
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
                same_bar_stop=p.same_bar_stop,
            ))
            pos = None

        def exit_level(p: _Position, i: int) -> tuple[float, str]:
            if p.direction > 0:
                ch = ex_lo[i]
                return (p.stop, "stop_2N") if p.stop >= ch else (ch, "exit_channel")
            ch = ex_hi[i]
            return (p.stop, "stop_2N") if p.stop <= ch else (ch, "exit_channel")

        def pyramid(p: _Position, i: int, equity_ref: float) -> None:
            while p.units < cfg.max_units:
                nxt = p.last_fill + p.direction * cfg.pyramid_n * p.n
                hit = h[i] >= nxt if p.direction > 0 else l[i] <= nxt
                if not hit:
                    return
                raw = self._buy_stop_fill(nxt, o[i]) if p.direction > 0 else self._sell_stop_fill(nxt, o[i])
                shares = self._cap_shares(p.unit_shares, raw, p.shares, equity_ref)
                if shares <= 0:
                    return
                add_unit(p, raw, shares)

        def intrabar_exit(p: _Position, i: int) -> bool:
            lvl, why = exit_level(p, i)
            touched = l[i] <= lvl if p.direction > 0 else h[i] >= lvl
            if touched:
                close_position(p, i, lvl, why)
                return True
            return False

        for i in range(1, len(df)):
            n_prev = n_series[i - 1]
            ready = not (np.isnan(n_prev) or np.isnan(ent_hi[i]) or np.isnan(ex_hi[i])
                         or (cfg.uses_failsafe and np.isnan(fs_hi[i])))
            exited_today = False

            # ---- 1) manage the open position -------------------------------
            if pos is not None:
                lvl, why = exit_level(pos, i)
                gapped = o[i] <= lvl if pos.direction > 0 else o[i] >= lvl
                if gapped:
                    close_position(pos, i, o[i], why + "_gap")
                    exited_today = True
                else:
                    pyramid(pos, i, prev_equity)
                    exited_today = intrabar_exit(pos, i)

            # ---- 2) shadow trade for the S1 skip filter ---------------------
            hyp_exited = False
            if ready and cfg.system == "S1" and hyp.direction != 0:
                d = hyp.direction
                ch = ex_lo[i] if d > 0 else ex_hi[i]
                lvl = max(hyp.stop, ch) if d > 0 else min(hyp.stop, ch)
                px = None
                if (o[i] <= lvl) if d > 0 else (o[i] >= lvl):
                    px = o[i]
                elif (l[i] <= lvl) if d > 0 else (h[i] >= lvl):
                    px = lvl
                if px is not None:
                    hyp.last_was_winner = d * (px - hyp.entry) > 0
                    hyp.direction = 0
                    hyp_exited = True

            if not ready:
                equity[i] = cash + (pos.direction * pos.shares * c[i] * pv if pos else 0.0)
                prev_equity = equity[i]
                continue

            long_brk = h[i] > ent_hi[i]
            short_brk = l[i] < ent_lo[i] and cfg.allow_short
            filter_ok = not (cfg.system == "S1" and cfg.use_skip_filter and hyp.last_was_winner)

            # ---- 3) new entry --------------------------------------------
            if pos is None and not exited_today:
                cand = []
                if long_brk and filter_ok:
                    cand.append((1, ent_hi[i], f"{cfg.system}_{cfg.entry_len}d"))
                elif cfg.uses_failsafe and h[i] > fs_hi[i]:
                    cand.append((1, fs_hi[i], f"failsafe_{cfg.failsafe_len}d"))
                if short_brk and filter_ok:
                    cand.append((-1, ent_lo[i], f"{cfg.system}_{cfg.entry_len}d"))
                elif cfg.uses_failsafe and cfg.allow_short and l[i] < fs_lo[i]:
                    cand.append((-1, fs_lo[i], f"failsafe_{cfg.failsafe_len}d"))

                if len(cand) == 2:
                    log.append(f"{dates[i].date()}: bar broke both channels, skipped (ambiguous)")
                elif len(cand) == 1:
                    d, level, reason = cand[0]
                    raw = self._buy_stop_fill(level, o[i]) if d > 0 else self._sell_stop_fill(level, o[i])
                    unit = self._unit_shares(prev_equity, n_prev)
                    shares = self._cap_shares(unit, raw, 0.0, prev_equity)
                    if shares > 0:
                        pos = _Position(direction=d, n=n_prev, entry_date=dates[i],
                                        entry_reason=reason, unit_shares=unit)
                        add_unit(pos, raw, shares)
                        pos.risk_dollars = cfg.stop_n * n_prev * shares * pv
                        pyramid(pos, i, prev_equity)
                        if intrabar_exit(pos, i):
                            trades[-1].same_bar_stop = True
                            log.append(f"{dates[i].date()}: entry and stop on the same bar "
                                       f"(assumed stopped out, pessimistic)")
                    else:
                        log.append(f"{dates[i].date()}: signal skipped, size rounds to 0 shares")

            # ---- 4) shadow trade entry (every 20-day breakout) --------------
            if cfg.system == "S1" and hyp.direction == 0 and not hyp_exited:
                if long_brk != short_brk:
                    d = 1 if long_brk else -1
                    level = ent_hi[i] if d > 0 else ent_lo[i]
                    px = self._buy_stop_fill(level, o[i]) if d > 0 else self._sell_stop_fill(level, o[i])
                    hyp.direction, hyp.entry = d, px
                    hyp.stop = px - d * cfg.stop_n * n_prev
                    if (l[i] <= hyp.stop) if d > 0 else (h[i] >= hyp.stop):
                        hyp.last_was_winner = False
                        hyp.direction = 0

            qty_hist[i] = pos.direction * pos.shares if pos else 0.0
            stop_hist[i] = pos.stop if pos else np.nan
            equity[i] = cash + (pos.direction * pos.shares * c[i] * pv if pos else 0.0)
            prev_equity = equity[i]

        equity[0] = cfg.initial_equity
        eq = pd.Series(equity, index=dates, name="equity")
        daily = pd.DataFrame({"equity": eq, "position": qty_hist, "stop": stop_hist,
                              "N": n_series, "entry_hi": ent_hi, "entry_lo": ent_lo,
                              "exit_hi": ex_hi, "exit_lo": ex_lo}, index=dates)
        open_pos = None
        if pos is not None:
            open_pos = {"direction": pos.direction, "entry_date": pos.entry_date,
                        "units": pos.units, "shares": pos.shares,
                        "avg_entry": pos.cost_basis / pos.shares, "stop": pos.stop,
                        "unrealized_pnl": pos.direction * (c[-1] - pos.cost_basis / pos.shares)
                        * pos.shares * pv}
        return BacktestResult(eq, trades, daily, cfg, open_pos, log)

    # ------------------------------------------------------------- channels
    def _channels(self, df: pd.DataFrame) -> tuple[np.ndarray, ...]:
        """Entry, exit and failsafe channel levels, each known before the bar
        opens. Separate method so research code can substitute entry levels
        (e.g. the random-entry benchmark) without touching the fill logic."""
        cfg = self.cfg
        ent_hi, ent_lo = (s.to_numpy() for s in donchian(df, cfg.entry_len))
        ex_hi, ex_lo = (s.to_numpy() for s in donchian(df, cfg.exit_len))
        if cfg.uses_failsafe:
            fs_hi, fs_lo = (s.to_numpy() for s in donchian(df, cfg.failsafe_len))
        else:
            fs_hi = fs_lo = np.full(len(df), np.nan)
        return ent_hi, ent_lo, ex_hi, ex_lo, fs_hi, fs_lo

    # --------------------------------------------------------------- sizing
    def _unit_shares(self, equity: float, n: float) -> float:
        """Original rule: 1 unit = (1% of equity) / (N * $ per point)."""
        raw = self.cfg.risk_per_unit * equity / (n * self.cfg.point_value)
        return raw if self.cfg.allow_fractional else math.floor(raw)

    def _cap_shares(self, want: float, price: float, held: float, equity: float) -> float:
        room = self.cfg.max_leverage * equity / (price * self.cfg.point_value) - held
        shares = min(want, max(room, 0.0))
        return shares if self.cfg.allow_fractional else math.floor(shares)
