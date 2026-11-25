# Alpaca paper-trading bot

Sends the Modern Turtle rules (version A of the paper report) to an
**Alpaca paper account**. That's simulated money on Alpaca's servers.

**Paper only, by construction.** The broker address is fixed in
`turtle_bot/broker.py` to `https://paper-api.alpaca.markets`, and the
client refuses to start if it's changed. No setting points it at a live
account. Using real money would mean deliberately rewriting that code;
don't, unless you've decided to with full knowledge of the risks.

## What it does each day

| When (New York time) | Command | What it sends |
|---|---|---|
| After 6 pm, trading days | `alpaca_bot.py evening --execute` | Cancels its own outdated orders, then sends **market orders** (filled at the next open) for the difference between the rules' target holdings and what the account holds, plus **GTC sell stops** for shares kept overnight |
| About 9:45 am, trading days | `alpaca_bot.py morning --execute` | Places one **GTC stop** per position covering all shares. New shares get max(evening stop, fill − 3N), and stops whose quantity or price is wrong are fixed |

The rules' target comes from the same engine as the backtest and the paper
report, rerun from `paper/config.json`'s start date. Because the bot only
sends differences, a missed or rejected order is corrected on the next run.

## Safety limits (built in)

- **Dry run by default.** Without `--execute`, it prints the plan and sends
  nothing.
- **Kill switch.** While the file `paper/alpaca/KILL` exists, nothing is
  sent (`touch paper/alpaca/KILL` to stop, delete it to resume).
- **Long-only.** Sells never exceed the shares held, counting market and
  stop sells together, so the account can't go short.
- **No margin.** Alpaca paper accounts allow margin, but the bot keeps total
  positions within 1x equity and shrinks buys in the rules' priority order.
- **Universe only.** It only trades the 14 ETFs in `paper/config.json`.
  Other positions and any orders you place yourself are left alone and
  reported.
- **Starts clean.** The first run refuses to trade unless the paper account
  is empty.
- **Equity check.** It refuses to trade if the account's equity is more than
  20% away from the engine's, because the target share counts would then be
  wrong.
- **Fresh data only.** The evening run refuses stale data (checked against
  Alpaca's calendar) and never uses the frozen research snapshot.
- **Market hours.** The evening run refuses while the market is open; the
  morning run refuses while it's closed.
- **No duplicate orders.** Correct live orders are left in place instead of
  being cancelled and resubmitted, and every order has a unique ID. If any
  cancel doesn't take effect, nothing new is sent.
- **Everything is logged.** Every request and response goes to
  `paper/alpaca/requests.log` (keys are never logged), and plans to
  `paper/alpaca/*.json`. That folder isn't committed.

## One-time setup

1. Create an Alpaca account and open the **Paper Trading** dashboard.
2. **Reset the paper account to $100,000** (the `equity` in
   `paper/config.json`). The bot requires an empty account on its first run.
3. Generate **paper** API keys. Store them **outside the repository**, for
   example in `~/.alpaca_paper_env`:
   ```bash
   export APCA_API_KEY_ID="your-paper-key-id"
   export APCA_API_SECRET_KEY="your-paper-secret"
   ```
   Then run `chmod 600 ~/.alpaca_paper_env`. Never paste keys into chat,
   commit them, or put them in the repo.
4. Install: `pip install -r requirements.txt`.
5. Check the connection: `source ~/.alpaca_paper_env && python scripts/alpaca_bot.py status`.

## First week: watch it by hand

```bash
source ~/.alpaca_paper_env
python scripts/download_etfs.py --out data/live --paper
python scripts/download_rates.py --out data/live
python scripts/paper_report.py                 # the human-readable report
python scripts/alpaca_bot.py evening           # dry run: read the plan
python scripts/alpaca_bot.py evening --execute # send it
# next morning, after ~9:45 ET:
python scripts/alpaca_bot.py morning --execute
python scripts/alpaca_bot.py status            # compare with the report
```

Check that each order in the Alpaca dashboard matches the plan, and that
every position has exactly one stop.

## Then schedule it

On Linux, `crontab -e` (with cronie, `CRON_TZ` sets New York time):

```cron
CRON_TZ=America/New_York
30 18 * * 1-5  cd ~/chat && . ~/.alpaca_paper_env && python3 scripts/download_etfs.py --out data/live --paper && python3 scripts/download_rates.py --out data/live && python3 scripts/paper_report.py && python3 scripts/alpaca_bot.py evening --execute >> paper/alpaca/cron.log 2>&1
45 9  * * 1-5  cd ~/chat && . ~/.alpaca_paper_env && python3 scripts/alpaca_bot.py morning --execute >> paper/alpaca/cron.log 2>&1
```

macOS cron ignores `CRON_TZ`, so convert to your local time. On Windows, use
Task Scheduler with the same commands. The machine must be on at those
times. On holidays the bot detects that the market is closed and does
nothing harmful.

## What I could and couldn't verify

**Verified here** (107 tests in total, 23 of them for the bot):
- every safety limit above;
- the planning logic for entries, adds, exits and stops;
- that the client only ever calls the paper URL and never logs keys;
- a full simulated daily cycle (evening → open with fills and stop
  triggers → morning) over 950 sessions with 32 trades, where the account's
  holdings matched the engine's on **950 of 950 days**.

**Not verified** (no access to your Alpaca account from here), so check
these during the first week:
- that Alpaca accepts the order format as sent (market/day orders queued
  after hours, GTC stop sells, `client_order_id`);
- that queued market orders fill at or near the open;
- that `orders?after=<date>` returns the morning's fills.

If anything is rejected, the bot reports `submit_failed` with Alpaca's
message and the next run retries. Paste the error (never the keys) and
I'll fix it.

## Known differences from the backtest

- Alpaca fills at its own prices; the engine simulates fills at the Yahoo
  open plus 2 bp. The bot copies the engine's **share counts**, so account
  equity will drift slightly from the engine's over time.
- Stops for brand-new positions use Alpaca's actual fill price, so they
  can differ by cents from the engine's.
- Alpaca's paper fills are simulated (no market impact), so they're at
  least as optimistic as the backtest.
- The signals use Yahoo daily data (the same as the research), not
  Alpaca's market data.
