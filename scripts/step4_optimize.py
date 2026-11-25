"""Step 4: can the Modern Turtle rules be improved without overfitting?

PRE-REGISTERED PROCEDURE (fixed and committed before the first run)

  Universe, costs, account: as in step 3 (14 ETFs, long/flat, cash account,
  2 bp slippage, T-bill interest).

  Selection sample: 2008-01-02 .. 2017-12-29. Data after 2017 is cut off
  before these runs, so it cannot influence the choice.
  Hold-out sample: 2018-01-02 .. end of the snapshot, run once with fresh
  capital (indicators may use earlier history, as live trading would).

  Candidates (a small set, each aimed at a problem already measured):
    C0  rulebook as written (baseline)
    C1  + cap each market at 20% of equity   (one quiet fund absorbing the cash)
    C2  + cap each market at 33% of equity
    C3  ensemble: 40-, 55- and 80-day breakouts, 1/3 of capital each
        (spread parameter risk instead of picking one breakout length)
    C4  C3 + 20% cap
    C5  original Turtle S2 long-only, one sub-account per ETF (simpler rules)

  Selection: the candidate with the highest excess Sharpe on the selection
  sample. If it is within 0.02 of C0 there, C0 is kept (no real gain).
  Adoption: the selected candidate replaces C0 only if, on the hold-out,
  its excess Sharpe is at least 0.05 higher than C0's. Otherwise C0 stays.

  Hold-out results of every candidate are reported for transparency, but
  only the pre-selected one can be adopted.

Caveat stated up front: 2018-2026 is unused for selection, not unseen.
Step 3's full-period results (which include it) were already looked at,
and the candidates were motivated partly by them.

    python scripts/step4_optimize.py
Writes reports/step4.md and reports/step4_*.csv.
"""

from __future__ import annotations

import pathlib
import sys
from dataclasses import replace

import numpy as np
import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from robustness import md  # noqa: E402
from step2_multi_etf import rf_daily_for, stats  # noqa: E402
from step3_modern import UNIVERSE  # noqa: E402
from turtle_bot import TurtleBacktester, TurtleConfig  # noqa: E402
from turtle_bot.data import load_csv, load_rate_csv  # noqa: E402
from turtle_bot.metrics import max_drawdown  # noqa: E402
from turtle_bot.portfolio import ModernConfig, PortfolioBacktester  # noqa: E402

IS_START, IS_END = "2008-01-02", "2017-12-29"
OOS_START = "2018-01-02"
MIN_IS_GAIN, MIN_OOS_GAIN = 0.02, 0.05
EQ = 100_000.0


def modern(cfg):
    def run(data, rate, start):
        return PortfolioBacktester(replace(cfg, initial_equity=EQ)).run(data, UNIVERSE, rate, start).equity
    return run


def ensemble(cap=None):
    def run(data, rate, start):
        parts = []
        for e in (40, 55, 80):
            cfg = ModernConfig(entry_len=e, initial_equity=EQ / 3, max_position_pct=cap)
            parts.append(PortfolioBacktester(cfg).run(data, UNIVERSE, rate, start).equity)
        return pd.concat(parts, axis=1).ffill().sum(axis=1)
    return run


def first_liquid(df):
    df = df[df["Volume"] > 0]
    dv = (df["Close"] * df["Volume"]).rolling(60, min_periods=60).median().shift(1)
    return dv[dv >= 1e6].index.min()


def s2_sleeves(data, rate, start):
    """Original S2 long-only, equal sub-accounts; a sub-account holds T-bills
    until its ETF is liquid."""
    ref_index = data["SPY"].loc[start:].index
    rf = rf_daily_for(ref_index, rate)
    tbill = (1 + rf).cumprod()
    total = pd.Series(0.0, index=ref_index)
    per = EQ / len(data)
    bt = TurtleBacktester(TurtleConfig(system="S2", allow_short=False, initial_equity=per))
    for t, df in data.items():
        df = df[df["Volume"] > 0]
        liquid = first_liquid(df)
        sleeve = per * tbill / tbill.iloc[0]
        go = max(pd.Timestamp(start), liquid) if pd.notna(liquid) else None   # never liquid: T-bills
        if go is not None and go <= ref_index[-1]:
            eq = bt.run(df, cash_rate=rate, start=str(go.date())).equity.reindex(ref_index).ffill()
            at_go = sleeve.loc[:go].iloc[-1]
            live = eq[eq.index >= go] / per * at_go
            sleeve = sleeve.where(sleeve.index < go, live)
        total += sleeve
    return total


