# Robustness Review — SPY Turtle (stage 1)

Date: 2026-11-25 · Data: SPY daily 1993-01-29 → 2026-11-24, dividend-adjusted,
frozen snapshot in `data/` · Full tables:
[`reports/robustness.md`](reports/robustness.md) (regenerate with
`python scripts/robustness.py data/SPY.csv`, ~1 min).

> **Settings:** these figures use the v1 engine settings: no drawdown rule and
> no interest on cash (`drawdown_rule=False`, which the script pins). Step 1
> results with the complete rules are in STEP1.md.
>
> **Data note:** the first version of this review used a download that
> included a still-forming bar for 2026-11-25. All numbers were regenerated
> on the frozen snapshot that ends at the last completed session. No
> conclusion changed (see section G for why individual numbers can move).

**No rules were changed.** Every test below uses the v1 engine audited in
`AUDIT.md` (stop-order fills, worst-case intrabar ordering, 2 bp slippage,
1x gross cap). Parameters were varied only to measure sensitivity, not to
pick new ones.

## Verdict

| Question | Long + short | Long-only |
|---|---|---|
| Profitable across sub-periods? | ❌ negative in 3 of 6 blocks (S1 and S2); block results swing with tiny data changes (G) | ✅ positive in 5 of 6 blocks; only 2000–04 negative |
| Stable across parameters? | ❌ Sharpe > 0 in 11 of 27 cells, all ≤ 0.22 | ✅ Sharpe > 0 in 26 of 27 cells (0.26–0.67), a broad plateau |
| Survives higher costs? | ❌ already about zero or negative at 0 bp | ✅ still positive at 20 bp (Sharpe 0.25–0.41) |
| Does tuning parameters help out of sample? | ❌ | ❌ walk-forward is no better than fixed 20/10 |
| Better than random entries? | n/a | ✅ beats 96% (20/10) and 93% (55/20) of random-entry runs |

**Short version:** the long-only Turtle on SPY is robust but modest: about 3–4%
CAGR at 1x with roughly half of buy-and-hold's drawdown. The breakout timing
does add value beyond simply being long. The long+short version fails every
robustness check. That is evidence for your decision on the short side; I have
not changed the rules.

---

## A. Sub-periods

| Block | S1 L+S | S1 long | S2 L+S | S2 long | SPY B&H |
|---|---:|---:|---:|---:|---:|
| 1993–1999 | −7.7% | +45.9% | +29.3% | +86.8% | +281.7% |
| 2000–2004 | −39.9% | −17.2% | −40.9% | −12.7% | −10.5% |
| 2005–2009 | −25.5% | +17.4% | +4.4% | +21.1% | +2.5% |
| 2010–2014 | +10.1% | +29.0% | +15.9% | +32.5% | +100.7% |
| 2015–2019 | +7.1% | +33.2% | −4.8% | +11.2% | +72.9% |
| 2020–2026 | +14.9% | +40.5% | −14.6% | +27.4% | +143.3% |
| **Worst block DD** | −43.3% | −26.4% | −44.2% | −21.0% | −55.2% |

* Long-only beats buy-and-hold in only one block (2005–09, which contains
  2008). In 2000–04 it lost slightly *more* than SPY (−12.7% / −17.2% vs
  −10.5%), though with half the drawdown. It gives up most of the return
  in bull markets in exchange for much smaller drawdowns. It works as a
  drawdown-control tool, not as a way to earn more than the index.
* **2000–2004 is the weak spot for every variant.** The 2000–02 bear market
  was a grind full of sharp bear-market rallies, which whipsaws breakouts in
  both directions.
* **Long+short block results are fragile.** S1 L+S 2020–26 was −5.7% on the
  earlier download and +14.9% on this snapshot, from price differences
  smaller than 0.0001% (see G). Long-only blocks barely moved.
* By calendar year (1994+), long-only is positive in 67–70% of years with a
  worst year of −8.8% (S1) / −10.3% (S2). SPY: 82% positive, worst −36.8%.

## B. Channel-length grid (entry 10–120 days × exit ¼, ½, ¾ of entry)

* **Long-only is a plateau, not a peak.** 26 of 27 cells have positive Sharpe.
  Longer entries and slower exits are somewhat better (0.56–0.67 vs 0.44 for
  20/10), but the walk-forward test (E) shows this does not carry forward.
* **Long + short is negative or about zero for every entry ≤ 55 days**
  (best: 0.09) and barely positive for very long channels (70–120 days,
  Sharpe ≤ 0.22). At 20 days it is negative for every exit length.

## C. Stop distance × max units (long-only)

