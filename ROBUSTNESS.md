# Robustness Review — SPY Turtle (stage 1)

Date: 2026-11-25 · Data: SPY daily 1993-01-29 → 2026-11-25 (dividend-adjusted) ·
Full tables: [`reports/robustness.md`](reports/robustness.md) (regenerate with
`python scripts/robustness.py data/SPY.csv`, ~20 s).

**No rules were changed.** Every test below uses the v1 engine audited in
`AUDIT.md` (stop-order fills, worst-case intrabar ordering, 2 bp slippage,
1x gross cap). Parameters were varied only to measure sensitivity, not to
pick new ones.

## Verdict

| Question | Long + short | Long-only |
|---|---|---|
| Profitable across sub-periods? | ❌ negative in 4 of 6 blocks (S1), 3 of 6 (S2) | ✅ positive in 5 of 6 blocks; only 2000–04 negative |
| Stable across parameters? | ❌ Sharpe > 0 in 13 of 27 cells, all ≤ 0.22 | ✅ Sharpe > 0 in 26 of 27 cells (0.25–0.67), a broad plateau |
| Survives higher costs? | ❌ already negative at 0 bp | ✅ still positive at 20 bp (Sharpe 0.24–0.38) |
| Does tuning parameters help out of sample? | ❌ | ❌ walk-forward is *worse* than fixed 20/10 |
| Better than random entries? | n/a | ✅ beats 96–97% (20/10) and 92–94% (55/20) of random-entry runs |

**Short version:** the long-only Turtle on SPY is robust but modest: about 3–4%
CAGR at 1x with roughly half of buy-and-hold's drawdown. The breakout timing
does add value beyond simply being long. The long+short version fails every
robustness check. That is evidence for your decision on the short side; I have
not changed the rules.

---

## A. Sub-periods

| Block | S1 L+S | S1 long | S2 L+S | S2 long | SPY B&H |
|---|---:|---:|---:|---:|---:|
| 1993–1999 | −4.5% | +43.9% | +31.9% | +86.8% | +281.7% |
| 2000–2004 | −39.9% | −17.2% | −40.1% | −12.7% | −10.5% |
| 2005–2009 | −25.5% | +15.3% | +1.6% | +19.6% | +2.5% |
| 2010–2014 | +9.8% | +29.0% | +15.1% | +32.5% | +100.7% |
| 2015–2019 | +4.3% | +28.7% | −4.8% | +11.3% | +72.9% |
| 2020–2026 | −5.7% | +40.5% | −7.6% | +30.1% | +141.1% |
| **Worst block DD** | −43.3% | −26.4% | −43.4% | −21.0% | −55.2% |

* Long-only beats buy-and-hold in only one block (2005–09, which contains
  2008). In 2000–04 it lost slightly *more* than SPY (−12.7% / −17.2% vs
  −10.5%), though with half the drawdown. It gives up most of the return
  in bull markets in exchange for much smaller drawdowns. It works as a
  drawdown-control tool, not as a way to earn more than the index.
* **2000–2004 is the weak spot for every variant.** The 2000–02 bear market
  was a grind full of sharp bear-market rallies, which whipsaws breakouts in
  both directions.
* By calendar year (1994+), long-only is positive in 67–70% of years with a
  worst year of −8.7% (S1) / −10.3% (S2). SPY: 82% positive, worst −36.8%.

## B. Channel-length grid (entry 10–120 days × exit ¼, ½, ¾ of entry)

* **Long-only is a plateau, not a peak.** 26 of 27 cells have positive Sharpe.
  Longer entries and slower exits are somewhat better (0.54–0.67 vs 0.45 for
  20/10), but the walk-forward test (E) shows this does not carry forward.
* **Long + short is negative or zero for every entry ≤ 40 days** and barely
  positive for very long channels (70–120 days, Sharpe ≤ 0.22). At 20 days it
  is negative for every exit length; at 55 days it is about zero (−0.13 to
  0.07).

## C. Stop distance × max units (long-only)

* **The 2N stop is not arbitrary on SPY.** 1N is clearly worse, especially
  with pyramiding (S1 1N/4 units: Sharpe 0.05, DD −51%). From ~3N up,
  results stop changing (3N = 4N = 8N) because the 10/20-day exit channel is
  always closer, so it is the exit channel that actually takes you out.
