# Technical Audit — Turtle Trading Bot (SPY, stage 1: underlying only)

Date: 2026-11-25 · Scope: signals, N/ATR, stops, look-ahead, simulator realism ·
No broker connectivity exists or was added. Nothing here places orders.

> **Note (step 1):** the figures in this document were produced by the v1
> engine settings: no drawdown rule and no interest on cash. They can be
> reproduced with `drawdown_rule=False` (`--no-dd-rule`). Equity and trades
> are unchanged by later versions. One exception: R-multiples were later
> redefined against a full unit (see STEP1.md), which moves S1's average R
> from −0.09 to −0.10. The −2.52R worst case is unchanged.

## 0. Status of the code under review

The repository contained **no bot code** when the audit started (only an empty
`README.md`, one commit, no other branches). There was nothing to audit line by
line, so this stage delivers:

1. **A reference implementation (v1)** in `turtle_bot/`, written to the
   original Turtle rules, with every timing decision documented.
2. **A test suite** (`tests/test_turtle.py`, 28 tests) that checks each of the
   five points you asked about. The look-ahead and accounting tests only use the
   public result (equity curve and trades), so they can be adapted to run
   against your own engine once you push it.
3. **A bug-impact experiment** (`scripts/inflation_demo.py`) that turns on
   common first-draft simulator bugs one at a time on real SPY data, so each
   one has a measured effect.
4. **The checklist below**, classified by severity, to apply to your code.

When you push your version, I will run the same checks against it and replace
section 3 with line-level findings.

---

## 1. Answers to your five questions (for the v1 in this repo)

| # | Question | Verdict | Evidence |
|---|----------|---------|----------|
| 1 | Donchian entries/exits correct | ✅ | Channel = max High / min Low of the **previous** L bars (`shift(1)`). Entry is a stop order at the channel and needs a **strict** break (`High > channel`). Gaps fill at the open. Exit = 10-day (S1) / 20-day (S2) opposite channel, also a stop order. Tests: `test_donchian_excludes_current_bar`, `test_long_entry_fills_at_breakout_level_not_close`, `test_gap_entry_fills_at_open`, `test_no_entry_when_high_only_equals_channel`, `test_exit_channel_10_day_low` |
| 2 | ATR (N) and 2N stops correct | ✅ | N = Wilder 20-day ATR, `N_t = (19·N_{t-1} + TR_t)/20`, seeded with the mean of the first 20 TRs. TR uses the previous close. The stop is 2N from the **latest unit's fill**, using the N frozen at the first entry. Adding a unit moves the stop for **all** units. Tests: `test_wilder_atr_matches_hand_calculation`, `test_true_range_uses_previous_close`, `test_stop_is_two_n_below_entry_and_fills_at_stop` (R = −1.00 exactly), `test_pyramid_raises_stop_for_all_units` |
| 3 | No look-ahead bias | ✅ | (a) **Truncation test**: running on data cut at day k gives a bit-identical equity curve and trade list up to k (6 cases, S1 and S2). (b) **Future-shock test**: halving every price after day 500 changes nothing before day 500. Sizing uses N and equity as of the previous close. |
| 4 | Simulator not inflating results | ✅ (pessimistic by design) | Gap fills at the open, so losses can exceed 1R (real SPY minimum: **−2.52R**). Unknown intrabar order is resolved against us. Slippage applies on every fill. Gross notional is capped. An accounting identity is tested (final equity = initial + closed P&L + open P&L). On driftless random walks the strategy shows no edge. A second, independent implementation (`inflation_demo.py`, "correct" row) reproduces the engine's return exactly (+6.34% vs +6.34%). |
| 5 | Errors and fixes | see §2–§3 | — |

---

## 2. Measured impact of common simulator bugs (real SPY, 2000-01 → 2026-11)

Simplified single-unit Turtle (20/10, 2N stop, long and short, 1% risk, 1x
cap, 2 bp slippage). `python scripts/inflation_demo.py data/SPY.csv`

| Variant | Total return | CAGR | Max DD |
|---|---:|---:|---:|
| **correct** | **+6.3%** | **0.23%** | **−27.7%** |
| channel includes today's bar | 0.0% (never trades) | 0.00% | 0.0% |
| signal on close, filled at intraday breakout level | **+137.3%** | 3.27% | −13.5% |
| N of today used for today's size/stop | +9.5% | 0.34% | −26.8% |
| simple-average ATR instead of Wilder | +2.6% | 0.10% | −28.7% |
| stop fills ignore gaps (always at stop price) | +27.7% | 0.91% | −24.0% |
| optimistic intrabar order (entry bar never stops out) | +18.7% | 0.64% | −26.7% |
| no costs | +17.1% | 0.59% | −25.2% |
| **all of the above (except the dead-channel bug)** | **+215.4%** | **4.36%** | **−12.9%** |

All the bugs together turn a strategy that roughly breaks even into one that
looks like +4.4% a year with half the drawdown. This is why each item below is
classified the way it is.

