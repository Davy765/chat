"""Step 1 comparison: effect of each original-rule addition on SPY.

  - drawdown sizing rule (3 reference definitions)
  - interest on idle cash (3-month T-bill, FRED DTB3)
  - S1 + S2 traded together in one account

    python scripts/step1_compare.py data/SPY.csv data/DTB3.csv
Writes reports/step1.md and reports/step1_*.csv.
"""

from __future__ import annotations

import pathlib
import sys

import numpy as np
import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from robustness import BLOCKS, md  # noqa: E402
from turtle_bot import TurtleBacktester, TurtleConfig  # noqa: E402
from turtle_bot.backtest import align_cash_rate  # noqa: E402
from turtle_bot.data import load_csv, load_rate_csv  # noqa: E402
from turtle_bot.metrics import max_drawdown  # noqa: E402

TD = 252
START = "1994"

V1 = dict(drawdown_rule=False)
VARIANTS = [
    # label,                                   config,                                        interest
    ("S1 v1 (no DD rule, no interest)",        dict(system="S1", **V1),                       False),
    ("S1 + DD rule (year_start)",              dict(system="S1"),                             False),
    ("S1 + DD rule (initial)",                 dict(system="S1", dd_reference="initial"),     False),
    ("S1 + DD rule (peak)",                    dict(system="S1", dd_reference="peak"),        False),
    ("S1 + interest",                          dict(system="S1", **V1),                       True),
    ("S1 complete",                            dict(system="S1"),                             True),
    ("S2 v1 (no DD rule, no interest)",        dict(system="S2", **V1),                       False),
    ("S2 complete",                            dict(system="S2"),                             True),
    ("S1+S2 v1 (no DD rule, no interest)",     dict(system="S1+S2", **V1),                    False),
    ("S1+S2 complete",                         dict(system="S1+S2"),                          True),
]


def stats(eq: pd.Series, rf_daily: pd.Series, trades: int | None = None) -> dict:
    eq = eq.loc[START:]
    r = eq.pct_change().dropna()
    ex = r - rf_daily.reindex(r.index).fillna(0)
    yrs = (eq.index[-1] - eq.index[0]).days / 365.25
    cagr = (eq.iloc[-1] / eq.iloc[0]) ** (1 / yrs) - 1
    dd = max_drawdown(eq)
    yearly = eq.resample("YE").last().pct_change().dropna()
    return {"cagr_%": 100 * cagr, "max_dd_%": 100 * dd,
            "MAR": cagr / abs(dd) if dd else np.nan,
            "sharpe": r.mean() / r.std() * np.sqrt(TD),
            "excess_sharpe": ex.mean() / ex.std() * np.sqrt(TD),
            "worst_year_%": 100 * yearly.min(),
            "trades": trades if trades is not None else 0}


def main() -> None:
    spy = sys.argv[1] if len(sys.argv) > 1 else str(ROOT / "data" / "SPY.csv")
    rates = sys.argv[2] if len(sys.argv) > 2 else str(ROOT / "data" / "DTB3.csv")
    df = load_csv(spy)
    rate = load_rate_csv(rates)
    # daily T-bill return actually earnable (same timing as the engine)
    known = pd.Series(align_cash_rate(rate, df.index), index=df.index)
    days = pd.Series(df.index, index=df.index).diff().dt.days.fillna(0)
    rf_daily = known / 100 * days / 365

    out = ROOT / "reports"
    out.mkdir(exist_ok=True)
    rows, blocks, curves = [], [], {}
    for short in (True, False):
        for label, kw, interest in VARIANTS:
            res = TurtleBacktester(TurtleConfig(allow_short=short, **kw)).run(
                df, cash_rate=rate if interest else None)
            tf = res.trades_frame()
            n = int((tf.entry_date >= START).sum()) if len(tf) else 0
            s = stats(res.equity, rf_daily, n)
            s["interest_share_of_profit_%"] = (
                100 * res.daily["interest"].loc[START:].sum() / (res.equity.iloc[-1] - res.equity.loc[START:].iloc[0])
                if interest else 0.0)
            rows.append({"shorts": short, "variant": label, **s})
            curves[(short, label)] = res.equity

    bh = df["Close"]
    rows.append({"shorts": "-", "variant": "SPY buy&hold", **stats(bh, rf_daily), "interest_share_of_profit_%": 0.0})
    table = pd.DataFrame(rows)

    for short in (True, False):
        for label in ("S1 complete", "S2 complete", "S1+S2 complete"):
            eq = curves[(short, label)]
            for name, a, b in BLOCKS:
                if name == "1993-1999":
                    a = START
                seg = eq.loc[a:b]
                yrs = (seg.index[-1] - seg.index[0]).days / 365.25
                blocks.append({"shorts": short, "variant": label, "period": name,
                               "cagr_%": 100 * ((seg.iloc[-1] / seg.iloc[0]) ** (1 / yrs) - 1),
                               "max_dd_%": 100 * max_drawdown(seg)})
    for name, a, b in BLOCKS:
        seg = bh.loc[(START if name == "1993-1999" else a):b]
        yrs = (seg.index[-1] - seg.index[0]).days / 365.25
        blocks.append({"shorts": "-", "variant": "SPY buy&hold", "period": name,
                       "cagr_%": 100 * ((seg.iloc[-1] / seg.iloc[0]) ** (1 / yrs) - 1),
                       "max_dd_%": 100 * max_drawdown(seg)})
    blocks = pd.DataFrame(blocks)
    block_pivot = blocks.assign(key=blocks.variant + " / shorts=" + blocks.shorts.astype(str)).pivot(
        index="key", columns="period", values="cagr_%").reset_index()

    weights = []
    for w in (0.25, 0.5, 0.75):
        res = TurtleBacktester(TurtleConfig(system="S1+S2", allow_short=False, s1_weight=w)).run(df, cash_rate=rate)
        weights.append({"s1_weight": w, **stats(res.equity, rf_daily, len(res.trades))})
    weights = pd.DataFrame(weights)

    table.to_csv(out / "step1_variants.csv", index=False)
    blocks.to_csv(out / "step1_subperiods.csv", index=False)
    weights.to_csv(out / "step1_weights.csv", index=False)
    report = [
        "# Step 1 — complete original rules (generated by scripts/step1_compare.py)\n",
        f"SPY {df.index[0].date()} → {df.index[-1].date()}, stats from {START}. "
        "1% risk/unit, 4 units per system, 2N stop, 1x gross cap, 2 bp slippage. "
        "`excess_sharpe` = Sharpe of returns above the T-bill rate.\n",
        "## Variants — long + short\n", md(table[table.shorts.isin([True, "-"])].drop(columns="shorts").round(2)),
        "\n## Variants — long-only\n", md(table[table.shorts.isin([False, "-"])].drop(columns="shorts").round(2)),
        "\n## Sub-period CAGR % (complete rules)\n", md(block_pivot.round(2)),
        "\n## S1/S2 split (S1+S2 complete, long-only)\n", md(weights.round(2)),
    ]
    (out / "step1.md").write_text("\n".join(report) + "\n")
    print((out / "step1.md").read_text())


if __name__ == "__main__":
    main()
