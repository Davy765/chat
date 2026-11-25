"""Backtest configuration.

Every default below is either the original Turtle rule (as documented by
Curtis Faith, "Way of the Turtle", and the public "Original Turtle Trading
Rules") or an explicitly documented deviation. Deviations are marked
``DEVIATION`` with the reason.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

SYSTEMS = ("S1", "S2", "S1+S2")


@dataclass(frozen=True)
class TurtleConfig:
    # --- System selection -------------------------------------------------
    # "S1":    20-day breakout entry, 10-day exit, with the "skip after a
    #          winner" filter and the 55-day failsafe breakout.
    # "S2":    55-day breakout entry, 20-day exit, no filter.
    # "S1+S2": both systems in ONE account (shared cash, equity, drawdown rule
    #          and leverage cap), as the Turtles traded them. Each system's
    #          unit is sized on its share of the account (``s1_weight``).
    system: str = "S1"
    s1_weight: float = 0.5          # S1+S2 only; S2 gets 1 - s1_weight

    # Channel lengths. Left configurable for single-system research runs; the
    # defaults come from ``system`` so S1/S2 can't be mixed up by accident.
    entry_len: int | None = None
    exit_len: int | None = None
    failsafe_len: int = 55          # S1 only
    use_skip_filter: bool = True    # S1 only

    # --- Volatility (N) and stops -----------------------------------------
    atr_len: int = 20               # N = 20-day Wilder ATR (original rule)
    stop_n: float = 2.0             # protective stop at 2N (original rule)

    # --- Position sizing ---------------------------------------------------
    risk_per_unit: float = 0.01     # 1 unit = 1% of equity per 1N move
    point_value: float = 1.0        # $ per 1.00 price move per share (SPY = 1)
    max_units: int = 4              # max units per market per system
    pyramid_n: float = 0.5          # add a unit every 1/2 N (original rule)

    # Original rule ("Adjusting trading size"): for every 10% drawdown from
    # the reference account, size trades on a notional account 20% smaller.
    #   reference = "year_start": equity at the last close of the previous
    #               year (reset every January), "initial": starting equity,
    #               "peak": running high-water mark.
    # The published rules say the notional account is cut per 10% drawdown
    # "from the starting account"; how often that start was reset is not
    # spelled out, so the reference is configurable. Default: year_start.
    drawdown_rule: bool = True
    dd_step: float = 0.10
    dd_cut: float = 0.20
    dd_reference: str = "year_start"

    # DEVIATION: the Turtles traded leveraged futures. On SPY shares, one
    # 1%-risk unit is roughly 0.01 / (N / price) ~ 70-100% of equity in
    # notional, so 4 units would need ~3-4x leverage. We cap gross notional
    # (all systems combined) at ``max_leverage`` x equity.
    max_leverage: float = 1.0
    allow_short: bool = True        # shorts on SPY now; PUTs later
    allow_fractional: bool = False  # whole shares only

    # --- Costs (DEVIATION from a frictionless backtest; never set both to 0
    # when you report results) --------------------------------------------
    slippage_bps: float = 2.0       # applied against us on every fill
    commission_per_share: float = 0.0
    initial_equity: float = 100_000.0

    def resolved(self) -> "TurtleConfig":
        if self.system not in SYSTEMS:
            raise ValueError(f"system must be one of {SYSTEMS}, got {self.system!r}")
        if self.dd_reference not in ("year_start", "initial", "peak"):
            raise ValueError(f"unknown dd_reference {self.dd_reference!r}")
        if self.system == "S1+S2":
            if self.entry_len or self.exit_len:
                raise ValueError("custom channel lengths are for single-system runs only")
            if not 0 < self.s1_weight < 1:
                raise ValueError("s1_weight must be strictly between 0 and 1")
            return self
        entry = self.entry_len or (20 if self.system == "S1" else 55)
        exit_ = self.exit_len or (10 if self.system == "S1" else 20)
        if exit_ >= entry:
            raise ValueError("exit channel must be shorter than entry channel")
        return replace(self, entry_len=entry, exit_len=exit_)

    def books(self) -> list[tuple["TurtleConfig", float]]:
        """(single-system config, share of account) for each system traded."""
        cfg = self.resolved()
        if cfg.system != "S1+S2":
            return [(cfg, 1.0)]
        return [(replace(cfg, system="S1").resolved(), cfg.s1_weight),
                (replace(cfg, system="S2").resolved(), 1 - cfg.s1_weight)]

    @property
    def uses_failsafe(self) -> bool:
        return self.system == "S1" and self.use_skip_filter