---

## 3. Defect checklist by severity (apply to your code)

### 🔴 CRITICAL: invalidates results

| ID | Defect | How to detect | Fix |
|---|---|---|---|
| C1 | **Donchian channel includes the current bar** (`rolling(20).max()` without `shift(1)`). With `High > channel` it never triggers; with `Close > channel` it never triggers either. With `>=`, the bar "breaks out" against itself. | Strategy trades rarely or never; or every new high is an entry. | `high.rolling(L).max().shift(1)` (see `indicators.donchian`). |
| C2 | **Close-confirmed signal filled at the breakout price.** Signal `Close > 20d high` but entry price = the 20-day high. This uses the close (future information within the bar) to select only the breakouts that held, while paying the intraday price. **+137% vs +6% in §2.** | Entry prices are always exactly the channel value, and no failed intraday breakouts appear in the log. | Choose one consistent model: (a) intraday stop order: trigger on `High > channel`, fill at `max(channel, Open)`; or (b) close signal: trigger on `Close > channel`, fill at the **next open**. v1 uses (a), which is the original Turtle method. |
| C3 | **N (ATR) of day t used to size or set stops for an entry on day t.** TR_t needs High_t, Low_t and Close_t, which are unknown at entry time. | Compare `N[i]` vs `N[i-1]` at the sizing call. | Use `N[i-1]` (v1: `n_prev`). |
| C4 | **Mixed adjustment basis**: adjusted Close with unadjusted High/Low (e.g. yfinance `Adj Close` next to raw `High`). This creates false gaps and breakouts on every ex-dividend date, and false signals around splits in older data. | Plot Close vs High: Close above High on some days. | Scale O/H/L/C by `Adj Close / Close` (`data.adjust_ohlc`). `validate_ohlc` rejects Close > High. |
| C5 | **Stops filled at the stop price on gap days.** This caps every loss at −1R; real gaps give up to −2.5R on SPY. +27.7% vs +6.3%. | `min(r_multiple)` is never below −1.0. | If Open is already beyond the stop, fill at Open (`*_gap` exit reasons in v1). |
| C6 | **Sizing from equity that includes today's P&L**, or from final equity, or from a constant "initial capital" while reporting compounded returns. | Equity used in sizing ≠ previous close equity. | Size with equity at the previous close (v1: `prev_equity`). |

### 🟠 HIGH: materially biased results

| ID | Defect | Fix |
|---|---|---|
| H1 | **Optimistic intrabar ordering**: on the entry bar (or a pyramid bar), the simulator assumes the high came before the low. +18.7% in §2. | Assume the worst order (v1). Report how many trades depend on it (`same_bar_stop_trades`: 18 of 289 on SPY S1). Resolve with intraday data later. |
| H2 | **No transaction costs or slippage.** +17.1% in §2. | v1 default: 2 bp per side on SPY. **For options this must be replaced with real bid/ask**; spreads are 1–5% of premium, roughly 100x larger. |
| H3 | **Unbounded leverage.** On SPY, N ≈ 1–1.5% of price, so one 1%-risk unit ≈ 70–100% of equity. Four units ≈ 3–4x notional. Futures margin made this normal for the Turtles; a cash or Reg-T equity account cannot do it. | **Deviation, explained**: `max_leverage=1.0` by default. This is a risk constraint, not a signal change. Set it higher to see the uncapped rules (table §4). |
| H4 | **Wrong N formula**: SMA of TR, or `ewm(span=20)` (alpha = 2/21), instead of Wilder (alpha = 1/20). | `indicators.wilder_atr`, tested against a hand-rolled loop. |
| H5 | **Pyramiding errors**: adds triggered from the signal price instead of the previous **fill**; stop not raised for earlier units; adds sized with a new N. | v1: next add = last fill ± ½N_entry; all units' stop = last fill ∓ 2N_entry; unit size fixed at entry. |
| H6 | **S1 skip filter based only on trades actually taken**, or filter without the 55-day failsafe. The original rule looks at the *last breakout, whether taken or not*, and uses a 55-day failsafe so big trends aren't missed. | v1 runs a shadow single-unit trade on every 20-day breakout (`_Hypothetical`) and enters on the 55-day failsafe when the filter blocks. |
| H7 | **Performance from closed trades only**: open drawdowns are invisible and max DD is understated. | v1 marks to market daily; metrics come from the daily equity curve. |

### 🟡 MEDIUM