* **The 2N stop is not arbitrary on SPY.** 1N is clearly worse, especially
  with pyramiding (S1 1N/4 units: Sharpe 0.08, DD −53%). From ~3N up,
  results barely change (3N ≈ 4N ≈ 8N) because the 10/20-day exit channel is
  almost always closer, so it is the exit channel that actually takes you out.
* **Pyramiding adds little under the 1x cap.** One unit is already ~70–100% of
  equity in notional, so units 2–4 are mostly clipped by the cap. For S1,
  1 unit has the best Sharpe (0.63, DD −16%) vs 4 units (0.53, DD −32%). For
  S2, 1 and 4 units are about tied on Sharpe (0.58 vs 0.57), and 4 units
  gives the higher return. This interaction will change with options, where
  the cap becomes premium at risk.

## D. Costs

Each +5 bp per fill costs about 0.03–0.06 Sharpe for long-only.

| Slippage | S1 long Sharpe | S2 long Sharpe |
|---:|---:|---:|
| 0 bp | 0.54 | 0.59 |
| 2 bp | 0.53 | 0.57 |
| 10 bp | 0.41 | 0.48 |
| 20 bp | 0.25 | 0.41 |

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
| Walk-forward "best of grid" | 3.14% | −17.6% | 0.45 |
| Fixed 20/10 (original S1 lengths, no filter) | 3.37% | −21.4% | 0.46 |

The combination chosen in training ranked on average **16.3 of 27** out of
sample (16.4 with shorts). Chance alone gives 14. **Picking parameters by
past performance gives no skill on SPY**, so the differences in grid B are
noise. Recommendation: keep the original 20/10 and 55/20 lengths. Don't
optimize them.

## F. Random-entry benchmark (is the breakout better than luck?)

Same engine, exits, 2N stop, pyramiding, sizing and costs. Only the long entry
is replaced by a random daily coin flip, with the probability set so the trade
count is roughly the same. 200 random runs per rule set.

| Rules | Real CAGR / Sharpe | Random median CAGR / Sharpe | Random runs beating real Sharpe |
|---|---|---|---:|
| 20/10 | 3.30% / 0.44 | 1.91% / 0.27 | 4.0% |
| 55/20 | 4.15% / 0.57 | 2.63% / 0.37 | 7.0% |

Random entries with Turtle exits already make money on SPY, because of the
index's upward drift and the trend-following exits. The breakout entry adds
about +1.4–1.5% CAGR on top, which random runs rarely match. That is roughly
p ≈ 0.04–0.07: meaningful, but from a single instrument.

## G. Numerical path noise (how much can a single result move for no reason?)

Every price is multiplied by a random factor within 1 ± 0.000001 (50 runs).
That's smaller than the change between two downloads of the same data.
Because units are whole shares, a one-share difference early on changes
equity, which changes later sizes and sometimes later trades.

| Variant | Snapshot CAGR | 5th–95th percentile | Max DD range |
|---|---:|---:|---:|
| S1 long+short | −1.56% | −2.54% to −1.58% | −72.2% to −71.0% |
| S1 long-only | 3.87% | 3.66% to 3.89% | −32.0% to −31.6% |
| S2 long+short | −0.78% | −0.83% to −0.51% | −57.9% to −56.4% |
| S2 long-only | 4.15% | 4.15% to 4.35% | −21.8% to −21.8% |

* **Full-period differences under ~0.3 CAGR points are noise.** Compare
  variants only when the gap is larger than that.
* The S1 long+short snapshot result (−1.56%) is slightly *better* than 95% of
  the perturbed runs. The typical value is about −2.4%.
* Sub-period results are noisier than full-period ones: a short block holds
  fewer trades, so one changed trade moves it more.

---

## Caveats

* Everything here is **in-sample** on one instrument (SPY). I ran about 300
  configurations in total. The walk-forward (E) and random benchmark (F) are
  the most trustworthy results, because they are designed to resist that.
* Daily bars: about 6% of S1 trades enter and stop on the same day and are
  resolved pessimistically. The results are more likely understated than
  overstated.
* Borrow costs on shorts are not modeled. Adding them would only make the
  long+short results worse.
* Returns are on a 1x cash basis with ~43% time in market, and idle cash earns
  nothing here. Step 1 adds T-bill interest (STEP1.md).

## Decisions for you (not taken)

1. **Short side:** the evidence says drop SPY shorts in the share version. In
   the options stage, either long-only CALLs, or PUTs tested separately with
   real option prices. A PUT's limited loss changes the short-side math, so it
   deserves its own test.
2. **S1 vs S2:** both are robust long-only. S2 has fewer trades (lower cost
   exposure, which matters for options) and slightly better Sharpe; S1 has
   more positive years. Running both, as the Turtles did, is also an option.
