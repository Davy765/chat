# Step 2 — Out-of-sample test on 8 ETFs

Date: 2026-11-25 · Full tables: [`reports/step2.md`](reports/step2.md)
(regenerate: `python scripts/step2_multi_etf.py`, ~3 min) · Data: frozen
snapshot in `data/`, each ETF from its first date through 2026-11-24.

**Setup.** The complete rules from step 1 (drawdown rule, T-bill interest,
1% risk per unit, 4 units, 2N stop, 1x cap, 2 bp slippage) ran on each ETF
with **no parameter changes**. SPY was the development market. QQQ, IWM, DIA,
EFA, EEM, TLT and GLD are out of sample.

**Pass criteria, fixed before running** (see the script's docstring and
commit `03cb895`). Long-only passes on an ETF if the reference system (S1+S2)
has (a) excess Sharpe over T-bills > 0 **and** (b) at most 10% of 100
random-entry runs beat it. The random runs use the same engine, exits,
stops, sizing, costs and interest, with trade frequency calibrated per
system. Calibration was checked: random runs make 1–7% more trades than the
real system and spend within 3 points as much time in the market.

## Verdict

| | Result |
|---|---|
| Long-only passes | **1 of 8 (GLD).** SPY misses by one run (11 of 100 random runs beat it). |
| Is the breakout entry better than random entries out of sample? | **No.** Across the 7 out-of-sample ETFs, on average **51%** of random runs beat the real rules, which is what you'd expect with no edge at all. One pass in 8 at a 10% threshold is also what chance alone produces (~0.8 expected). |
| Does long-only beat cash? | Yes on 7 of 8 (IWM is negative). But it beats **buy and hold** on excess Sharpe only for TLT and GLD (both by ≤ 0.02) and for QQQ with S2. |
| Do shorts work anywhere? | **No.** The short side loses on all 8 ETFs, including bonds and gold. Short profit factor ranges 0.25–0.87. |
| Does diversification help? | **For risk, yes; for return, no.** The 8-ETF basket roughly halves the drawdown of the SPY-only system but earns less and has lower excess Sharpe. |

**Bottom line:** out of sample, the Turtle breakout **entry** shows no
reliable edge on these ETFs. SPY's earlier result (beating 96% of random
entries) does not generalize. It was most likely partly luck in the market
the rules were developed on. What does hold up is the **risk-control** side:
long-only trend following with 2N stops and channel exits keeps drawdowns
very small. That is a drawdown-control tool with returns below buy and hold,
not a source of excess return.

---

## 1. Long-only, reference system S1+S2

| ETF | Asset | CAGR | Excess Sharpe | Random median excess Sharpe | Random runs beating it | Buy & hold excess Sharpe | Pass |
|---|---|---:|---:|---:|---:|---:|:---:|
| SPY | US large cap (dev.) | 5.08% | 0.36 | 0.24 | 11% | 0.50 | ❌ (borderline) |
| QQQ | Nasdaq-100 | 5.38% | 0.36 | 0.34 | 45% | 0.42 | ❌ |
| IWM | US small cap | −0.06% | −0.15 | 0.10 | 100% | 0.36 | ❌ |
| DIA | Dow 30 | 2.00% | 0.02 | 0.01 | 49% | 0.41 | ❌ |
| EFA | Developed ex-US | 2.06% | 0.08 | 0.18 | 73% | 0.32 | ❌ |
| EEM | Emerging markets | 3.06% | 0.17 | 0.22 | 63% | 0.36 | ❌ |
| TLT | 20y+ Treasuries | 2.99% | 0.20 | 0.10 | 21% | 0.18 | ❌ |
| GLD | Gold | 6.76% | 0.51 | 0.31 | 7% | 0.50 | ✅ |

* **IWM is the clearest failure:** all 100 random runs beat it. Small-cap
  breakouts reverse often (29% win rate, −0.10R per trade), so buying the
  20/55-day high is worse than buying on a random day.
* **Random entries with Turtle exits beat cash on every ETF** (median excess
  Sharpe 0.01–0.34). Most of what the
  strategy earns comes from being long part of the time, the stop/exit
  discipline and interest, not from the breakout timing.
* **GLD and TLT**, the two non-equity markets, are where trend following
  looks best relative to buy and hold. That fits the long history of trend
  following working better outside equities, but it's 2 markets, and GLD's
  pass is about what chance would give.

### Which system?

Long-only excess Sharpe, by system:

| | SPY | QQQ | IWM | DIA | EFA | EEM | TLT | GLD |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| S1 | 0.36 | 0.29 | −0.08 | 0.05 | 0.04 | 0.19 | 0.22 | 0.54 |
| S2 | 0.42 | 0.47 | 0.02 | 0.10 | 0.08 | 0.33 | 0.10 | 0.54 |
| S1+S2 | 0.36 | 0.36 | −0.15 | 0.02 | 0.08 | 0.17 | 0.20 | 0.51 |
| Buy & hold | 0.50 | 0.42 | 0.36 | 0.41 | 0.32 | 0.36 | 0.18 | 0.50 |

