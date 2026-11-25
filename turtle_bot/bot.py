"""Evening / morning runs of the Alpaca PAPER bot.

Both runs take a broker object (``AlpacaPaperBroker`` or ``FakeBroker``),
so the full daily cycle is tested end to end without network access.
Nothing is sent unless ``execute=True``, and never while the kill switch
file ``<state_dir>/KILL`` exists.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import json
import pathlib

from .broker import BrokerError
from .live import Limits, Plan, Target, plan_evening, plan_morning, target_from_engine
from .portfolio import ModernConfig, PortfolioBacktester


def _now_tag() -> str:
    """Unique per run (time + random), so client_order_ids never collide."""
    import uuid
    return dt.datetime.now(dt.timezone.utc).strftime("%H%M%S") + uuid.uuid4().hex[:4]


def kill_switch_on(state_dir: pathlib.Path) -> bool:
    return (state_dir / "KILL").exists()


def last_completed_session(calendar: list[dict], now_et: dt.datetime) -> str | None:
    """Latest calendar date whose close has passed (Alpaca calendar rows:
    {"date": "YYYY-MM-DD", "close": "HH:MM", ...}, times in New York)."""
    done = []
    for row in calendar:
        d = dt.date.fromisoformat(row["date"])
        hh, mm = (int(x) for x in row["close"].split(":")[:2])
        close = dt.datetime(d.year, d.month, d.day, hh, mm, tzinfo=now_et.tzinfo)
        if now_et >= close + dt.timedelta(hours=2):      # daily bars are final ~2h after the close
            done.append(row["date"])
    return max(done) if done else None


def execute(plan: Plan, broker, execute: bool, state_dir: pathlib.Path) -> list[dict]:
    """Cancel, verify nothing of ours is still open, then submit. Every
    result is returned (and logged by the broker client)."""
    results = []
    if not execute:
        return [{"dry_run": True, **o} for o in plan.orders]
    if kill_switch_on(state_dir):
        return [{"skipped": "kill switch on", **o} for o in plan.orders]
    if not plan.ok:
        return [{"skipped": "plan blocked", **o} for o in plan.orders]
    for c in plan.cancels:
        try:
            broker.cancel(c["order_id"])
            results.append({"cancelled": c["client_order_id"]})
        except BrokerError as e:
            results.append({"cancel_failed": c["client_order_id"], "error": str(e)})
    still = {o.id for o in broker.open_orders()} & {c["order_id"] for c in plan.cancels}
    if still:
        return results + [{"aborted": "some cancels did not take effect; nothing submitted", "ids": sorted(still)}]
    for o in plan.orders:
        try:
            r = broker.submit(o["symbol"], o["qty"], o["side"], o["type"], o["tif"],
                              o["client_order_id"], o.get("stop_price"))
            results.append({"submitted": o["client_order_id"], "id": r.id, "status": r.status})
        except BrokerError as e:
            results.append({"submit_failed": o["client_order_id"], "error": str(e)})
    return results


def _save(state_dir: pathlib.Path, name: str, obj) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / name).write_text(json.dumps(obj, indent=2, default=str))


def run_evening(broker, data, universe: dict, rate, start: str, equity: float,
                state_dir: pathlib.Path, execute_orders: bool, now_et: dt.datetime | None = None,
                limits: Limits | None = None) -> dict:
    limits = limits or Limits(frozenset(universe))
    report = {"run": "evening", "execute": execute_orders, "blocked": [], "warnings": []}
    if broker.clock().get("is_open"):
        report["blocked"].append("ALL: the market is open; the evening run only works while it is closed")
        return report
    session = str(min(df.index[-1] for df in data.values()).date())
    if now_et is not None:
        cal = broker.calendar((dt.date.fromisoformat(session) - dt.timedelta(days=10)).isoformat(),
                              now_et.date().isoformat())
        expected = last_completed_session(cal, now_et) if cal else None
        if expected is None:
            report["warnings"].append("could not confirm the latest session from the broker calendar")
        elif expected != session:
            report["blocked"].append(f"ALL: data ends {session} but the last completed session is {expected}; "
                                     "refresh data/live first")
            return report
    res = PortfolioBacktester(ModernConfig(initial_equity=equity)).run(data, universe, rate, start)
    target = target_from_engine(res, session)
    acct = broker.account()
    if acct.trading_blocked:
        report["blocked"].append("ALL: the broker account is blocked from trading")
        return report
    first_run = not (state_dir / "state.json").exists()
    plan = plan_evening(target, broker.positions(), broker.open_orders(), acct.equity, limits,
                        _now_tag(), first_run)
    report.update(session=session, equity=acct.equity, engine_equity=target.engine_equity,
                  cancels=plan.cancels, orders=plan.orders,
                  blocked=report["blocked"] + plan.blocked, warnings=report["warnings"] + plan.warnings)
    report["results"] = execute(plan, broker, execute_orders, state_dir)
    _save(state_dir, "latest_plan.json", {"target": dataclasses.asdict(target), "report": report})
    _save(state_dir, f"plan_{session}_{_now_tag()}.json", {"target": dataclasses.asdict(target), "report": report})
    submitted = any("submitted" in r for r in report["results"])
    if execute_orders and plan.ok and (submitted or not plan.orders) and not kill_switch_on(state_dir):
        _save(state_dir, "state.json", {"initialized": True, "last_evening_session": session})
    return report


def run_morning(broker, universe: dict, state_dir: pathlib.Path, execute_orders: bool,
                limits: Limits | None = None) -> dict:
    limits = limits or Limits(frozenset(universe))
    report = {"run": "morning", "execute": execute_orders, "blocked": [], "warnings": []}
    if not broker.clock().get("is_open"):
        report["blocked"].append("ALL: the market is closed; the morning run works after the open")
        return report
    path = state_dir / "latest_plan.json"
    if not path.exists():
        report["blocked"].append("ALL: no evening plan found; run the evening step first")
        return report
    saved = json.loads(path.read_text())
    target = Target(**saved["target"])
    evening_ids = {o["client_order_id"] for o in saved["report"].get("orders", [])}
    fills: dict[str, list] = {}
    for o in broker.orders_since(target.session):
        if o.client_order_id in evening_ids and o.side == "buy" and o.filled_qty > 0 and o.filled_avg_price:
            fills.setdefault(o.symbol, []).append((o.filled_qty, o.filled_avg_price))
    avg_fill = {s: sum(q * p for q, p in v) / sum(q for q, _ in v) for s, v in fills.items()}
    plan = plan_morning(target, broker.positions(), broker.open_orders(), avg_fill, limits, _now_tag())
    report.update(session=target.session, cancels=plan.cancels, orders=plan.orders,
                  blocked=plan.blocked, warnings=plan.warnings, fills=avg_fill)
    report["results"] = execute(plan, broker, execute_orders, state_dir)
    _save(state_dir, f"morning_{target.session}_{_now_tag()}.json", report)
    return report
