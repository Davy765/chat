"""Step 3: backtest the "Modern Turtle" rulebook (Track B: ETFs, long/flat).

Pre-registered pass criteria (fixed and committed before the first run):

  Main configuration: rulebook defaults, cash account (1x gross cap), 2 bp
  slippage, T-bill interest on idle cash, 14 Track-B ETFs, trading from
  2008-01-02 to the end of the frozen snapshot.

  P1  excess Sharpe over T-bills > 0.
  P2  at most 10% of 100 random-entry runs beat its excess Sharpe. The random
      runs keep every other rule (trend filter, N, sizing, adds, stops,
      exits, caps, drawdown rules); only the breakout entry is replaced by a
      coin flip whose rate is calibrated to the real number of entries.
  P3  the rulebook's own robustness grid, entry 40/55/80 days x stop and
      trailing distance 2.5/3/3.5 N: excess Sharpe > 0 in at least 7 of 9.

  Informational (no pass/fail): equal-weight buy & hold of the same ETFs,
  SPY buy & hold, the original Turtle S2 long-only rules on the same ETFs,
  2x margin, 10 bp slippage, random entries without the trend filter,
  sub-periods.

Crypto ETFs (IBIT, ETHA) are excluded: they started in 2024, too short for
a 200-day trend filter plus a test.

    python scripts/step3_modern.py
Writes reports/step3.md and reports/step3_*.csv.
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
from turtle_bot import TurtleBacktester, TurtleConfig  # noqa: E402
from turtle_bot.data import load_csv, load_rate_csv  # noqa: E402
from turtle_bot.metrics import max_drawdown  # noqa: E402
from turtle_bot.portfolio import ModernConfig, PortfolioBacktester  # noqa: E402

START = "2008-01-02"
N_RANDOM = 100
UNIVERSE = {
    "SPY": "equity", "QQQ": "equity", "IWM": "equity", "EFA": "equity", "EEM": "equity",
    "TLT": "bonds", "IEF": "bonds",
    "GLD": "metals", "SLV": "metals", "CPER": "metals",
    "USO": "energy", "UNG": "energy",
    "DBA": "agriculture",
    "UUP": "dollar",
}
SUBPERIODS = [("2008-2012", "2008", "2012"), ("2013-2017", "2013", "2017"),
              ("2018-2022", "2018", "2022"), ("2023-2026", "2023", "2026")]


class RandomEntry(PortfolioBacktester):
    """Identical rules; the breakout entry becomes a calibrated coin flip.
    Draws are per (market, bar) so they do not depend on processing order."""

    def __init__(self, config, p: float, seed: int, keep_trend_filter: bool = True):
        super().__init__(config)
        self.p, self.seed, self.keep_trend = p, seed, keep_trend_filter
        self._draws: dict[str, np.ndarray] = {}

    def entry_signal(self, m, j):
        if np.isnan(m.n[j]) or np.isnan(m.ent_hi[j]) or (self.keep_trend and not m.trend_ok[j]):
            return False, 0.0
        u = self._draws.get(m.ticker)
        if u is None:
            key = self.seed * 1000 + sum(ord(ch) * 31 ** k for k, ch in enumerate(m.ticker)) % 1000
            u = self._draws[m.ticker] = np.random.default_rng(key).random(len(m.c))
        return (u[j] < self.p), float(u[j])


def entries(res) -> int:
    return len(res.trades) + len(res.open_positions)


def calibrate(cfg, data, rate, target, keep_trend):
    p = 0.02
    for _ in range(4):
        got = np.mean([entries(RandomEntry(cfg, p, s, keep_trend).run(data, UNIVERSE, rate, START))
                       for s in range(5)])
        p *= target / max(got, 1)
    return p


def window_stats(eq, rf):
    s = stats(eq, rf)
    s["worst_year_%"] = 100 * eq.resample("YE").last().pct_change().min()
    return s


def main() -> None:
    rate = load_rate_csv(str(ROOT / "data" / "DTB3.csv"))
    data = {t: load_csv(str(ROOT / "data" / f"{t}.csv")) for t in UNIVERSE}
    out = ROOT / "reports"
    out.mkdir(exist_ok=True)

    base = ModernConfig()
    main_res = PortfolioBacktester(base).run(data, UNIVERSE, rate, START)
    eq = main_res.equity
    rf = rf_daily_for(eq.index, rate)
    main_s = window_stats(eq, rf)
    tf = main_res.trades_frame()

    # ---------------- P2: random entries
    rnd_rows = []
    target = entries(main_res)
    for keep in (True, False):
        p = calibrate(base, data, rate, target, keep)
        sims = []
        for seed in range(N_RANDOM):
            r = RandomEntry(base, p, seed, keep).run(data, UNIVERSE, rate, START)
            sims.append({**stats(r.equity, rf), "entries": entries(r)})
        sims = pd.DataFrame(sims)
        rnd_rows.append({"random_entries": "with trend filter" if keep else "no trend filter",
                         "p_per_bar": p, "real_excess_sharpe": main_s["excess_sharpe"],
                         "random_median_excess_sharpe": sims["excess_sharpe"].median(),
                         "random_p90_excess_sharpe": sims["excess_sharpe"].quantile(0.9),
                         "pct_random_beating_real": 100 * (sims["excess_sharpe"] >= main_s["excess_sharpe"]).mean(),
                         "real_cagr_%": main_s["cagr_%"], "random_median_cagr_%": sims["cagr_%"].median(),
                         "real_entries": target, "random_median_entries": sims["entries"].median()})
    rnd = pd.DataFrame(rnd_rows)

    # ---------------- P3: rulebook robustness grid
    grid_rows = []
    for e in (40, 55, 80):
        for k in (2.5, 3.0, 3.5):
            r = PortfolioBacktester(replace(base, entry_len=e, stop_n=k, trail_n=k)).run(data, UNIVERSE, rate, START)
            grid_rows.append({"entry_days": e, "stop_trail_N": k, **window_stats(r.equity, rf),
                              "trades": len(r.trades)})
    grid = pd.DataFrame(grid_rows)

    # ---------------- informational variants
    var_rows = [{"variant": "MAIN: rulebook, cash 1x, 2 bp", **main_s, "trades": len(tf),
                 "avg_gross_leverage": main_res.daily["gross_leverage"].mean()}]
    for label, cfg in [("2x margin (T-bill + 1.5% on borrowing)", replace(base, max_leverage=2.0)),
                       ("10 bp slippage", replace(base, slippage_bps=10.0)),
                       ("no trend filter", replace(base, use_trend_filter=False)),
                       ("no trailing stop", replace(base, use_trailing=False))]:
        r = PortfolioBacktester(cfg).run(data, UNIVERSE, rate, START)
        var_rows.append({"variant": label, **window_stats(r.equity, rf), "trades": len(r.trades),
                         "avg_gross_leverage": r.daily["gross_leverage"].mean()})

    # benchmarks: equal-weight sleeves, cash (T-bills) until each ETF is liquid
    rf_full = rf.copy()
    tbill = (1 + rf_full).cumprod()
    first_liquid = {}
    for t, df in data.items():
        df = df[df["Volume"] > 0]
        dv = (df["Close"] * df["Volume"]).rolling(60, min_periods=60).median().shift(1)
        first_liquid[t] = dv[dv >= 1e6].index.min()

    def sleeve_bh(t):
        px = data[t]["Close"].reindex(eq.index).ffill()
        start = max(pd.Timestamp(START), first_liquid[t])
        v = tbill / tbill.loc[:start].iloc[-1]
        v[v.index >= start] = px[px.index >= start] / px.loc[start:].iloc[0]
        return v

    def sleeve_s2(t):
        df = data[t]
        df = df[df["Volume"] > 0]
        df = df[df.index >= first_liquid[t] - pd.Timedelta(days=120)]
        r = TurtleBacktester(TurtleConfig(system="S2", allow_short=False)).run(df, cash_rate=rate)
        e = r.equity.reindex(eq.index).ffill()
        start = max(pd.Timestamp(START), e.first_valid_index())
        v = tbill / tbill.loc[:start].iloc[-1]
        v[v.index >= start] = e[e.index >= start] / e.loc[start:].iloc[0]
        return v

    bh = sum(sleeve_bh(t) for t in UNIVERSE) / len(UNIVERSE)
    s2 = sum(sleeve_s2(t) for t in UNIVERSE) / len(UNIVERSE)
    spy = data["SPY"]["Close"].reindex(eq.index).ffill()
    for label, curve in [("Benchmark: equal-weight buy & hold, same 14 ETFs", bh),
                         ("Benchmark: original Turtle S2 long-only, same 14 ETFs", s2),
                         ("Benchmark: SPY buy & hold", spy)]:
        var_rows.append({"variant": label, **window_stats(curve, rf), "trades": np.nan,
                         "avg_gross_leverage": np.nan})
    variants = pd.DataFrame(var_rows)

    sub_rows = []
    for name, a, b in SUBPERIODS:
        for label, curve in [("Modern (main)", eq), ("EW buy & hold", bh), ("SPY", spy)]:
            seg = curve.loc[a:b]
            yrs = (seg.index[-1] - seg.index[0]).days / 365.25
            sub_rows.append({"period": name, "portfolio": label,
                             "cagr_%": 100 * ((seg.iloc[-1] / seg.iloc[0]) ** (1 / yrs) - 1),
                             "max_dd_%": 100 * max_drawdown(seg)})
    subs = pd.DataFrame(sub_rows).pivot(index="portfolio", columns="period", values="cagr_%").reset_index()

    by_mkt = tf.groupby("ticker").agg(trades=("pnl", "size"), pnl=("pnl", "sum"),
                                      win_pct=("pnl", lambda x: 100 * (x > 0).mean()),
                                      avg_R=("r_multiple", "mean")).reset_index()
    by_mkt["pnl_%_of_initial"] = 100 * by_mkt.pop("pnl") / 100_000
    by_mkt = by_mkt.sort_values("pnl_%_of_initial", ascending=False)
    exits = tf.exit_reason.value_counts().rename_axis("exit").reset_index(name="count")
    d = main_res.daily
    usage = pd.DataFrame([{
        "avg_gross_leverage": d["gross_leverage"].mean(),
        "days_at_>=95%_cash_cap_%": 100 * (d["gross_leverage"] >= 0.95).mean(),
        "avg_heat_%": 100 * d["heat"].mean(), "max_heat_%": 100 * d["heat"].max(),
        "days_heat_>=5%_%": 100 * (d["heat"] >= 0.05).mean(),
        "entries_dropped_by_cash_cap": sum("leverage cap" in m for m in main_res.log),
        "drawdown_pauses": sum("paused" in m for m in main_res.log),
        "win_rate_%": 100 * (tf.pnl > 0).mean(), "avg_R": tf.r_multiple.mean(),
        "same_bar_stops": int(tf.same_bar_stop.sum()), "trades": len(tf)}])

    verdict = pd.DataFrame([
        {"criterion": "P1 excess Sharpe > 0", "value": f"{main_s['excess_sharpe']:.2f}",
         "pass": bool(main_s["excess_sharpe"] > 0)},
        {"criterion": "P2 <= 10% of random entries (with trend filter) beat it",
         "value": f"{rnd.iloc[0]['pct_random_beating_real']:.0f}%",
         "pass": bool(rnd.iloc[0]["pct_random_beating_real"] <= 10)},
        {"criterion": "P3 excess Sharpe > 0 in >= 7 of 9 grid cells",
         "value": f"{int((grid['excess_sharpe'] > 0).sum())} of 9",
         "pass": bool((grid["excess_sharpe"] > 0).sum() >= 7)},
    ])

    for name, t in [("step3_verdict", verdict), ("step3_variants", variants), ("step3_random", rnd),
                    ("step3_grid", grid), ("step3_subperiods", subs), ("step3_by_market", by_mkt),
                    ("step3_usage", usage)]:
        t.to_csv(out / f"{name}.csv", index=False)
    report = [
        "# Step 3 — Modern Turtle rulebook backtest (generated by scripts/step3_modern.py)\n",
        f"Track B, {len(UNIVERSE)} ETFs, long/flat, trading {START} → {eq.index[-1].date()}. "
        "Rulebook defaults; cash account (1x gross cap); 2 bp slippage; T-bill interest. "
        f"Random benchmark: {N_RANDOM} runs per variant.\n",
        "## Pre-registered criteria\n", md(verdict),
        "\n## Variants and benchmarks\n", md(variants.round(2)),
        "\n## Random-entry benchmark\n", md(rnd.round(4)),
        "\n## Rulebook robustness grid\n", md(grid.round(2)),
        "\n## Sub-period CAGR %\n", md(subs.round(2)),
        "\n## How the rules were used (main run)\n", md(usage.round(2)),
        "\n## P&L by market (main run)\n", md(by_mkt.round(2)),
        "\n## Exit reasons (main run)\n", md(exits),
    ]
    (out / "step3.md").write_text("\n".join(report) + "\n")
    print((out / "step3.md").read_text())


if __name__ == "__main__":
    main()
