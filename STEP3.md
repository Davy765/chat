# Step 3 — Backtest of the "Modern Turtle" rulebook

Date: 2026-11-25 · Full tables: [`reports/step3.md`](reports/step3.md)
(regenerate: `python scripts/step3_modern.py`, ~5 min) · Engine:
`turtle_bot/portfolio.py` (multi-market, shared account; 28 tests).

**What was tested.** The rulebook page exactly as written, Track B (ETFs,
long or flat). It uses: N with an ATR(100) floor, a 50/200-day trend filter,
a close-confirmed 55-day breakout filled at the next open, 0.5% risk per
unit at a 3N stop, adds every 1N up to 3 units, a 3N trailing stop, a 20-day
channel exit, group, total and 6% heat caps, linear drawdown sizing and a 25%
drawdown pause. Where the page is ambiguous, the reading chosen is documented
in the engine's docstring.

**Setup.** 14 ETFs: SPY, QQQ, IWM, EFA, EEM (equity), TLT, IEF (bonds), GLD,
SLV, CPER (metals), USO, UNG (energy), DBA (agriculture), UUP (dollar). The
page's crypto ETFs started in 2024 and are excluded. Trading runs from
2008-01-02 to 2026-11-24 in a cash account (1x gross cap), with 2 bp slippage
and T-bill interest on idle cash. Zero-volume bars are dropped, and a market
can trade only with at least $1M median daily dollar volume, which keeps
CPER out until 2020.

## Verdict

**It passes all three pre-registered criteria** (committed in `1630801`
before the first run):

| Criterion | Result | Pass |
|---|---|:---:|
| P1: excess Sharpe over T-bills > 0 | 0.48 | ✅ |
| P2: ≤ 10% of 100 random-entry runs (all other rules kept) beat it | 4% | ✅ |
| P3: excess Sharpe > 0 in ≥ 7 of the page's 9 grid cells (40/55/80 days × 2.5/3/3.5N) | 9 of 9 (0.45–0.55) | ✅ |

This is the first positive result in the project. **At the portfolio level,
the breakout timing beats random entries.** That holds with the trend filter
(random median 0.26 vs 0.48) and without it (0.16 vs 0.48). The step 2
per-ETF tests mostly failed; with 14 diversified markets in one account,
there is now enough evidence to separate the signal from luck.

**Checks after the result:**
- Prices perturbed by ±0.0001% (20 runs): excess Sharpe 0.46–0.50.
- Data cut at the end of 2024: excess Sharpe 0.52, so the result isn't
  driven by the last two years.
- The last SLV trade exited before the unverified 2026-01-30 silver move.

## Results

| | CAGR | Max DD | MAR | Excess Sharpe | Worst year |
|---|---:|---:|---:|---:|---:|
| **Modern Turtle, cash account** | **4.94%** | **−12.1%** | **0.41** | **0.48** | **−4.5%** |
| Same rules, 2x margin | 5.70% | −17.1% | 0.33 | 0.49 | −7.0% |
| Same rules, 10 bp slippage | 3.84% | −16.4% | 0.24 | 0.34 | −6.4% |
| Original Turtle S2 long-only, same 14 ETFs | 3.72% | −8.7% | 0.43 | 0.49 | −3.0% |
| Equal-weight buy & hold, same 14 ETFs | 6.28% | −33.3% | 0.19 | 0.44 | −19.6% |
| SPY buy & hold | 10.83% | −51.9% | 0.21 | 0.55 | −18.2% |

19 years, 452 trades, 34% winners, average +0.38R per trade.

## What the results don't support

1. **The "twelve fixes" don't beat the original rules.** The original S2
   long-only rules on the same 14 ETFs have the same excess Sharpe (0.49 vs
   0.48) and a smaller drawdown (−8.7% vs −12.1%). The modern version earns
   more (4.9% vs 3.7% a year) by holding more exposure. It reshapes risk and
   return; it doesn't add edge.
2. **The page's individual fixes are about drawdown, not return.**
   Removing the trend filter gives excess Sharpe 0.49 (DD −17%), and removing
   the trailing stop gives 0.50 (DD −19.6%). Both rules cut drawdowns and
   neither improves risk-adjusted return.
3. **Several rules never came into play.** The 6% heat cap peaked at 5.2%
   (heat was ≥ 5% on 0.04% of days). The 25% drawdown pause never triggered.
   In a cash account, the binding limit is the **1x cash cap**: exposure was
   at the cap on 27% of days, and 222 entries were dropped for lack of cash.
4. **The profits are concentrated.** SPY and QQQ made +65% of initial
   capital, more than all other markets combined. **Without them**, excess
   Sharpe falls to 0.20, and 12.5% of random runs beat it, a narrow miss on
   P2. DBA, CPER, UUP and EFA lost money. Diversification steadies the ride
   more than it adds return.
5. **There are long flat stretches.** The strategy lost about 0.8% a year
   over 2013–2017 while SPY made 15% a year.
6. **It still trails SPY.** SPY bought and held had a higher excess Sharpe
   (0.55) and more than twice the return, but a drawdown about four times
   deeper. The strategy beats equal-weight buy and hold of the same ETFs on
   risk (0.48 vs 0.44, a third of the drawdown) but earns less.
7. **Hindsight risk.** The rulebook was written after 2008–2026 happened, by
   a model trained on that history, and this is the only period these ETFs
   have existed together. The pre-registered test guards against *my*
   tuning, not against rules designed with knowledge of the period.
   Paper trading is the only truly out-of-sample test left.

## Sub-period CAGR

| | 2008–12 | 2013–17 | 2018–22 | 2023–26 |
|---|---:|---:|---:|---:|
| Modern Turtle | 4.8% | −0.8% | 10.0% | 5.6% |
| Equal-weight buy & hold | 2.2% | 4.0% | 5.1% | 16.2% |
| SPY | 1.8% | 15.1% | 9.2% | 19.2% |

## Bottom line

A realistic expectation for this rulebook, if the future resembles
2008–2026: **about 4–6% a year with drawdowns around 10–15%**, roughly a
third of the time profitable trades, and multi-year flat stretches. It's a
genuine but modest trend-following edge, mostly from US equity indexes,
with much smaller drawdowns than holding stocks. It isn't a way to beat the
market's return.

## Decisions for you

1. **Paper trading.** This result justifies building the daily signal report
   (no broker, no orders) for the Modern rules, to track live for 3–6 months.
   That's the only test free of hindsight.
2. **Which version to paper-trade.** The original S2 long-only basket is
   equally good on risk-adjusted terms and simpler (fewer rules to execute
   correctly). The Modern version earns a bit more. Paper-trading both side by
   side would show which one you can actually run day to day.
3. **Options.** The edge is about 0.4–0.5 excess Sharpe in shares at 2 bp.
   At 10 bp it already drops to 0.34. Option spreads are far wider, so expect
   the options version to lose most of the edge. A dedicated cost test with
   real option data is needed before any decision.
