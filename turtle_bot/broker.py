"""Broker access for the paper-trading bot: Alpaca PAPER only.

Safety by construction:
* The base URL is a constant for Alpaca's paper environment. There is no
  parameter, environment variable or config to change it, and the client
  refuses to start if the constant has been edited to anything else.
* Keys come from the environment (APCA_API_KEY_ID / APCA_API_SECRET_KEY),
  never from files in the repo, and are never logged.
* Every request and response is appended to a local JSON-lines log
  (paper/alpaca/requests.log, not committed).

``FakeBroker`` implements the same interface in memory for tests.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import pathlib
import uuid
from dataclasses import dataclass, field

PAPER_BASE_URL = "https://paper-api.alpaca.markets"
_REQUIRED_HOST = "paper-api.alpaca.markets"
BOT_PREFIX = "tt-"            # client_order_id prefix of every order this bot sends


class BrokerError(RuntimeError):
    pass


@dataclass
class Position:
    symbol: str
    qty: int
    avg_entry_price: float


@dataclass
class Order:
    id: str
    client_order_id: str
    symbol: str
    side: str                 # "buy" | "sell"
    type: str                 # "market" | "stop"
    qty: int
    time_in_force: str        # "day" | "gtc"
    stop_price: float | None = None
    status: str = "new"
    filled_qty: int = 0
    filled_avg_price: float | None = None
    submitted_at: str = ""

    @property
    def is_bot(self) -> bool:
        return self.client_order_id.startswith(BOT_PREFIX)


@dataclass
class Account:
    equity: float
    cash: float
    account_number: str
    trading_blocked: bool = False
    status: str = "ACTIVE"


class AlpacaPaperBroker:
    def __init__(self, log_path: pathlib.Path | None = None, session=None):
        if not PAPER_BASE_URL.startswith("https://") or _REQUIRED_HOST not in PAPER_BASE_URL:
            raise BrokerError("refusing to run: broker URL is not Alpaca PAPER")
        key, secret = os.environ.get("APCA_API_KEY_ID"), os.environ.get("APCA_API_SECRET_KEY")
        if not key or not secret:
            raise BrokerError("set APCA_API_KEY_ID and APCA_API_SECRET_KEY (paper keys) in the environment")
        import requests

        self._s = session or requests.Session()
        self._s.headers.update({"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret})
        self._log = log_path

    # ---------------------------------------------------------------- http
    def _req(self, method: str, path: str, **kw):
        url = PAPER_BASE_URL + path
        r = self._s.request(method, url, timeout=30, **kw)
        if self._log is not None:
            self._log.parent.mkdir(parents=True, exist_ok=True)
            with self._log.open("a") as f:
                f.write(json.dumps({"t": dt.datetime.now(dt.timezone.utc).isoformat(), "method": method,
                                    "path": path, "params": kw.get("params"), "body": kw.get("json"),
                                    "status": r.status_code, "response": r.text[:2000]}) + "\n")
        if r.status_code >= 400:
            raise BrokerError(f"{method} {path} -> {r.status_code}: {r.text[:300]}")
        return r.json() if r.text else None

    # ------------------------------------------------------------- reading
    def account(self) -> Account:
        a = self._req("GET", "/v2/account")
        return Account(float(a["equity"]), float(a["cash"]), a.get("account_number", ""),
                       bool(a.get("trading_blocked") or a.get("account_blocked")), a.get("status", ""))

    def positions(self) -> dict[str, Position]:
        out = {}
        for p in self._req("GET", "/v2/positions"):
            qty = float(p["qty"])
            out[p["symbol"]] = Position(p["symbol"], int(qty) if qty == int(qty) else qty,
                                        float(p["avg_entry_price"]))
        return out

    def open_orders(self) -> list[Order]:
        return [self._order(o) for o in self._req("GET", "/v2/orders", params={"status": "open", "limit": 500})]

    def orders_since(self, after_iso: str) -> list[Order]:
        rows = self._req("GET", "/v2/orders", params={"status": "all", "after": after_iso, "limit": 500})
        return [self._order(o) for o in rows]

    def clock(self) -> dict:
        return self._req("GET", "/v2/clock")

    def calendar(self, start: str, end: str) -> list[dict]:
        return self._req("GET", "/v2/calendar", params={"start": start, "end": end})

    # ------------------------------------------------------------- writing
    def submit(self, symbol: str, qty: int, side: str, type_: str, tif: str,
               client_order_id: str, stop_price: float | None = None) -> Order:
        body = {"symbol": symbol, "qty": str(int(qty)), "side": side, "type": type_,
                "time_in_force": tif, "client_order_id": client_order_id}
        if stop_price is not None:
            body["stop_price"] = f"{stop_price:.2f}"
        return self._order(self._req("POST", "/v2/orders", json=body))

    def cancel(self, order_id: str) -> None:
        self._req("DELETE", f"/v2/orders/{order_id}")

    @staticmethod
    def _order(o: dict) -> Order:
        q = float(o.get("qty") or 0)
        return Order(o["id"], o.get("client_order_id", ""), o["symbol"], o["side"], o["type"],
                     int(q), o.get("time_in_force", ""),
                     float(o["stop_price"]) if o.get("stop_price") else None, o.get("status", ""),
                     int(float(o.get("filled_qty") or 0)),
                     float(o["filled_avg_price"]) if o.get("filled_avg_price") else None,
                     o.get("submitted_at", ""))


@dataclass
class FakeBroker:
    """In-memory broker for tests. Models what matters for the bot's safety:
    positions, open orders, cash, duplicate client ids, and (in ``open_session``)
    market fills at the open plus stop triggers."""
    cash: float = 100_000.0
    pos: dict = field(default_factory=dict)          # symbol -> Position
    orders: dict = field(default_factory=dict)       # id -> Order
    is_open: bool = False
    allow_short: bool = True                         # like a real margin account: the BOT must prevent shorts
    sent: list = field(default_factory=list)
    marks: dict = field(default_factory=dict)        # symbol -> last price seen (marked to market)

    def account(self) -> Account:
        value = sum(p.qty * self.marks.get(s, p.avg_entry_price) for s, p in self.pos.items())
        return Account(self.cash + value, self.cash, "PAFAKE")

    def mark(self, prices: dict[str, float]) -> None:
        self.marks.update(prices)

    def positions(self):
        return {s: Position(p.symbol, p.qty, p.avg_entry_price) for s, p in self.pos.items()}

    def open_orders(self):
        return [o for o in self.orders.values() if o.status in ("new", "accepted")]

    def orders_since(self, after_iso):
        return list(self.orders.values())

    def clock(self):
        return {"is_open": self.is_open}

    def calendar(self, start, end):
        return []

    def submit(self, symbol, qty, side, type_, tif, client_order_id, stop_price=None):
        if any(o.client_order_id == client_order_id for o in self.orders.values()):
            raise BrokerError("client_order_id must be unique")
        o = Order(uuid.uuid4().hex, client_order_id, symbol, side, type_, int(qty), tif, stop_price, "new")
        self.orders[o.id] = o
        self.sent.append(o)
        return o

    def cancel(self, order_id):
        self.orders[order_id].status = "canceled"

    # ----------------------------------------------------------- simulation
    def open_session(self, opens: dict[str, float], lows: dict[str, float] | None = None):
        """Fill queued market orders at the open, then trigger sell stops."""
        self.mark(opens)
        for o in list(self.open_orders()):
            if o.type == "market":
                self._fill(o, opens[o.symbol])
        for o in list(self.open_orders()):
            if o.type == "stop" and o.side == "sell":
                low = (lows or opens)[o.symbol]
                if opens[o.symbol] <= o.stop_price:
                    self._fill(o, opens[o.symbol])
                elif low <= o.stop_price:
                    self._fill(o, o.stop_price)

    def _fill(self, o: Order, px: float):
        held = self.pos.get(o.symbol)
        q = held.qty if held else 0
        new_q = q + (o.qty if o.side == "buy" else -o.qty)
        if new_q < 0 and not self.allow_short:
            o.status = "rejected"
            return
        if o.side == "buy":
            avg = ((held.avg_entry_price * q) if held else 0) + px * o.qty
            self.pos[o.symbol] = Position(o.symbol, new_q, avg / new_q)
        elif new_q == 0:
            self.pos.pop(o.symbol)
        else:
            self.pos[o.symbol] = Position(o.symbol, new_q, held.avg_entry_price if held else px)
        self.cash += (-1 if o.side == "buy" else 1) * px * o.qty
        o.status, o.filled_qty, o.filled_avg_price = "filled", o.qty, px
