"""Step 2: out-of-sample test of the complete Turtle rules on other ETFs.

No parameter is changed from the SPY work. SPY is the development market;
every other ETF is out of sample. Pass criteria were fixed BEFORE running:

  Long-only passes on an ETF if, for the reference system (S1+S2 complete):
    (a) excess Sharpe over T-bills > 0, and
    (b) at most 10% of random-entry runs beat its Sharpe.
  Shorts: report each ETF's short-side P&L to see whether shorts work on any
  asset class (bonds and gold have no equity-style upward drift).
  Diversification: equal-weight basket of all ETFs vs SPY alone.

    python scripts/step2_multi_etf.py
Writes reports/step2.md and reports/step2_*.csv.
"""

from __future__ import annotations

import pathlib
import sys

import numpy as np
import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from robustness import md  # noqa: E402
from turtle_bot import TurtleBacktester, TurtleConfig  # noqa: E402
from turtle_bot.backtest import align_cash_rate  # noqa: E402
from turtle_bot.data import load_csv, load_rate_csv  # noqa: E402
from turtle_bot.metrics import max_drawdown  # noqa: E402

TD = 252
WARMUP_BARS = 60          # 55-day channel + N must exist before stats start
N_RANDOM = 100
UNIVERSE = {
    "SPY": "US large cap (development market)",
    "QQQ": "US Nasdaq-100",
    "IWM": "US small cap",
    "DIA": "US Dow 30",
    "EFA": "Developed ex-US equity",
    "EEM": "Emerging-market equity",
    "TLT": "US 20y+ Treasuries",
    "GLD": "Gold",
}
SYSTEMS = ["S1", "S2", "S1+S2"]


def rf_daily_for(index: pd.DatetimeIndex, rate: pd.Series) -> pd.Series:
    known = pd.Series(align_cash_rate(rate, index), index=index)
    days = pd.Series(index, index=index).diff().dt.days.fillna(0)
    return known / 100 * days / 365


def stats(eq: pd.Series, rf: pd.Series) -> dict:
    r = eq.pct_change().dropna()
    ex = r - rf.reindex(r.index).fillna(0)
    yrs = (eq.index[-1] - eq.index[0]).days / 365.25
    cagr = (eq.iloc[-1] / eq.iloc[0]) ** (1 / yrs) - 1
    dd = max_drawdown(eq)
    return {"cagr_%": 100 * cagr, "max_dd_%": 100 * dd, "MAR": cagr / abs(dd) if dd else np.nan,
            "excess_sharpe": ex.mean() / ex.std() * np.sqrt(TD) if ex.std() > 0 else 0.0}


class RandomEntryCombined(TurtleBacktester):
    """Same engine and exits; each system's long entry is a daily coin flip
    with its own probability. Filter/failsafe are off (meaningless for random
    entries). Channels stay NaN during warm-up so start dates match."""

    def __init__(self, config, p_by_system: dict, seed: int):
        super().__init__(config)
        self.p, self.seed = p_by_system, seed

    def _channels(self, df, cfg):
        ent_hi, ent_lo, ex_hi, ex_lo, fs_hi, fs_lo = super()._channels(df, cfg)
        offset = 0 if cfg.system == "S1" else 10_000
        fire = np.random.default_rng(self.seed + offset).random(len(df)) < self.p[cfg.system]
        rand_hi = np.where(fire, -np.inf, np.inf)
        rand_hi[np.isnan(ent_hi)] = np.nan
        rand_lo = np.where(np.isnan(ent_lo), np.nan, -np.inf)
        return rand_hi, rand_lo, ex_hi, ex_lo, fs_hi, fs_lo


