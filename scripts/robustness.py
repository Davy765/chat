"""Robustness tests for the SPY Turtle backtest (no rule changes).

Sections
  A. Sub-period performance (fixed blocks + calendar years)
  B. Channel-length grid (entry x exit), long/short and long-only
  C. Stop distance x max units grid
  D. Transaction-cost sensitivity
  E. Walk-forward parameter selection (is choosing the "best" grid cell real?)
  F. Random-entry benchmark (is the breakout signal better than random entries
     with the same exits, stops and sizing?)
  G. Numerical path noise: every price multiplied by 1 +/- 1e-6 (invisible,
     smaller than a data vendor's re-adjustment). Whole-share rounding makes
     the path chaotic, so this measures how much ANY single result can move
     for no economic reason.

Every backtest runs on the full history once. Sub-period numbers are sliced
from that single causal equity curve, so no period loses its warm-up and
positions carry across boundaries exactly as they would live.

    python scripts/robustness.py data/SPY.csv
Writes reports/robustness.md and reports/*.csv.
"""

from __future__ import annotations

import pathlib
import sys
from itertools import product

import numpy as np
import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from turtle_bot import TurtleBacktester, TurtleConfig  # noqa: E402
from turtle_bot.data import load_csv  # noqa: E402
from turtle_bot.metrics import max_drawdown  # noqa: E402

TD = 252
BLOCKS = [("1993-1999", "1993", "1999"), ("2000-2004", "2000", "2004"),
          ("2005-2009", "2005", "2009"), ("2010-2014", "2010", "2014"),
          ("2015-2019", "2015", "2019"), ("2020-2026", "2020", "2026")]
BASES = {
    "S1 long+short": dict(system="S1"),
    "S1 long-only": dict(system="S1", allow_short=False),
    "S2 long+short": dict(system="S2"),
    "S2 long-only": dict(system="S2", allow_short=False),
}


# ----------------------------------------------------------------- helpers
def stats(eq: pd.Series) -> dict:
    eq = eq.dropna()
    r = eq.pct_change().dropna()
    yrs = max((eq.index[-1] - eq.index[0]).days / 365.25, 1e-9)
    growth = eq.iloc[-1] / eq.iloc[0]
    return {"return_%": 100 * (growth - 1),
            "cagr_%": 100 * (growth ** (1 / yrs) - 1),
            "max_dd_%": 100 * max_drawdown(eq),
            "sharpe": r.mean() / r.std() * np.sqrt(TD) if r.std() > 0 else 0.0}


# The published report (ROBUSTNESS.md) was produced with the v1 engine
# settings: no drawdown rule, no interest on cash. Pin them so the report
# stays reproducible; pass drawdown_rule=True to test the complete rules.
V1 = dict(drawdown_rule=False)


def run(df, **kw):
    return TurtleBacktester(TurtleConfig(**{**V1, **kw})).run(df)


def md(df: pd.DataFrame, floatfmt=".2f") -> str:
    out = df.copy()
    for c in out.columns:
        if pd.api.types.is_float_dtype(out[c]):
            out[c] = out[c].map(lambda v: f"{v:{floatfmt}}")
    cols = [str(c) for c in out.columns]
    lines = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    lines += ["| " + " | ".join(str(v) for v in row) + " |" for row in out.itertuples(index=False)]
    return "\n".join(lines)


class RandomEntryBacktester(TurtleBacktester):
    """Same engine, same exits/stops/sizing/costs; only the long entry trigger
    is replaced by a coin flip each day (fills at the open). Channels remain
    NaN during warm-up so the start date matches the real system."""

    def __init__(self, config, p: float, seed: int):
        super().__init__(config)
        self.p, self.seed = p, seed

    def _channels(self, df, cfg):
        ent_hi, ent_lo, ex_hi, ex_lo, fs_hi, fs_lo = super()._channels(df, cfg)
        rng = np.random.default_rng(self.seed)
        fire = rng.random(len(df)) < self.p
        rand_hi = np.where(fire, -np.inf, np.inf)
        rand_hi[np.isnan(ent_hi)] = np.nan
        rand_lo = np.where(np.isnan(ent_lo), np.nan, -np.inf)  # never short
        return rand_hi, rand_lo, ex_hi, ex_lo, fs_hi, fs_lo


# ---------------------------------------------------------------- sections
def section_a(df):
    bh = df["Close"]
    rows, yearly = [], {}
    for name, kw in BASES.items():
        res = run(df, **kw)
        eq = res.equity
        tf = res.trades_frame()
        for label, a, b in BLOCKS:
            s = stats(eq.loc[a:b])
            n = int(((tf.entry_date >= a) & (tf.entry_date <= f"{b}-12-31")).sum())
            rows.append({"variant": name, "period": label, **s, "trades": n})
        yearly[name] = eq.resample("YE").last().pct_change().mul(100)
        yearly[name].iloc[0] = 100 * (eq.resample("YE").last().iloc[0] / eq.iloc[0] - 1)
    for label, a, b in BLOCKS:
        s = stats(bh.loc[a:b])
        rows.append({"variant": "SPY buy&hold", "period": label, **s, "trades": 0})
    yearly["SPY buy&hold"] = bh.resample("YE").last().pct_change().mul(100)
    yearly["SPY buy&hold"].iloc[0] = 100 * (bh.resample("YE").last().iloc[0] / bh.iloc[0] - 1)
    blocks = pd.DataFrame(rows)
    years = pd.DataFrame(yearly)
    years.index = years.index.year
    return blocks, years


