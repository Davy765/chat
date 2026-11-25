"""Backtest configuration.

Every default below is either the original Turtle rule (as documented by
Curtis Faith, "Way of the Turtle", and the public "Original Turtle Trading
Rules") or an explicitly documented deviation. Deviations are marked
``DEVIATION`` with the reason.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TurtleConfig:
    # --- System selection -------------------------------------------------
    # "S1": 20-day breakout entry, 10-day exit, with the "skip after a winner"
    #       filter and the 55-day failsafe breakout.
    # "S2": 55-day breakout entry, 20-day exit, no filter.
    system: str = "S1"

    # Channel lengths. Left configurable, but the defaults are set from
    # ``system`` in ``resolved()`` so S1/S2 can't be mixed up by accident.
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
    max_units: int = 4              # max units per market (original rule)
    pyramid_n: float = 0.5          # add a unit every 1/2 N (original rule)

    # DEVIATION: the Turtles traded leveraged futures. On SPY shares, one
    # 1%-risk unit is roughly 0.01 / (N / price) ~ 70-100% of equity in
    # notional, so 4 units would need ~3-4x leverage. We cap gross notional
    # at ``max_leverage`` x equity. Set it to a large value to disable the cap.
    max_leverage: float = 1.0
    allow_short: bool = True        # shorts on SPY now; PUTs later
    allow_fractional: bool = False  # whole shares only

    # --- Costs (DEVIATION from a frictionless backtest; never set both to 0
    # when you report results) --------------------------------------------
    slippage_bps: float = 2.0       # applied against us on every fill
    commission_per_share: float = 0.0
    initial_equity: float = 100_000.0

    def resolved(self) -> "TurtleConfig":
        if self.system not in ("S1", "S2"):
            raise ValueError(f"system must be 'S1' or 'S2', got {self.system!r}")
        entry = self.entry_len or (20 if self.system == "S1" else 55)
        exit_ = self.exit_len or (10 if self.system == "S1" else 20)
        if exit_ >= entry:
            raise ValueError("exit channel must be shorter than entry channel")
        from dataclasses import replace

        return replace(self, entry_len=entry, exit_len=exit_)

    @property
    def uses_failsafe(self) -> bool:
        return self.system == "S1" and self.use_skip_filter
