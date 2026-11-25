# Step 1 — Completing the original rules

Date: 2026-11-25 · Full tables: [`reports/step1.md`](reports/step1.md)
(regenerate: `python scripts/step1_compare.py data/SPY.csv data/DTB3.csv`).
Stats from 1994 (after warm-up) to 2026-11-24. Same costs and 1x cap as before.

> **Data note:** numbers were regenerated on the frozen data snapshot in
> `data/`, which ends at the last completed session. The first version
> included a still-forming 2026-11-25 bar. No conclusion changed. Full-period
> differences under ~0.3 CAGR points are within the numerical noise measured
> in ROBUSTNESS.md §G.

## What changed in the engine

| Addition | What it does | Default |
|---|---|---|
| **Drawdown sizing rule** (original Turtle rule) | For each full 10% drawdown below the reference equity, size new units on a notional account 20% smaller (−10% → 80%, −20% → 64%, …). | **on**, reference = equity at the start of each year |
| **S1 + S2 in one account** (`--system S1+S2`) | Both systems share cash, equity, the drawdown rule and the 1x gross cap. Each system sizes its units on its share of the account (`s1_weight`, default 50/50). | opt-in |
| **Interest on idle cash** | Idle equity (equity minus gross position value) earns the 3-month T-bill rate published the previous day, accrued by calendar days. | **on** in the scripts when `data/DTB3.csv` exists (`--no-cash-rate` turns it off) |
| **R-multiple fix** | 1R is now the 2N risk of one *full* unit. Before, it used the first fill, which the leverage cap can clip. That inflated R for capped trades (up to 3,401R in combined runs). | — |

**Verification:**
- With the new features off, the engine reproduces v1 **exactly**: equity to the cent and every trade, in 5 reference configurations. The robustness report regenerates byte-for-byte.
- 23 new tests (51 total). They recompute the drawdown sizing basis independently, check interest compounding and its one-day publication delay, check that interest is paid only on idle equity, and check the gross cap across both systems.
- The look-ahead truncation test and the accounting identity (equity = initial + closed P&L + open P&L + interest) pass with all features on.
- The R fix changes only the R column (on the earlier download, S1's average R 2000–2026 went from −0.09 to −0.10). The −2.52R worst gap loss is unchanged.

## Results (long-only shown; long + short is in the full report)

| Variant | CAGR | Max DD | MAR | Sharpe | Excess Sharpe* | Worst year |
|---|---:|---:|---:|---:|---:|---:|
| S1 v1 | 3.87% | −32.0% | 0.12 | 0.53 | 0.21 | −8.8% |
| S1 + drawdown rule | 3.76% | −32.0% | 0.12 | 0.52 | 0.20 | −8.7% |
| S1 + interest | 5.15% | −22.7% | 0.23 | 0.69 | 0.37 | −5.9% |
| **S1 complete** | **5.14%** | **−22.7%** | **0.23** | **0.69** | **0.37** | **−5.9%** |
| S2 v1 | 4.15% | −21.8% | 0.19 | 0.57 | 0.24 | −10.3% |
| **S2 complete** | **5.75%** | **−14.3%** | **0.40** | **0.76** | **0.44** | **−9.3%** |
| S1+S2 v1 settings | 3.65% | −27.7% | 0.13 | 0.50 | 0.18 | −7.6% |
| **S1+S2 complete** (50/50) | **5.18%** | **−18.7%** | **0.28** | **0.69** | **0.37** | **−5.4%** |
| SPY buy & hold | 10.61% | −55.2% | 0.19 | 0.63 | 0.50 | −36.8% |

\* Excess Sharpe = Sharpe of returns above the T-bill rate: the honest measure
once cash earns interest. Raw Sharpe flatters any strategy that sits in cash.

With shorts, every variant is still negative (S1+S2 complete: −1.62% CAGR,
−68% DD). Step 1 doesn't change the short-side conclusion.

## Findings

1. **The drawdown rule does almost nothing on SPY at 1x, and that reveals
   something important.** When in a trade, the bot holds ≥95% of equity on
   90% of days. The **1x cap, not the 1% risk rule, is what sizes positions**.
   A 20% smaller notional only means the add-on units hit the cap a little
   later. With yearly resets, the rule was active on just 1.3% of days
   long-only (7.9% with shorts).
   In the options stage, size will be limited by premium at risk instead of
   the cap. The 1%-per-N sizing and the drawdown rule will then actually
   drive results, so their correct implementation matters more there than
   here.
2. **Which reference the drawdown rule uses is noise.** Long-only, year-start,
   initial and peak are within 0.11 CAGR points of having no rule. With
   shorts they spread over about 1.2 points, which is inside that variant's
   noise band. There's no consistent winner. I kept year-start as the default. Note that I
   could not confirm from the published rules how often the Turtles reset
   their reference, so treat this as an interpretation.
3. **Interest on cash is the largest effect, and it's real money, not an
   edge.** It adds about +1.3–1.6% CAGR and accounts for about 25% of
   long-only profit. It also cuts the worst drawdown by about 9 points: the
   worst drawdown lasted 5.5 years (Jan 1999 → Jun 2004), while the bot was
   mostly in cash earning T-bills. The excess Sharpe (0.37–0.44) shows the
   trading itself still beats cash, but less than buy and hold (0.50).
4. **S1+S2 together doesn't beat S2 alone on SPY.** Both systems trade the
   same market, so they're highly correlated and compete for the same 1x
   cap. The S1/S2 split barely matters (25% S1: MAR 0.33; 50%: 0.28; 75%:
   0.22). The benefit the Turtles got from running both came from many
   markets, which we haven't tested yet (step 2).
5. **Best complete version on SPY: S2 long-only.** 5.75% CAGR, −14.3% max DD,
   MAR 0.40 (buy and hold: 0.19). It still trails buy and hold on return
   (10.6%) and on excess Sharpe (0.44 vs 0.50). It offers a much smoother
   ride, not more money.

## Sub-period CAGR, complete rules, long-only

| | 1994–99 | 2000–04 | 2005–09 | 2010–14 | 2015–19 | 2020–26 |
|---|---:|---:|---:|---:|---:|---:|
| S1 | 9.2% | −1.8% | 4.5% | 5.3% | 5.7% | 6.7% |
| S2 | 14.8% | −0.5% | 5.6% | 5.7% | 2.8% | 5.0% |
| S1+S2 | 11.7% | −1.3% | 5.2% | 3.4% | 5.4% | 5.5% |
| SPY | 23.4% | −2.2% | 0.5% | 15.0% | 11.6% | 13.8% |

## Decisions taken

- **Defaults:** the drawdown rule is on by default (original rule). Interest
  is on in the scripts whenever `data/DTB3.csv` exists.
- **Reference system:** S1+S2 complete, until the multi-ETF test (step 2)
  shows whether combining helps across markets. S2 long-only is the
  strongest on SPY alone.
