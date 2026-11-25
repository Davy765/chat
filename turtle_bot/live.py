"""Order planning for the Alpaca PAPER bot (pure functions, no I/O).

The bot mirrors the paper report's version A (Modern rulebook). Each
evening the engine is rerun from the paper start date; its holdings after
the next open become the TARGET. The plan sends only the difference between
the target and what the broker account actually holds, so a missed or
rejected order is corrected on the next run instead of drifting.

Evening (market closed, before the next open):
  1. cancel every open order this bot placed (prefix ``tt-``);
  2. market (day) orders for the differences, executed at the next open;
  3. GTC sell stops at the engine's stop for shares held overnight that
     are not being sold.
Morning (after the open):
  4. one GTC sell stop per position, sized to the shares now held: new
     shares get max(evening stop, fill - 3N), as in the rulebook.

Safety rails (a violation blocks the order, it is never "fixed" silently):
  * long-only: sells never exceed shares held (market + stop sells
    together), so the account can never go short;
  * only symbols in the configured universe; anything else is left alone
    and reported;
  * no margin: projected gross <= ``max_gross`` x equity; buys are shrunk
    in the engine's priority order (adds first, then strongest breakouts);
  * one order may not exceed ``max_order_pct`` of equity;
  * the account's equity must be within ``max_equity_mismatch`` of the
    engine's, since the target shares were sized for the engine's account;
  * if someone else has open orders on a universe symbol, that symbol is
    not touched.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .broker import BOT_PREFIX, Order, Position


@dataclass(frozen=True)
class Limits:
    universe: frozenset
    max_gross: float = 1.0
    max_order_pct: float = 1.0
    max_equity_mismatch: float = 0.20
    stop_n: float = 3.0


@dataclass
class Target:
    session: str                                   # last completed session (YYYY-MM-DD)
    shares: dict[str, int]                         # holdings wanted after the next open
    stops: dict[str, float]                        # engine stop for shares held overnight
    n_for_new: dict[str, float]                    # N used for the stop of shares bought at the open
    priority: list[str]                            # buy priority (engine order)
    closes: dict[str, float]
    engine_equity: float


@dataclass
class Plan:
    cancels: list[dict] = field(default_factory=list)
    orders: list[dict] = field(default_factory=list)
    blocked: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not any(b.startswith("ALL:") for b in self.blocked)


def target_from_engine(res, session: str) -> Target:
    """Target holdings after the next open, from a PortfolioResult."""
    shares = {p["ticker"]: int(p["shares"]) for p in res.open_positions}
    stops = {p["ticker"]: float(p["stop"]) for p in res.open_positions}
    pos_n = {p["ticker"]: float(p["n"]) for p in res.open_positions}
    n_new, priority = {}, []
    for o in res.pending_orders:
        t = o["ticker"]
        if o["kind"] == "exit":
            shares[t] = 0
            stops.pop(t, None)
        elif o["kind"] == "add":
            shares[t] = shares.get(t, 0) + int(o["shares"])
            n_new[t] = pos_n[t]                       # adds keep the entry's N
            priority.append(t)
        elif o["kind"] == "entry":
            shares[t] = int(o["shares"])
            n_new[t] = float(o["n"])
            priority.append(t)
    closes = {s["ticker"]: float(s["close"]) for s in res.snapshot}
    return Target(session, shares, stops, n_new, priority, closes, float(res.state["equity"]))


def _cid(session: str, symbol: str, kind: str, run_tag: str) -> str:
    return f"{BOT_PREFIX}{session}-{symbol}-{kind}-{run_tag}"[:48]


def plan_evening(t: Target, positions: dict[str, Position], open_orders: list[Order],
                 equity: float, limits: Limits, run_tag: str, first_run: bool) -> Plan:
    plan = Plan()
    universe = limits.universe

    if first_run and (any(s in universe for s in positions) or open_orders):
        plan.blocked.append("ALL: first run needs an empty paper account (no positions or open orders). "
                            "Reset the Alpaca paper account, then run again.")
        return plan
    if t.engine_equity > 0 and abs(equity / t.engine_equity - 1) > limits.max_equity_mismatch:
        plan.blocked.append(f"ALL: broker equity ${equity:,.0f} differs from the engine's ${t.engine_equity:,.0f} "
                            f"by more than {limits.max_equity_mismatch:.0%}; target sizes would be wrong. "
                            "Reset the paper account to the configured equity.")
        return plan
    unknown = [s for s in t.shares if s not in universe]
    if unknown:
        plan.blocked.append(f"ALL: engine target has symbols outside the universe: {unknown}")
        return plan

    foreign = {o.symbol for o in open_orders if not o.is_bot}
    for s in sorted(foreign & universe):
        plan.warnings.append(f"{s}: open orders not placed by this bot; symbol left untouched")
    for s in sorted(set(positions) - universe):
        plan.warnings.append(f"{s}: position outside the universe; left untouched")

    plan.cancels = [{"order_id": o.id, "symbol": o.symbol, "client_order_id": o.client_order_id}
                    for o in open_orders if o.is_bot]

    sells, buys, stops = [], [], []
    for s in sorted(universe):
        if s in foreign:
            continue
        held = int(positions[s].qty) if s in positions else 0
        if held < 0:
            plan.blocked.append(f"{s}: account is SHORT {held}; not touched, fix manually")
            continue
        want = max(int(t.shares.get(s, 0)), 0)
        if want < held:
            sells.append((s, held - want))
        elif want > held:
            buys.append((s, want - held))
        keep = min(held, want)
        if keep > 0:
            if s in t.stops:
                stops.append((s, keep, t.stops[s]))
            else:
                plan.warnings.append(f"{s}: held {keep} but the engine has no stop for it; morning run will set one")

    # ---- cash: projected gross after the open must fit, shrink buys in priority order
    px = t.closes
    gross = sum(int(p.qty) * px.get(s, p.avg_entry_price) for s, p in positions.items() if s in universe)
    gross -= sum(q * px[s] for s, q in sells)
    budget = limits.max_gross * equity - gross
    order_of = {s: k for k, s in enumerate(t.priority)}
    for s, q in sorted(buys, key=lambda b: order_of.get(b[0], 10**6)):
        fit = max(0, math.floor(budget / px[s]))
        if fit < q:
            plan.warnings.append(f"{s}: buy reduced {q} -> {fit} to stay within {limits.max_gross:.0%} of equity")
            q = fit
        if q <= 0:
            continue
        if q * px[s] > limits.max_order_pct * equity:
            plan.blocked.append(f"{s}: buy of {q} (${q * px[s]:,.0f}) exceeds the per-order limit")
            continue
        budget -= q * px[s]
        plan.orders.append({"symbol": s, "qty": q, "side": "buy", "type": "market", "tif": "day",
                            "client_order_id": _cid(t.session, s, "buy", run_tag), "reason": "target"})
    for s, q in sells:
        plan.orders.append({"symbol": s, "qty": q, "side": "sell", "type": "market", "tif": "day",
                            "client_order_id": _cid(t.session, s, "sell", run_tag), "reason": "target"})
    for s, q, stop in stops:
        plan.orders.append({"symbol": s, "qty": q, "side": "sell", "type": "stop", "tif": "gtc",
                            "stop_price": round(stop, 2),
                            "client_order_id": _cid(t.session, s, "stop", run_tag), "reason": "engine stop"})
    kept = keep_identical(plan, [o for o in open_orders if o.is_bot])
    check_no_short(plan, positions, kept)
    return plan


def _same(o: Order, want: dict) -> bool:
    return (o.symbol == want["symbol"] and o.side == want["side"] and o.type == want["type"]
            and o.qty == want["qty"] and o.time_in_force == want["tif"]
            and (o.stop_price is None) == (want.get("stop_price") is None)
            and (o.stop_price is None or abs(o.stop_price - want["stop_price"]) < 0.005))


def keep_identical(plan: Plan, live: list[Order]) -> list[Order]:
    """Leave already-correct live orders in place instead of cancelling and
    resubmitting them, so a stop is never removed just to be re-placed.
    Returns the live orders that stay open."""
    kept = []
    for o in live:
        match = next((w for w in plan.orders if _same(o, w)), None)
        if match is not None:
            plan.orders.remove(match)
            plan.cancels = [c for c in plan.cancels if c["order_id"] != o.id]
            kept.append(o)
    return kept


def plan_morning(t: Target, positions: dict[str, Position], open_orders: list[Order],
                 todays_buy_fills: dict[str, float], limits: Limits, run_tag: str) -> Plan:
    """After the open: make sure every universe position has exactly one bot
    stop covering all its shares at the right price."""
    plan = Plan()
    foreign = {o.symbol for o in open_orders if not o.is_bot}
    for s, p in sorted(positions.items()):
        if s not in limits.universe or s in foreign:
            continue
        held = int(p.qty)
        if held <= 0:
            continue
        price = t.stops.get(s, -math.inf)
        if s in todays_buy_fills:
            n = t.n_for_new.get(s)
            if n is None:
                plan.warnings.append(f"{s}: bought today but the evening plan has no N for it; "
                                     "keeping the evening stop only")
            else:
                price = max(price, todays_buy_fills[s] - limits.stop_n * n)
        if price == -math.inf:
            plan.blocked.append(f"{s}: holding {held} shares with no known stop; set one manually")
            continue
        price = round(price, 2)
        bot_stops = [o for o in open_orders if o.is_bot and o.symbol == s and o.type == "stop"]
        if len(bot_stops) == 1 and bot_stops[0].qty == held and bot_stops[0].stop_price == price:
            continue
        plan.cancels += [{"order_id": o.id, "symbol": s, "client_order_id": o.client_order_id} for o in bot_stops]
        plan.orders.append({"symbol": s, "qty": held, "side": "sell", "type": "stop", "tif": "gtc",
                            "stop_price": price, "client_order_id": _cid(t.session, s, "mstop", run_tag),
                            "reason": "morning stop"})
        if s in t.shares and t.shares[s] != held:
            plan.warnings.append(f"{s}: holding {held}, engine target {t.shares[s]} (fill difference)")
    for s, want in t.shares.items():
        if want > 0 and s not in positions:
            plan.warnings.append(f"{s}: engine target {want} shares but none held (order not filled?)")
    kept_open = [o for o in open_orders if o.id not in {c["order_id"] for c in plan.cancels}]
    check_no_short(plan, positions, kept_open)
    return plan


def check_no_short(plan: Plan, positions: dict[str, Position], other_open: list[Order]) -> None:
    """Block every sell that could take a symbol below zero, counting market
    and stop sells that would all be live at the same time."""
    by_sym: dict[str, int] = {}
    for o in other_open:
        if o.side == "sell":
            by_sym[o.symbol] = by_sym.get(o.symbol, 0) + o.qty
    keep = []
    for o in plan.orders:
        if o["side"] == "sell":
            held = int(positions[o["symbol"]].qty) if o["symbol"] in positions else 0
            total = by_sym.get(o["symbol"], 0) + o["qty"]
            if total > held:
                plan.blocked.append(f"{o['symbol']}: {o['type']} sell of {o['qty']} would exceed the "
                                    f"{held} shares held (no shorting); not sent")
                continue
            by_sym[o["symbol"]] = total
        keep.append(o)
    plan.orders = keep