def grid_runs(df):
    """Full-history equity curves for every grid cell (reused by B and E)."""
    entries = [10, 15, 20, 30, 40, 55, 70, 90, 120]
    fracs = [0.25, 0.5, 0.75]
    curves, rows = {}, []
    for short, e, f in product([True, False], entries, fracs):
        x = max(2, round(e * f))
        res = run(df, system="S2", entry_len=e, exit_len=x, allow_short=short)
        key = (short, e, x)
        curves[key] = res.equity
        rows.append({"shorts": short, "entry": e, "exit": x,
                     **stats(res.equity.loc["1994":]), "trades": len(res.trades)})
    return pd.DataFrame(rows), curves


def section_c(df):
    rows = []
    for system, stop_n, units in product(["S1", "S2"], [1.0, 1.5, 2.0, 3.0, 4.0, 8.0], [1, 2, 4]):
        res = run(df, system=system, allow_short=False, stop_n=stop_n, max_units=units)
        rows.append({"system": system, "stop_N": stop_n, "max_units": units,
                     **stats(res.equity.loc["1994":]), "trades": len(res.trades)})
    return pd.DataFrame(rows)


def section_d(df):
    rows = []
    for name, kw in BASES.items():
        for bps in [0, 2, 5, 10, 20]:
            res = run(df, slippage_bps=bps, **kw)
            rows.append({"variant": name, "slippage_bps": bps, **stats(res.equity.loc["1994":])})
    return pd.DataFrame(rows)


def section_e(curves, train_years=8, test_years=2):
    """Each test window picks the grid cell with the best Sharpe over the
    preceding train window, then records that cell's out-of-sample returns."""
    rows, oos = [], {True: [], False: []}
    for short in (True, False):
        keys = [k for k in curves if k[0] == short]
        rets = pd.DataFrame({k: curves[k].pct_change() for k in keys})
        for y in range(1994 + train_years, 2027, test_years):
            tr = rets.loc[str(y - train_years):str(y - 1)]
            te = rets.loc[str(y):str(min(y + test_years - 1, 2026))]
            if te.empty:
                continue
            sh = tr.mean() / tr.std() * np.sqrt(TD)
            best = sh.idxmax()
            te_sh = te.mean() / te.std() * np.sqrt(TD)
            rows.append({"shorts": short, "test": f"{y}-{min(y + test_years - 1, 2026)}",
                         "chosen_entry/exit": f"{best[1]}/{best[2]}",
                         "train_sharpe": sh[best], "test_sharpe": te_sh[best],
                         "test_rank_of_chosen": int(te_sh.rank(ascending=False)[best]),
                         "cells": len(keys)})
            oos[short].append(te[best])
    summary = []
    for short in (True, False):
        wf = pd.concat(oos[short])
        eq_wf = (1 + wf.fillna(0)).cumprod()
        default = curves[(short, 20, 10)].loc[wf.index[0]:]
        median_cell = pd.DataFrame({k: curves[k].pct_change() for k in curves if k[0] == short}).loc[wf.index]
        med_eq = (1 + median_cell.fillna(0)).cumprod().iloc[-1]
        summary.append({"shorts": short, "method": "walk-forward best-of-grid", **stats(eq_wf)})
        summary.append({"shorts": short, "method": "fixed 20/10 (S1 rules, no filter)", **stats(default)})
        summary.append({"shorts": short, "method": "median grid cell (final value)",
                        "return_%": 100 * (med_eq.median() - 1), "cagr_%": np.nan,
                        "max_dd_%": np.nan, "sharpe": np.nan})
    return pd.DataFrame(rows), pd.DataFrame(summary)


def section_f(df, n_seeds=200):
    rows = []
    for label, e, x in [("20/10", 20, 10), ("55/20", 55, 20)]:
        cfg = TurtleConfig(system="S2", entry_len=e, exit_len=x, allow_short=False, **V1)
        real = TurtleBacktester(cfg).run(df)
        real_s = stats(real.equity.loc["1994":])
        flat_ready = int(((real.daily["position"] == 0) & real.daily["entry_hi"].notna()).sum())
        p = len(real.trades) / max(flat_ready, 1)
        sims = []
        for seed in range(n_seeds):
            r = RandomEntryBacktester(cfg, p=p, seed=seed).run(df)
            sims.append({**stats(r.equity.loc["1994":]), "trades": len(r.trades)})
        sims = pd.DataFrame(sims)
        rows.append({"rules": label, "real_cagr_%": real_s["cagr_%"], "real_sharpe": real_s["sharpe"],
                     "real_trades": len(real.trades),
                     "random_median_cagr_%": sims["cagr_%"].median(),
                     "random_median_sharpe": sims["sharpe"].median(),
                     "random_median_trades": sims["trades"].median(),
                     "pct_random_beating_real_sharpe": 100 * (sims["sharpe"] >= real_s["sharpe"]).mean(),
                     "pct_random_beating_real_cagr": 100 * (sims["cagr_%"] >= real_s["cagr_%"]).mean()})
    return pd.DataFrame(rows)