| ID | Defect | Fix |
|---|---|---|
| M1 | A bar that breaks both channels (outside bar) is resolved arbitrarily. | v1 skips it and logs it. |
| M2 | Re-entry on the same bar as an exit (impossible to order with daily data). | v1 waits for the next bar. |
| M3 | `>=` instead of `>` for the breakout (ties trigger trades the rule doesn't). | Strict comparison, with a test. |
| M4 | Fractional shares inflate the precision of small accounts. | Whole shares by default (`allow_fractional=False`). |
| M5 | Short-side costs: dividends owed on short SPY and borrow fees. Adjusted prices already charge the dividend to the short; borrow fees are **not** modeled. | Accept for stage 1; it disappears when shorts become PUTs. |
| M6 | Single in-sample run with fixed parameters is presented as evidence. | Add walk-forward, sub-period reports and a parameter-stability grid (stage 2). |

### 🟢 LOW

| ID | Defect | Fix |
|---|---|---|
| L1 | No input validation (unsorted dates, NaN, duplicates, OHLC inconsistencies). | `data.validate_ohlc`. |
| L2 | Warm-up bars counted in CAGR or exposure. | Equity is flat before indicators exist; report the start date explicitly. |
| L3 | Magic numbers scattered through the code. | All parameters live in `TurtleConfig`, and every deviation is labeled there. |

---

## 4. What v1 shows about the strategy on SPY (no rules changed)

2000-01-03 → 2026-11-25, 2 bp slippage, whole shares. SPY buy and hold over
the same period: +683% (max DD −55%).

| System | Shorts | Leverage cap | Total return | CAGR | Max DD | Sharpe | Trades | Win % | PF |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| S1 | yes | 1x | −48.8% | −2.46% | −58.7% | −0.14 | 289 | 34.3 | 0.74 |
| S1 | yes | 4x | −93.4% | −9.63% | −94.0% | −0.25 | 302 | 23.2 | 0.57 |
| S1 | no | 1x | +122.5% | 3.02% | −26.4% | 0.44 | 153 | 44.4 | 1.86 |
| S1 | no | 4x | +191.2% | 4.05% | −40.9% | 0.31 | 160 | 33.8 | 1.49 |
| S2 | yes | 1x | −34.5% | −1.56% | −50.3% | −0.07 | 190 | 26.8 | 0.79 |
| S2 | yes | 4x | −78.2% | −5.50% | −85.0% | −0.09 | 193 | 20.2 | 0.64 |
| S2 | no | 1x | +105.2% | 2.71% | −21.6% | 0.40 | 98 | 40.8 | 1.86 |
| S2 | no | 4x | +314.8% | 5.43% | −39.0% | 0.38 | 102 | 30.4 | 1.71 |

Observations (reported, **not acted on**; changing the rules is your decision):

* **The short side is the problem.** S1 1x: longs +$23.9k over 154 trades,
  shorts −$73.9k over 135 trades. SPY has a strong upward drift and sharp
  V-shaped recoveries, which is the worst case for a 20-day short breakout.
  The Turtles traded a diversified futures portfolio, where a single
  instrument's drift averaged out; on one equity index it does not.
* Even long-only, the strategy lags buy and hold in total return. It does so
  with about half the drawdown and ~43% market exposure.
* 6% of S1 trades have entry and stop on the same daily bar, and their outcome
  depends on an assumption (we assume the worst). Intraday data would remove
  this uncertainty.
* These numbers are in-sample, on one instrument, with default parameters.
  Treat them as a correctness baseline, not as evidence of an edge.

Possible stage-2 discussion points (not implemented): long-only, or shorts
only through defined-risk PUTs; a trend filter (e.g. 200-day) for the short
side; volatility-regime filters. Each is a change to the original rules and
must be tested out of sample.

---

## 5. Notes for the options stage (CALL/PUT, 60–90 DTE)

* **Keep the signal on the underlying.** Donchian channels and N should stay on
  SPY prices. Option prices are discontinuous across strikes and expiries and
  make poor channel inputs.
* **Strikes from unadjusted prices.** v1 uses dividend-adjusted history, which
  is right for signals but wrong for choosing strikes in historical tests.
* **Sizing changes meaning.** "1% risk per N" becomes a delta-adjusted
  exposure (`contracts ≈ unit_shares / (100 · delta)`) **capped by premium at
  risk**. A long option can lose 100% of its premium, and this cap replaces the
  leverage cap.
* **Stops.** Decide whether the 2N stop stays on the underlying (recommended,
  keeps the rule intact) or moves to option P&L. Exits fill at the option's
  **bid**, not mid.
* **Costs.** Model each fill at bid/ask (or mid ± a fraction of the spread).
  The 2 bp equity slippage is meaningless for options.
* **Rolling.** 60–90 DTE at entry; define a roll rule (e.g. roll at 21–30 DTE,
  or when delta leaves a band) and charge its costs.
* **Data.** Needs historical end-of-day option chains with bid/ask, IV and
  greeks (e.g. CBOE DataShop, ORATS, OptionMetrics, Polygon). Check the point
  in time of each quote, because end-of-day quotes are often taken at 16:00
  while SPY closes at 16:00 and options trade until 16:15.

---

## 6. How to reproduce

```bash
pip install -r requirements.txt
python scripts/download_spy.py                # read-only market data
python -m pytest -q                           # 28 tests
python scripts/run_backtest.py data/SPY.csv --system S1 --start 2000-01-01
python scripts/inflation_demo.py data/SPY.csv
```