* **Pyramiding adds little under the 1x cap.** One unit is already ~70–100% of
  equity in notional, so units 2–4 are mostly clipped by the cap. For S1,
  1 unit has the best Sharpe (0.62, DD −16%) vs 4 units (0.51, DD −32%). For
  S2, 1 and 4 units tie on Sharpe (0.57), and 4 units gives the higher
  return. This interaction will change with options,
  where the cap becomes premium at risk.

## D. Costs

Each +5 bp per fill costs about 0.03–0.05 Sharpe for long-only.

| Slippage | S1 long Sharpe | S2 long Sharpe |
|---:|---:|---:|
| 0 bp | 0.54 | 0.59 |
| 2 bp | 0.51 | 0.57 |
| 10 bp | 0.40 | 0.48 |
| 20 bp | 0.24 | 0.38 |

⚠️ **For the options stage:** a 60–90 DTE SPY option spread is typically
1–5% of premium. Translated to the underlying through delta, that is usually
well above 20 bp per round trip. Cost modeling will decide whether the options
version keeps any edge, so it needs real bid/ask data.

## E. Walk-forward parameter selection

Rolling 8-year training window → next 2 years out of sample. In each window,
the parameter combination with the best training Sharpe (27 to choose from) is
traded out of sample.

| Long-only, 2002–2026 | CAGR | Max DD | Sharpe |
|---|---:|---:|---:|
| Walk-forward "best of grid" | 3.16% | −17.0% | 0.45 |
| Fixed 20/10 (original S1 lengths, no filter) | 3.51% | −21.3% | 0.48 |

The combination chosen in training ranked on average **15.8 of 27** out of
sample (16.5 with shorts). Chance alone gives 14. **Picking parameters by
past performance gives no skill on SPY**, so the differences in grid B are
noise. Recommendation: keep the original 20/10 and 55/20 lengths. Don't
optimize them.

## F. Random-entry benchmark (is the breakout better than luck?)

Same engine, exits, 2N stop, pyramiding, sizing and costs. Only the long entry
is replaced by a random daily coin flip, with the probability set so the trade
count is roughly the same. 200 random runs per rule set.

| Rules | Real CAGR / Sharpe | Random median CAGR / Sharpe | Random runs beating real Sharpe |
|---|---|---|---:|
| 20/10 | 3.39% / 0.45 | 1.94% / 0.27 | 3.0% |
| 55/20 | 4.18% / 0.57 | 2.60% / 0.37 | 6.5% |

Random entries with Turtle exits already make money on SPY, because of the
index's upward drift and the trend-following exits. The breakout entry adds
about +1.5% CAGR on top, which random runs rarely match. That is roughly
p ≈ 0.03–0.07: meaningful, but from a single instrument.

---

## Caveats

* Everything here is **in-sample** on one instrument (SPY), and the data are
  the same as in the audit. I ran about 300 configurations in total. The
  walk-forward (E) and random benchmark (F) are the most trustworthy results,
  because they are designed to resist that.
* Daily bars: 6% of S1 trades enter and stop on the same day and are resolved
  pessimistically. The results are more likely understated than overstated.
* Borrow costs on shorts are not modeled. Adding them would only make the
  long+short results worse.
* Returns are on a 1x cash basis with ~43% time in market. Uninvested cash
  earns nothing in the backtest; T-bill interest would add roughly 1–2% a
  year for long-only. This is not modeled yet and is worth adding.

## Decisions for you (not taken)

1. **Short side:** the evidence says drop SPY shorts in the share version. In
   the options stage, either long-only CALLs, or PUTs tested separately with
   real option prices. A PUT's limited loss changes the short-side math, so it
   deserves its own test.
2. **S1 vs S2:** both are robust long-only. S2 has fewer trades (lower cost
   exposure, which matters for options) and slightly better Sharpe; S1 has
   more positive years. Running both, as the Turtles did, is also an option.
3. **Cash yield:** add interest on idle cash to the simulator (small,
   mechanical change; doesn't touch signals).
