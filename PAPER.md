# Paper trading — daily routine

**No broker connection. Nothing here places orders.** The report tells you
what the rules would do. A paper account is simulated by the same engine
used in the research.

Three versions run side by side on the same 14 ETFs, with $100,000 each
(`paper/config.json`):

- **A. Modern Turtle rulebook** (STEP3.md): market orders at the next open,
  plus GTC stop orders. This is what the Alpaca paper bot trades.
- **A2. The same rules plus a 20% cap per market** (STEP4.md): a hypothesis
  under test, report only. Not adopted, and never sent to the broker.
- **B. Original Turtle S2, long-only**: one sub-account per ETF, with
  stop orders that rest for one session.

## Every trading day, after 6 pm New York time

```bash
python scripts/download_etfs.py --out data/live --paper   # completed sessions only
python scripts/download_rates.py --out data/live
python scripts/paper_report.py
```

The report is printed and saved to `paper/reports/<session>.md`, and a line
is added to `paper/journal.csv`. Commit both each day so the record is
timestamped by git:

```bash
git add paper/ && git commit -m "Paper report <session>" && git push
```

**Run it before the next open.** A report generated after its session has
opened is marked "Generated late" and doesn't count as a real-time record.

The first real-time report is for the 2026-11-25 close (data is final after
6 pm ET that day). Its orders are for Friday 2026-11-27, the session after
Thanksgiving, which is a half-day.

## How it works, and why it can be trusted

- The paper account **is** the backtest engine, run each evening from the
  start date in `paper/config.json` on the data so far. The engines are
  causal: they are tested so that adding new bars never changes the past. So
  each run reproduces every earlier day and adds one.
- Tests check that the orders printed after a close are exactly what the
  engine executes at the next open, that the stop shown is the stop used
  next day, and that tomorrow's channel levels match.
- Reports are **never overwritten**. If a rerun for the same session differs
  from the archived one, it is saved separately as `<session>-rerun-*.md`.
  A difference means the data was revised or the code changed, and it
  should be investigated.
- Fresh data goes to `data/live/` (not committed). The frozen research
  snapshot in `data/` never changes.

## What to expect, and what paper trading can and can't tell you

- **It won't tell you whether the strategy has an edge.** In the backtest,
  the Modern rules made about 24 trades a year, with long flat stretches.
  Three to six months is a handful of trades, which is pure noise for
  judging returns.
- **It will tell you** whether the daily routine is practical, whether the
  orders make sense when you see them in real time, and whether the
  real-time record matches reruns (data quality). It also tells you whether
  you would actually follow the rules through a losing streak.
- **Known trait of version A:** sizing by volatility gives quiet markets very
  large positions. In a cash account, one low-volatility fund (UUP, IEF) can
  absorb most of the cash and block other entries. In a test run from
  2026-06-01, UUP alone held about 100% of the account while risking 0.8%
  to its stop. That's how the rulebook is written; it's not a bug. Watch
  whether it bothers you.

## Suggested checkpoints (decide now, before seeing results)

After 3 months and again after 6, check:

1. Was a real-time report produced for every session? Missed days count as
   failures of the routine, not the rules.
2. Did any rerun differ from its archived report? Each one needs an
   explanation.
3. Are drawdown, number of trades and time invested inside the backtest's
   ranges (STEP3.md)? Being outside them is a warning even if returns look
   fine.
4. Only after that: compare A vs B, and decide whether a small real-money
   test makes sense. That decision is yours, ideally with a licensed advisor.