CANDIDATES = {
    "C0 rulebook (baseline)": modern(ModernConfig()),
    "C1 + 20% per-market cap": modern(ModernConfig(max_position_pct=0.20)),
    "C2 + 33% per-market cap": modern(ModernConfig(max_position_pct=0.33)),
    "C3 ensemble 40/55/80": ensemble(),
    "C4 ensemble + 20% cap": ensemble(0.20),
    "C5 original S2 long-only": s2_sleeves,
}


def summarize(eq, rate):
    rf = rf_daily_for(eq.index, rate)
    s = stats(eq, rf)
    s["worst_year_%"] = 100 * eq.resample("YE").last().pct_change().min()
    return s


def main() -> None:
    rate = load_rate_csv(str(ROOT / "data" / "DTB3.csv"))
    full = {t: load_csv(str(ROOT / "data" / f"{t}.csv")) for t in UNIVERSE}
    is_data = {t: df.loc[:IS_END] for t, df in full.items()}
    is_data = {t: df for t, df in is_data.items() if len(df) > 0}
    is_universe_ok = set(is_data) == set(full)

    rows = []
    curves = {}
    for name, fn in CANDIDATES.items():
        eq_is = fn(is_data if is_universe_ok else full, rate, IS_START).loc[:IS_END]
        eq_oos = fn(full, rate, OOS_START)
        curves[name] = eq_oos
        rows.append({"candidate": name,
                     **{f"IS_{k}": v for k, v in summarize(eq_is, rate).items()},
                     **{f"OOS_{k}": v for k, v in summarize(eq_oos, rate).items()}})
        print(f"{name} done", flush=True)
    table = pd.DataFrame(rows)

    base = table.iloc[0]
    best = table.loc[table["IS_excess_sharpe"].idxmax()]
    if best["candidate"] == base["candidate"] or best["IS_excess_sharpe"] - base["IS_excess_sharpe"] < MIN_IS_GAIN:
        selected, reason = base["candidate"], (f"no candidate beat the baseline by ≥ {MIN_IS_GAIN} excess Sharpe "
                                               "on 2008–2017")
    else:
        selected, reason = best["candidate"], (f"highest 2008–2017 excess Sharpe "
                                               f"({best['IS_excess_sharpe']:.2f} vs {base['IS_excess_sharpe']:.2f})")
    sel = table[table.candidate == selected].iloc[0]
    oos_gain = sel["OOS_excess_sharpe"] - base["OOS_excess_sharpe"]
    adopted = selected != base["candidate"] and oos_gain >= MIN_OOS_GAIN
    decision = pd.DataFrame([
        {"step": "selection (2008–2017)", "result": selected, "why": reason},
        {"step": "hold-out (2018–2026)",
         "result": f"{sel['OOS_excess_sharpe']:.2f} vs baseline {base['OOS_excess_sharpe']:.2f} ({oos_gain:+.2f})",
         "why": f"adoption needs ≥ +{MIN_OOS_GAIN}"},
        {"step": "DECISION", "result": f"adopt {selected}" if adopted else "keep the rulebook as written",
         "why": "pre-registered rule"},
    ])

    subs = []
    for name, eq in curves.items():
        for a, b in (("2018", "2020"), ("2021", "2023"), ("2024", "2026")):
            seg = eq.loc[a:b]
            yrs = (seg.index[-1] - seg.index[0]).days / 365.25
            subs.append({"candidate": name, "period": f"{a}-{b}",
                         "cagr_%": 100 * ((seg.iloc[-1] / seg.iloc[0]) ** (1 / yrs) - 1),
                         "max_dd_%": 100 * max_drawdown(seg)})
    subs = pd.DataFrame(subs).pivot(index="candidate", columns="period", values="cagr_%").reset_index()

    for name, t in [("step4_candidates", table), ("step4_decision", decision), ("step4_holdout_subperiods", subs)]:
        t.to_csv(ROOT / "reports" / f"{name}.csv", index=False)
    show = table[["candidate", "IS_cagr_%", "IS_max_dd_%", "IS_excess_sharpe",
                  "OOS_cagr_%", "OOS_max_dd_%", "OOS_MAR", "OOS_excess_sharpe", "OOS_worst_year_%"]]
    report = [
        "# Step 4 — pre-registered optimization (generated by scripts/step4_optimize.py)\n",
        f"Selection on {IS_START} → {IS_END} (later data cut off); hold-out {OOS_START} → end, run once "
        "with fresh capital. Cash account, 2 bp, T-bills, 14 ETFs.\n",
        "## Decision\n", md(decision),
        "\n## Candidates\n", md(show.round(2)),
        "\n## Hold-out CAGR % by sub-period\n", md(subs.round(2)),
    ]
    (ROOT / "reports" / "step4.md").write_text("\n".join(report) + "\n")
    print((ROOT / "reports" / "step4.md").read_text())


if __name__ == "__main__":
    main()