def section_g(df, n_seeds=50):
    rows = []
    for name, kw in BASES.items():
        base = stats(run(df, **kw).equity.loc["1994":])
        cagrs, dds = [], []
        for seed in range(n_seeds):
            f = 1 + np.random.default_rng(seed).uniform(-1e-6, 1e-6, len(df))
            d = df.copy()
            for col in ("Open", "High", "Low", "Close"):
                d[col] = d[col] * f
            d["High"] = d[["Open", "High", "Close"]].max(axis=1)
            d["Low"] = d[["Open", "Low", "Close"]].min(axis=1)
            s = stats(run(d, **kw).equity.loc["1994":])
            cagrs.append(s["cagr_%"])
            dds.append(s["max_dd_%"])
        rows.append({"variant": name, "unperturbed_cagr_%": base["cagr_%"],
                     "cagr_p5_%": np.percentile(cagrs, 5), "cagr_median_%": np.median(cagrs),
                     "cagr_p95_%": np.percentile(cagrs, 95),
                     "max_dd_p5_%": np.percentile(dds, 5), "max_dd_p95_%": np.percentile(dds, 95)})
    return pd.DataFrame(rows)


# -------------------------------------------------------------------- main
def main() -> None:
    path = sys.argv[1] if len(sys.argv) > 1 else str(ROOT / "data" / "SPY.csv")
    df = load_csv(path)
    out = ROOT / "reports"
    out.mkdir(exist_ok=True)

    blocks, years = section_a(df)
    grid, curves = grid_runs(df)
    stopgrid = section_c(df)
    costs = section_d(df)
    wf_rows, wf_sum = section_e(curves)
    rnd = section_f(df)
    noise = section_g(df)

    for name, t in [("subperiods", blocks), ("yearly", years), ("channel_grid", grid),
                    ("stop_units_grid", stopgrid), ("costs", costs),
                    ("walkforward_windows", wf_rows), ("walkforward_summary", wf_sum),
                    ("random_entry", rnd), ("path_noise", noise)]:
        t.to_csv(out / f"{name}.csv", index=name == "yearly")

    def pivot(short):
        g = grid[grid.shorts == short].copy()
        g["exit_frac"] = (g.exit / g.entry).round(2).map(lambda v: {0.25: "~1/4", 0.5: "1/2", 0.75: "~3/4"}.get(
            min([0.25, 0.5, 0.75], key=lambda f: abs(f - v))))
        t = g.pivot(index="entry", columns="exit_frac", values="sharpe")[["~1/4", "1/2", "~3/4"]]
        t.columns = [f"exit {c} of entry" for c in t.columns]
        return t.reset_index()

    yrs = years.loc[1994:]
    year_summary = pd.DataFrame({
        "variant": yrs.columns,
        "positive_years_%": [(yrs[c] > 0).mean() * 100 for c in yrs.columns],
        "worst_year_%": [yrs[c].min() for c in yrs.columns],
        "best_year_%": [yrs[c].max() for c in yrs.columns],
        "corr_with_SPY_years": [yrs[c].corr(yrs["SPY buy&hold"]) for c in yrs.columns],
    })

    report = [
        "# Robustness report — SPY Turtle (generated by scripts/robustness.py)\n",
        f"Data: SPY daily, {df.index[0].date()} → {df.index[-1].date()}, dividend-adjusted. "
        "Defaults: 1% risk/unit, 4 units, 2N stop, 1x gross cap, 2 bp slippage. "
        "Full-period stats start 1994 (after warm-up).\n",
        "## A. Sub-periods\n", md(blocks.round(2)),
        "\n### Calendar years (1994+)\n", md(year_summary.round(2)),
        "\n## B. Channel-length grid — Sharpe (S2-style engine, no skip filter)\n",
        "### Long + short\n", md(pivot(True)),
        "\n### Long-only\n", md(pivot(False)),
        "\nFull grid with CAGR / drawdown / trades: `reports/channel_grid.csv`.\n",
        "## C. Stop distance × max units (long-only)\n", md(stopgrid.round(2)),
        "\n## D. Slippage sensitivity\n", md(costs.round(2)),
        "\n## E. Walk-forward selection (8y train → 2y test, rolling)\n", md(wf_sum.round(2)),
        "\n### Windows\n", md(wf_rows.round(2)),
        "\n## F. Random-entry benchmark (long-only, 200 seeds)\n", md(rnd.round(2)),
        "\n## G. Numerical path noise (prices x (1 ± 1e-6), 50 seeds)\n", md(noise.round(2)),
    ]
    (out / "robustness.md").write_text("\n".join(report) + "\n")
    print((out / "robustness.md").read_text())


if __name__ == "__main__":
    main()
