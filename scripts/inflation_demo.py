"""Quantify how common first-draft backtest bugs inflate Turtle results on SPY.

A deliberately simple single-unit Turtle (20/10 breakout, 2N stop, long and
short, 1% risk, leverage cap 1x) is run with each bug switched on one at a
time. Compare every row with "correct".

    python scripts/inflation_demo.py data/SPY.csv
"""

from __future__ import annotations

import math
import pathlib
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from turtle_bot.data import load_csv  # noqa: E402
from turtle_bot.indicators import true_range, wilder_atr  # noqa: E402
from turtle_bot.metrics import max_drawdown  # noqa: E402

BUGS = {
    "correct": {},
    "channel_includes_today": {"no_shift": True},
    "close_confirmed_fill_at_level": {"close_confirm": True},
    "atr_includes_today": {"atr_today": True},
    "simple_atr_not_wilder": {"sma_atr": True},
    "stop_fills_ignore_gaps": {"no_gap": True},
    "optimistic_intrabar_order": {"optimistic": True},
    "no_costs": {"no_costs": True},
    "ALL_BUGS": {"close_confirm": True, "atr_today": True, "no_gap": True,
                 "optimistic": True, "no_costs": True},
}


def run(df: pd.DataFrame, entry_len=20, exit_len=10, slip_bps=2.0, **bug) -> pd.Series:
    o, h, l, c = (df[k].to_numpy(float) for k in ("Open", "High", "Low", "Close"))
    if bug.get("sma_atr"):
        n = true_range(df).rolling(20).mean().to_numpy()
    else:
        n = wilder_atr(df, 20).to_numpy()
    shift = 0 if bug.get("no_shift") else 1
    # With no shift, "High > max(High incl. today)" can never be true, so the
    # typical buggy version compares Close with the unshifted channel of Highs
    # of the PREVIOUS bars plus today, and fills at the channel.
    up = df["High"].rolling(entry_len).max().shift(shift).to_numpy()
    dn = df["Low"].rolling(entry_len).min().shift(shift).to_numpy()
    xup = df["High"].rolling(exit_len).max().shift(1).to_numpy()
    xdn = df["Low"].rolling(exit_len).min().shift(1).to_numpy()
    s = 0.0 if bug.get("no_costs") else slip_bps / 1e4

    cash, d, sh, entry, stop = 100_000.0, 0, 0, 0.0, 0.0
    eq = np.full(len(df), 100_000.0)
    for i in range(1, len(df)):
        ni = n[i] if bug.get("atr_today") else n[i - 1]
        if np.isnan(ni) or np.isnan(up[i]) or np.isnan(xup[i]):
            eq[i] = cash
            continue
        exited = False
        if d:
            lvl = max(stop, xdn[i]) if d > 0 else min(stop, xup[i])
            gap = (o[i] <= lvl) if d > 0 else (o[i] >= lvl)
            hit = (l[i] <= lvl) if d > 0 else (h[i] >= lvl)
            if gap and not bug.get("no_gap"):
                px = o[i]
            elif hit or gap:
                px = lvl
            else:
                px = None
            if px is not None:
                cash += d * sh * px * (1 - d * s)
                d, exited = 0, True
        if not d and not exited:
            if bug.get("close_confirm") or bug.get("no_shift"):
                lg, st = c[i] > up[i], c[i] < dn[i]
            else:
                lg, st = h[i] > up[i], l[i] < dn[i]
            if lg != st:
                d = 1 if lg else -1
                lvl = up[i] if lg else dn[i]
                px = lvl if (bug.get("close_confirm") or bug.get("no_shift")) else (
                    max(lvl, o[i]) if lg else min(lvl, o[i]))
                eq_prev = eq[i - 1]
                sh = min(math.floor(0.01 * eq_prev / ni), math.floor(eq_prev / px))
                if sh <= 0:
                    d = 0
                else:
                    fill = px * (1 + d * s)
                    cash -= d * sh * fill
                    entry, stop = px, px - d * 2 * ni
                    hit = (l[i] <= stop) if d > 0 else (h[i] >= stop)
                    if hit and not bug.get("optimistic"):
                        cash += d * sh * stop * (1 - d * s)
                        d = 0
        eq[i] = cash + (d * sh * c[i] if d else 0.0)
    return pd.Series(eq, index=df.index)


def main() -> None:
    path = sys.argv[1] if len(sys.argv) > 1 else "data/SPY.csv"
    df = load_csv(path).loc["2000":]
    rows = []
    for name, bug in BUGS.items():
        eq = run(df, **bug)
        yrs = (eq.index[-1] - eq.index[0]).days / 365.25
        rows.append({"variant": name,
                     "total_return_%": round(100 * (eq.iloc[-1] / eq.iloc[0] - 1), 1),
                     "cagr_%": round(100 * ((eq.iloc[-1] / eq.iloc[0]) ** (1 / yrs) - 1), 2),
                     "max_dd_%": round(100 * max_drawdown(eq), 1)})
    print(pd.DataFrame(rows).to_string(index=False))


if __name__ == "__main__":
    main()
