# Step 4 — Optimization, done so the result can be trusted

Date: 2026-11-25 · Full tables: [`reports/step4.md`](reports/step4.md)
(regenerate: `python scripts/step4_optimize.py`, ~1 min) · Procedure
committed before any run: `6816729`.

## Why it was done this way

Earlier tests showed that picking parameters because they looked best on
past data gives **no** improvement afterwards (ROBUSTNESS.md §E). So:

1. Six candidates were chosen in advance, each aimed at a problem already
   measured. No open-ended search.
2. **Selection used 2008–2017 only.** Later data was cut off, so it couldn't
   influence the choice.
3. The chosen candidate was tested **once** on the **2018–2026 hold-out**.
   It would only be adopted if it beat the current rules there by at least
   0.05 excess Sharpe.

## Result: keep the rulebook as written

| Step | Result |
|---|---|
| Selection (2008–2017) | **C5, original S2 long-only**: excess Sharpe 0.39 vs the rulebook's 0.28 |
| Hold-out (2018–2026) | C5 **0.55** vs rulebook **0.68**: worse by 0.13 |
| **Decision** | **No change.** The best in-sample candidate failed out of sample. |

| Candidate | 2008–17 excess Sharpe | 2018–26 CAGR | 2018–26 max DD | 2018–26 excess Sharpe |
|---|---:|---:|---:|---:|
| C0 rulebook (current) | 0.28 | 7.99% | −6.7% | 0.68 |
| C1 + 20% per-market cap | 0.31 | 9.53% | −5.1% | 1.00 |
| C2 + 33% per-market cap | 0.32 | 8.58% | −5.9% | 0.77 |
| C3 ensemble 40/55/80 days | 0.28 | 7.34% | −7.2% | 0.62 |
| C4 ensemble + 20% cap | 0.30 | 9.01% | −5.0% | 0.94 |
| C5 original S2 long-only | **0.39** | 5.49% | −8.0% | 0.55 |

**A bug I found and fixed.** The first run reported "adopt C2" (the 33%
cap). That came from a bug in the C5 code: CPER is never liquid before
2018, and the code turned that into gaps in C5's equity curve, which
lowered its 2008–2017 score. With the bug fixed, C5 becomes the
in-sample winner and then fails the hold-out. The fix changed code, not
rules, and both runs are described here. It shows how easily a
plausible-looking "improvement" can be produced by accident.

**A second correction.** When the 20% cap was added to the paper report, it
showed an "add" order the cap would have blocked at the fill. The engine now
applies the cap when it approves an order after the close, so the printed
orders, heat and unit counts match what can fill. That moved the cap
candidates' numbers slightly (C1: 0.29 → 0.31 in-sample, 1.04 → 1.00
hold-out). The decision is unchanged. The figures above are after the fix.

## What the results suggest (not adopted)

- **Per-market caps (C1, C4) did best on 2018–2026,** with excess Sharpe
  of about 0.94–1.00 vs 0.68, higher returns and smaller drawdowns. A block
  bootstrap puts C1's advantage over the rulebook *within 2018–2026* at
  +0.33 (90% interval +0.08 to +0.57).
- **But on 2008–2017 the same cap made little difference** (0.31 vs 0.28)
  and earned less. It looks like the best choice only because I looked at the
  hold-out. Adopting it now would be exactly the hindsight selection this
  procedure exists to prevent.
- **It still makes sense mechanically.** It stops one quiet fund from
  absorbing the whole cash account (the UUP problem in PAPER.md). That
  makes it a reasonable **hypothesis to test going forward**. Paper
  trading is the only data neither of us has seen.
- **The breakout-length ensemble (C3) didn't help** in either period.
- **2018–2026 was a good period for every version** (0.55–1.00), better
  than 2008–2017 (0.28–0.39). Expect weaker stretches.

## Recommendation

1. **Keep trading the rulebook as written** in the paper report and the
   Alpaca paper bot (no change).
2. **Track the 20% cap as a third paper version: done.** It appears as
   version A2 in the paper report, report only, with no orders sent by the
   bot. After 6–12 months, compare it with the rulebook on data neither
   version was chosen on.
3. **Don't keep optimizing on the same history.** Each extra round of
   "try it and look" makes the backtest look better and the future less
   predictable.
