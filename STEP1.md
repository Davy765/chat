# Step 1 — Completing the original rules

Date: 2026-11-25 · Full tables: [`reports/step1.md`](reports/step1.md)
(regenerate: `python scripts/step1_compare.py data/SPY.csv data/DTB3.csv`).
Stats from 1994 (after warm-up) to 2026-11-25. Same costs and 1x cap as before.

## What changed in the engine

| Addition | What it does | Default |
|---|---|---|
| **Drawdown sizing rule** (original Turtle rule) | For each full 10% drawdown below the reference equity, size new units on a notional account 20% smaller (−10% → 80%, −20% → 64%, …). | **on**, reference = equity at the start of each year |
| **S1 + S2 in one account** (`--system S1+S2`) | Both systems share cash, equity, the drawdown rule and the 1x gross cap. Each system sizes its units on its share of the account (`s1_weight`, default 50/50). | opt-in |
| **Interest on idle cash** (`--cash-rate data/DTB3.csv`) | Idle equity (equity minus gross position value) earns the 3-month T-bill rate published the previous day, accrued by calendar days. | opt-in (needs the rate file) |
| **R-multiple fix** | 1R is now the 2N risk of one *full* unit. Before, it used the first fill, which the leverage cap can clip. That inflated R for capped trades (up to 3,401R in combined runs). | — |

**Verification:**
- With the new features off, the engine reproduces v1 **exactly**: equity to the cent and every trade, in 5 reference configurations. The robustness report regenerates byte-for-byte.
- 23 new tests (51 total). They recompute the drawdown sizing basis independently, check interest compounding and its one-day publication delay, check that interest is paid only on idle equity, and check the gross cap across both systems.
- The look-ahead truncation test and the accounting identity (equity = initial + closed P&L + open P&L + interest) pass with all features on.
- The R fix changes only the R column: S1 average R 2000–2026 goes from −0.09 to −0.10. The −2.52R worst gap loss is unchanged.

## Results (long-only shown; long + short is in the full report)

| Variant | CAGR | Max DD | MAR | Sharpe | Excess Sharpe* | Worst year |
|---|---:|---:|---:|---:|---:|---:|
| S1 v1 | 3.71% | −32.0% | 0.12 | 0.51 | 0.19 | −8.7% |
| S1 + drawdown rule | 3.71% | −31.9% | 0.12 | 0.51 | 0.19 | −8.6% |
| S1 + interest | 5.14% | −22.7% | 0.23 | 0.69 | 0.37 | −5.9% |
| **S1 complete** | **5.14%** | **−22.7%** | **0.23** | **0.69** | **0.37** | **−6.0%** |
| S2 v1 | 4.18% | −21.8% | 0.19 | 0.57 | 0.25 | −10.3% |
| **S2 complete** | **5.71%** | **−14.7%** | **0.39** | **0.75** | **0.43** | **−9.3%** |
| S1+S2 v1 settings | 3.55% | −27.7% | 0.13 | 0.49 | 0.17 | −7.6% |
| **S1+S2 complete** (50/50) | **5.23%** | **−18.7%** | **0.28** | **0.69** | **0.38** | **−5.4%** |
| SPY buy & hold | 10.58% | −55.2% | 0.19 | 0.63 | 0.50 | −36.8% |

\* Excess Sharpe = Sharpe of returns above the T-bill rate: the honest measure
once cash earns interest. Raw Sharpe flatters any strategy that sits in cash.

With shorts, every variant is still negative (S1+S2 complete: −1.55% CAGR,
−67% DD). Step 1 doesn't change the short-side conclusion.

## Findings

1. **The drawdown rule does almost nothing on SPY at 1x, and that reveals
   something important.** When in a trade, the bot holds ≥95% of equity on
   90% of days. The **1x cap, not the 1% risk rule, is what sizes positions**.
   A 20% smaller notional only means the add-on units hit the cap a little
   later. With yearly resets, the rule was active on just 1.3% of days
   long-only (8.1% with shorts).
   In the options stage, size will be limited by premium at risk instead of
   the cap. The 1%-per-N sizing and the drawdown rule will then actually
   drive results, so their correct implementation matters more there than
   here.
2. **Which reference the drawdown rule uses is mostly noise.** Year-start,
   initial and peak are within ±0.7% CAGR with shorts and ±0.05% long-only,
   with no consistent winner. I kept year-start as the default. Note that I
   could not confirm from the published rules how often the Turtles reset
   their reference, so treat this as an interpretation.
3. **Interest on cash is the largest effect, and it's real money, not an
   edge.** It adds about +1.4–1.5% CAGR and accounts for about 25% of
   long-only profit. It also cuts the worst drawdown by about 9 points: the
   worst drawdown lasted 5.5 years (Jan 1999 → Jun 2004), while the bot was
   mostly in cash earning T-bills. The excess Sharpe (0.37–0.43) shows the
   trading itself still beats cash, but less than buy and hold (0.50).
4. **S1+S2 together doesn't beat S2 alone on SPY.** Both systems trade the
   same market, so they're highly correlated and compete for the same 1x
   cap. The S1/S2 split barely matters (25% S1: MAR 0.33; 50%: 0.28; 75%:
   0.21). The benefit the Turtles got from running both came from many
   markets, which we haven't tested yet (step 2).
5. **Best complete version on SPY: S2 long-only.** 5.71% CAGR, −14.7% max DD,
   MAR 0.39 (buy and hold: 0.19). It still trails buy and hold on return
   (10.6%) and on excess Sharpe (0.43 vs 0.50). It offers a much smoother
   ride, not more money.

## Sub-period CAGR, complete rules, long-only

| | 1994–99 | 2000–04 | 2005–09 | 2010–14 | 2015–19 | 2020–26 |
|---|---:|---:|---:|---:|---:|---:|
| S1 | 9.5% | −1.8% | 4.1% | 5.3% | 5.7% | 6.7% |
| S2 | 14.8% | −0.5% | 5.1% | 5.8% | 2.7% | 5.2% |
| S1+S2 | 11.9% | −1.3% | 4.8% | 3.4% | 5.4% | 5.9% |
| SPY | 23.4% | −2.2% | 0.5% | 15.0% | 11.6% | 13.6% |

## Decisions for you

- **Defaults:** the drawdown rule is now on by default (it's an original
  rule). Interest needs `--cash-rate`. Should interest also be on by default
  when `data/DTB3.csv` exists?
- **System choice:** S2 long-only is the strongest on SPY alone. I'd keep
  S1+S2 as the reference system until the multi-ETF test (step 2) shows
  whether combining helps across markets.
- **Next:** step 2, the multi-ETF out-of-sample test with these complete
  rules and no retuning.