S2 is best or tied on 7 of 8 ETFs (TLT is the exception). **S1+S2 is never
better than S2 alone**, so on these markets combining the systems adds trades
and costs without adding diversification.

## 2. Short side

S1+S2 long+short runs, P&L of short trades as % of initial equity:

| ETF | Short trades | Short P&L | Long P&L | Short profit factor | Short win % |
|---|---:|---:|---:|---:|---:|
| SPY | 276 | −164% | +86% | 0.25 | 15% |
| QQQ | 222 | −147% | +86% | 0.29 | 14% |
| IWM | 199 | −55% | −23% | 0.63 | 22% |
| DIA | 212 | −73% | +4% | 0.50 | 17% |
| EFA | 206 | −87% | +20% | 0.49 | 19% |
| EEM | 193 | −147% | +99% | 0.48 | 20% |
| TLT | 180 | −20% | +46% | 0.87 | 24% |
| GLD | 156 | −78% | +152% | 0.49 | 24% |

The short side loses everywhere, wiping out most or all of the long-side
profit. Even TLT and gold, which have no equity-style upward drift, lose on
shorts. Part of the cost is structural: a short ETF position pays the
dividends or coupons, which the adjusted prices charge correctly. A PUT's
price embeds the same carry, so this cost doesn't disappear with options.

**This is strong out-of-sample evidence for dropping the short side.**

## 3. Equal-weight 8-ETF basket vs SPY alone

2005-02-15 → 2026-11-24 (common period), no rebalancing. Each ETF runs in its
own sub-account with its own 1x cap, so the basket is 1x gross in total.
This is an approximation: the Turtles' limits on correlated markets are not
modeled.

| Portfolio | CAGR | Max DD | MAR | Excess Sharpe |
|---|---:|---:|---:|---:|
| Basket, S2 long-only | 4.54% | −6.4% | 0.71 | 0.50 |
| SPY only, S2 long-only | 5.03% | −14.3% | 0.35 | 0.45 |
| Basket, S1+S2 long-only | 4.00% | −6.3% | 0.63 | 0.41 |
| SPY only, S1+S2 long-only | 5.18% | −11.8% | 0.44 | 0.47 |
| Basket, S1+S2 long+short | −0.43% | −28.7% | −0.01 | −0.22 |
| Basket buy & hold | 9.53% | −41.4% | 0.23 | 0.56 |
| SPY buy & hold | 10.51% | −55.2% | 0.19 | 0.53 |

* Diversification works as expected for risk. The strategies' daily returns
  are less correlated with each other (0.30) than the ETFs' prices are
  (0.42), and the basket's worst drawdown is about half of SPY-only's.
* **The S2 long-only basket is the best risk-adjusted result in this
  study:** −6.4% worst drawdown over 22 years (including 2008 and 2020),
  MAR 0.71 and excess Sharpe 0.50. That's still below buy and hold's 0.56,
  but with about one-sixth of the drawdown.

---

## Caveats

* 8 markets, of which 6 are equity indices that are highly correlated with
  each other. The Turtles traded about 20 futures markets across bonds,
  currencies, commodities and equities. Trend following is generally
  reported to work best in commodities and currencies, which are not tested
  here.
* 100 random runs per ETF: a "random runs beating" figure has a sampling
  error of about ±3 points near 10%, so SPY's 11% is effectively on the line.
* Path noise (ROBUSTNESS.md §G) applies: differences under ~0.3 CAGR points
  or ~0.05 Sharpe are not meaningful.
* All tests use daily bars, worst-case intrabar ordering and 2 bp slippage.

## What this means for the project (decisions for you, nothing changed)

1. **Shorts:** drop them. The evidence is now consistent across 8 markets and
   every robustness test.
2. **Be realistic about what the system offers.** On equity ETFs it is a
   drawdown-control overlay, not an alpha source. A long-only S2 basket gave
   about 4.5% a year (part of it T-bill interest on idle cash) with a −6%
   worst drawdown.
3. **Options deserve caution.** Long CALLs pay time decay and wide spreads.
   With no reliable entry edge, a CALL version will likely do *worse* than
   the share version after costs. If you still want options, I'd treat the
   options stage first as a cost test: can the drawdown-control benefit
   survive real option costs?
4. **An alternative to options:** if the goal is a real trend-following
   edge, the next test with the best chance is a broader, less correlated
   universe in the Turtles' spirit. That means commodity, currency and
   rate ETFs (e.g. DBC, USO, SLV, UUP, FXE, IEF). It would be a new
   out-of-sample test, again with no retuning.