def main() -> None:
    rate = load_rate_csv(str(ROOT / "data" / "DTB3.csv"))
    out = ROOT / "reports"
    out.mkdir(exist_ok=True)

    rows, shorts_rows, rnd_rows = [], [], []
    curves, prices = {}, {}
    for tkr, desc in UNIVERSE.items():
        df = load_csv(str(ROOT / "data" / f"{tkr}.csv"))
        start = df.index[WARMUP_BARS]
        rf = rf_daily_for(df.index, rate)
        prices[tkr] = df["Close"]
        bh = stats(df["Close"].loc[start:], rf)
        rows.append({"etf": tkr, "system": "buy&hold", "shorts": "-", **bh, "trades": 0})
        for system in SYSTEMS:
            for short in (True, False):
                res = TurtleBacktester(TurtleConfig(system=system, allow_short=short)).run(df, cash_rate=rate)
                tf = res.trades_frame()
                rows.append({"etf": tkr, "system": system, "shorts": short,
                             **stats(res.equity.loc[start:], rf), "trades": len(tf)})
                curves[(tkr, system, short)] = res.equity
                if short and system == "S1+S2":
                    sh = tf[tf.direction < 0]
                    lg = tf[tf.direction > 0]
                    loss = -sh.pnl[sh.pnl < 0].sum()
                    shorts_rows.append({
                        "etf": tkr, "asset": desc, "short_trades": len(sh),
                        "short_pnl_%_of_initial": 100 * sh.pnl.sum() / 100_000,
                        "long_pnl_%_of_initial": 100 * lg.pnl.sum() / 100_000,
                        "short_profit_factor": sh.pnl[sh.pnl > 0].sum() / loss if loss > 0 else np.inf,
                        "short_win_%": 100 * (sh.pnl > 0).mean() if len(sh) else np.nan})
                if not short and system == "S1+S2":
                    real = stats(res.equity.loc[start:], rf)
                    d = res.daily
                    ready = d["entry_hi"].notna()
                    p = {s: tf[tf.system == s].shape[0] / max(int(((d[f"pos_{s}"] == 0) & ready).sum()), 1)
                         for s in ("S1", "S2")}
                    rcfg = TurtleConfig(system="S1+S2", allow_short=False, use_skip_filter=False)
                    sims = [stats(RandomEntryCombined(rcfg, p, seed).run(df, cash_rate=rate).equity.loc[start:], rf)
                            for seed in range(N_RANDOM)]
                    sims = pd.DataFrame(sims)
                    beat = 100 * (sims["excess_sharpe"] >= real["excess_sharpe"]).mean()
                    rnd_rows.append({
                        "etf": tkr, "asset": desc, "start": start.date(),
                        "real_cagr_%": real["cagr_%"], "real_excess_sharpe": real["excess_sharpe"],
                        "random_median_cagr_%": sims["cagr_%"].median(),
                        "random_median_excess_sharpe": sims["excess_sharpe"].median(),
                        "pct_random_beating_real": beat,
                        "buyhold_excess_sharpe": bh["excess_sharpe"],
                        "PASS": bool(real["excess_sharpe"] > 0 and beat <= 10)})
        print(f"{tkr} done", flush=True)

    table = pd.DataFrame(rows)
    shorts_t = pd.DataFrame(shorts_rows)
    rnd = pd.DataFrame(rnd_rows)

    # ---------------- equal-weight basket, no rebalancing, common start
    common = max(load_csv(str(ROOT / "data" / f"{t}.csv")).index[WARMUP_BARS] for t in UNIVERSE)
    end = min(c.index[-1] for c in curves.values())
    rf_common = rf_daily_for(prices["SPY"].loc[common:end].index, rate)

    def basket(series_by_tkr):
        parts = [s.loc[common:end] / s.loc[common:end].iloc[0] / len(series_by_tkr) for s in series_by_tkr]
        return pd.concat(parts, axis=1).ffill().sum(axis=1)

    bask_rows = []
    for short in (False, True):
        label = "long+short" if short else "long-only"
        for system in SYSTEMS:
            eq = basket([curves[(t, system, short)] for t in UNIVERSE])
            bask_rows.append({"portfolio": f"8-ETF basket {system} {label}", **stats(eq, rf_common)})
            spy = curves[("SPY", system, short)].loc[common:end]
            bask_rows.append({"portfolio": f"SPY only {system} {label}", **stats(spy, rf_common)})
    bask_rows.append({"portfolio": "8-ETF basket buy&hold", **stats(basket(list(prices.values())), rf_common)})
    bask_rows.append({"portfolio": "SPY buy&hold", **stats(prices["SPY"].loc[common:end], rf_common)})
    bask = pd.DataFrame(bask_rows)

    rets = pd.DataFrame({t: curves[(t, "S1+S2", False)].loc[common:end].pct_change() for t in UNIVERSE}).dropna()
    corr = rets.corr()
    avg_corr = (corr.values.sum() - len(corr)) / (len(corr) ** 2 - len(corr))
    px_corr = pd.DataFrame({t: prices[t].loc[common:end].pct_change() for t in UNIVERSE}).dropna().corr()

    for name, t in [("step2_etf_results", table), ("step2_shorts", shorts_t), ("step2_random", rnd),
                    ("step2_basket", bask), ("step2_strategy_corr", corr)]:
        t.to_csv(out / f"{name}.csv", index=name == "step2_strategy_corr")

    def piv(short):
        t = table[(table.shorts == short) | (table.system == "buy&hold")].copy()
        t["col"] = t.system
        return t.pivot(index="etf", columns="col", values="excess_sharpe").loc[list(UNIVERSE)].reset_index()

    report = [
        "# Step 2 — multi-ETF out-of-sample test (generated by scripts/step2_multi_etf.py)\n",
        "Complete rules (drawdown rule, T-bill interest, 1% risk/unit, 4 units, 2N stop, 1x cap, "
        f"2 bp slippage), no parameter changes. Stats start {WARMUP_BARS} bars after each ETF's first date. "
        f"Random benchmark: {N_RANDOM} runs per ETF.\n",
        "## Pass / fail (reference system: S1+S2 long-only)\n", md(rnd.round(2)),
        "\n## Excess Sharpe — long-only\n", md(piv(False).round(2)),
        "\n## Excess Sharpe — long + short\n", md(piv(True).round(2)),
        "\n## Short side, S1+S2 (P&L as % of initial equity)\n", md(shorts_t.round(2)),
        f"\n## Equal-weight basket vs SPY alone ({common.date()} → {end.date()}, no rebalancing)\n",
        md(bask.round(2)),
        f"\nAverage pairwise correlation of daily strategy returns (S1+S2 long-only): {avg_corr:.2f}. "
        f"Of ETF prices: {(px_corr.values.sum() - 8) / 56:.2f}.\n",
        "\n## All runs\n", md(table.round(2)),
    ]
    (out / "step2.md").write_text("\n".join(report) + "\n")
    print((out / "step2.md").read_text())


if __name__ == "__main__":
    main()
