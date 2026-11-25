"""Alpaca PAPER trading bot for the Modern Turtle rules (version A).

PAPER ONLY: the broker URL is fixed to paper-api.alpaca.markets in
turtle_bot/broker.py. Dry run by default; orders are sent only with
--execute. See ALPACA.md for setup and scheduling.

    python scripts/alpaca_bot.py status
    python scripts/alpaca_bot.py evening            # after 6 pm ET: plan (dry run)
    python scripts/alpaca_bot.py evening --execute  # ... and send the orders
    python scripts/alpaca_bot.py morning --execute  # ~9:45 am ET: stops for new fills

Kill switch: create the file paper/alpaca/KILL to stop all order sending.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import sys
from zoneinfo import ZoneInfo

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from turtle_bot.bot import kill_switch_on, run_evening, run_morning  # noqa: E402
from turtle_bot.broker import AlpacaPaperBroker, BrokerError  # noqa: E402
from turtle_bot.data import load_csv, load_rate_csv  # noqa: E402

STATE = ROOT / "paper" / "alpaca"


def print_report(rep: dict) -> None:
    mode = "EXECUTE" if rep.get("execute") else "DRY RUN (nothing sent; add --execute)"
    print(f"== {rep['run']} run · {mode} · session {rep.get('session', '?')}")
    if "equity" in rep:
        print(f"   broker equity ${rep['equity']:,.2f} · engine equity ${rep['engine_equity']:,.2f}")
    for b in rep.get("blocked", []):
        print(f"   BLOCKED: {b}")
    for w in rep.get("warnings", []):
        print(f"   warning: {w}")
    for c in rep.get("cancels", []):
        print(f"   cancel  {c['symbol']:5s} {c['client_order_id']}")
    for o in rep.get("orders", []):
        extra = f" stop {o['stop_price']:.2f}" if o.get("stop_price") is not None else ""
        print(f"   {o['side'].upper():4s} {o['qty']:>6} {o['symbol']:5s} {o['type']}/{o['tif']}{extra}  ({o['reason']})")
    if not rep.get("orders") and not rep.get("cancels"):
        print("   no orders")
    for r in rep.get("results", []):
        if not r.get("dry_run"):
            print(f"   -> {r}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=["status", "evening", "morning"])
    ap.add_argument("--execute", action="store_true", help="actually send orders to the PAPER account")
    args = ap.parse_args()

    cfg = json.loads((ROOT / "paper" / "config.json").read_text())
    universe = cfg["universe"]
    try:
        broker = AlpacaPaperBroker(log_path=STATE / "requests.log")
    except BrokerError as e:
        sys.exit(f"error: {e}")
    if kill_switch_on(STATE):
        print("KILL SWITCH ON (paper/alpaca/KILL exists): no orders will be sent.")

    if args.command == "status":
        a = broker.account()
        print(f"account {a.account_number} · status {a.status} · equity ${a.equity:,.2f} · cash ${a.cash:,.2f}")
        for p in broker.positions().values():
            print(f"   position {p.symbol:5s} {p.qty:>6} @ {p.avg_entry_price:.2f}")
        for o in broker.open_orders():
            who = "bot" if o.is_bot else "OTHER"
            print(f"   open order [{who}] {o.side} {o.qty} {o.symbol} {o.type}"
                  + (f" stop {o.stop_price}" if o.stop_price else ""))
        return

    if args.command == "evening":
        live = ROOT / "data" / "live"
        missing = [t for t in universe if not (live / f"{t}.csv").exists()] + \
                  ([] if (live / "DTB3.csv").exists() else ["DTB3"])
        if missing:
            sys.exit(f"error: data/live is missing {missing}. Refresh it first (see ALPACA.md). "
                     "The bot never trades on the frozen research snapshot.")
        data = {t: load_csv(str(live / f"{t}.csv")) for t in universe}
        rate = load_rate_csv(str(live / "DTB3.csv"))
        rep = run_evening(broker, data, universe, rate, cfg["start"], float(cfg["equity"]), STATE,
                          args.execute, now_et=dt.datetime.now(ZoneInfo("America/New_York")))
    else:
        rep = run_morning(broker, universe, STATE, args.execute)
    print_report(rep)
    if any(b.startswith("ALL:") for b in rep.get("blocked", [])):
        sys.exit(2)


if __name__ == "__main__":
    main()
