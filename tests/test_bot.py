import datetime as dt
import math
import pathlib
import sys
from zoneinfo import ZoneInfo

import numpy as np
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import turtle_bot.broker as broker_mod  # noqa: E402
from turtle_bot.bot import last_completed_session, run_evening, run_morning  # noqa: E402
from turtle_bot.broker import AlpacaPaperBroker, BrokerError, FakeBroker, Order, Position  # noqa: E402
from turtle_bot.data import synthetic_ohlc  # noqa: E402
from turtle_bot.live import Limits, Target, check_no_short, plan_evening, plan_morning  # noqa: E402
from turtle_bot.portfolio import ModernConfig, PortfolioBacktester  # noqa: E402

U = frozenset({"AAA", "BBB", "CCC"})
LIM = Limits(U)


def tgt(shares, stops=None, n=None, closes=None, priority=None, equity=100_000.0):
    closes = closes or {s: 100.0 for s in U}
    return Target("2026-01-05", shares, stops or {}, n or {}, priority or list(shares), closes, equity)


def bot_order(sym, side, typ, qty, stop=None, cid=None):
    return Order(f"id-{sym}-{typ}", cid or f"tt-x-{sym}-{typ}", sym, side, typ, qty, "gtc", stop, "new")


# ---------------------------------------------------------------- broker URL
def test_client_refuses_without_keys(monkeypatch):
    monkeypatch.delenv("APCA_API_KEY_ID", raising=False)
    monkeypatch.delenv("APCA_API_SECRET_KEY", raising=False)
    with pytest.raises(BrokerError):
        AlpacaPaperBroker()


def test_client_refuses_non_paper_url(monkeypatch):
    monkeypatch.setenv("APCA_API_KEY_ID", "x")
    monkeypatch.setenv("APCA_API_SECRET_KEY", "y")
    monkeypatch.setattr(broker_mod, "PAPER_BASE_URL", "https://api.alpaca.markets")
    with pytest.raises(BrokerError, match="PAPER"):
        AlpacaPaperBroker()


def test_client_uses_paper_url_and_never_logs_keys(monkeypatch, tmp_path):
    monkeypatch.setenv("APCA_API_KEY_ID", "KEY123")
    monkeypatch.setenv("APCA_API_SECRET_KEY", "SECRET456")
    seen = []

    class Resp:
        status_code, text = 200, '{"equity": "100000", "cash": "100000", "account_number": "PA1"}'

        def json(self):
            return {"equity": "100000", "cash": "100000", "account_number": "PA1"}

    class Sess:
        headers = {}

        def request(self, method, url, **kw):
            seen.append(url)
            return Resp()

    b = AlpacaPaperBroker(log_path=tmp_path / "log", session=Sess())
    b.account()
    assert seen == ["https://paper-api.alpaca.markets/v2/account"]
    log = (tmp_path / "log").read_text()
    assert "KEY123" not in log and "SECRET456" not in log


# ------------------------------------------------------------ evening plan
def test_evening_buys_entries_and_stops_only_held_shares():
    t = tgt({"AAA": 100, "BBB": 50}, stops={"BBB": 95.0}, n={"AAA": 1.0})
    pos = {"BBB": Position("BBB", 50, 99.0)}
    p = plan_evening(t, pos, [], 100_000, LIM, "r1", first_run=False)
    kinds = {(o["symbol"], o["type"], o["side"]): o for o in p.orders}
    assert kinds[("AAA", "market", "buy")]["qty"] == 100
    assert ("AAA", "stop", "sell") not in kinds                    # no stop before shares exist
    assert kinds[("BBB", "stop", "sell")]["qty"] == 50 and kinds[("BBB", "stop", "sell")]["stop_price"] == 95.0


def test_evening_exit_sells_and_places_no_stop():
    t = tgt({"AAA": 0}, stops={})
    pos = {"AAA": Position("AAA", 80, 100.0)}
    p = plan_evening(t, pos, [bot_order("AAA", "sell", "stop", 80, 90.0)], 100_000, LIM, "r1", False)
    assert [c["symbol"] for c in p.cancels] == ["AAA"]               # old stop cancelled
    assert [(o["side"], o["type"], o["qty"]) for o in p.orders] == [("sell", "market", 80)]


def test_evening_never_shorts_with_partial_reduction():
    t = tgt({"AAA": 30}, stops={"AAA": 90.0})
    pos = {"AAA": Position("AAA", 100, 100.0)}
    p = plan_evening(t, pos, [], 100_000, LIM, "r1", False)
    sells = sum(o["qty"] for o in p.orders if o["side"] == "sell")
    assert sells == 100                                             # 70 market + 30 stop = held


def test_check_no_short_blocks_excess_sells():
    from turtle_bot.live import Plan
    plan = Plan(orders=[{"symbol": "AAA", "qty": 60, "side": "sell", "type": "stop"}])
    check_no_short(plan, {"AAA": Position("AAA", 100, 1.0)}, [bot_order("AAA", "sell", "stop", 50, 9.0)])
    assert plan.orders == [] and "no shorting" in plan.blocked[0]


def test_evening_shrinks_buys_to_cash_in_priority_order():
    t = tgt({"AAA": 600, "BBB": 600}, n={"AAA": 1, "BBB": 1}, priority=["BBB", "AAA"])
    p = plan_evening(t, {}, [], 100_000, LIM, "r1", False)
    q = {o["symbol"]: o["qty"] for o in p.orders}
    assert q["BBB"] == 600 and q["AAA"] == 400                       # 1x cash at $100
    assert any("reduced" in w for w in p.warnings)


def test_evening_first_run_requires_empty_account():
    t = tgt({"AAA": 10})
    p = plan_evening(t, {"BBB": Position("BBB", 5, 1.0)}, [], 100_000, LIM, "r1", first_run=True)
    assert not p.ok and p.orders == []


def test_evening_blocks_on_equity_mismatch():
    p = plan_evening(tgt({"AAA": 10}), {}, [], 50_000, LIM, "r1", False)
    assert not p.ok


def test_evening_blocks_symbols_outside_universe():
    p = plan_evening(tgt({"ZZZ": 10}, closes={"ZZZ": 1.0, **{s: 100.0 for s in U}}), {}, [], 100_000, LIM, "r1", False)
    assert not p.ok


def test_evening_leaves_foreign_orders_and_positions_alone():
    t = tgt({"AAA": 10, "BBB": 10})
    manual = Order("m1", "my-own-order", "AAA", "buy", "limit", 5, "day")
    p = plan_evening(t, {"TSLA": Position("TSLA", 3, 200.0)}, [manual], 100_000, LIM, "r1", False)
    assert all(o["symbol"] != "AAA" for o in p.orders)
    assert all(c["order_id"] != "m1" for c in p.cancels)
    assert any("TSLA" in w for w in p.warnings) and any("AAA" in w for w in p.warnings)


def test_evening_never_touches_a_short_position():
    p = plan_evening(tgt({"AAA": 0}), {"AAA": Position("AAA", -10, 100.0)}, [], 100_000, LIM, "r1", False)
    assert p.orders == [] and any("SHORT" in b for b in p.blocked)


# ------------------------------------------------------------ morning plan
def test_morning_stop_for_new_fill_is_max_of_evening_stop_and_fill_minus_3n():
    t = tgt({"AAA": 100, "BBB": 150}, stops={"BBB": 97.0}, n={"AAA": 2.0, "BBB": 1.0})
    pos = {"AAA": Position("AAA", 100, 101.0), "BBB": Position("BBB", 150, 99.0)}
    p = plan_morning(t, pos, [bot_order("BBB", "sell", "stop", 100, 97.0)],
                     {"AAA": 101.0, "BBB": 101.5}, LIM, "m1")
    st = {o["symbol"]: o for o in p.orders}
    assert st["AAA"]["stop_price"] == 95.0 and st["AAA"]["qty"] == 100     # 101 - 3x2
    assert st["BBB"]["stop_price"] == 98.5 and st["BBB"]["qty"] == 150     # max(97, 101.5-3)
    assert [c["symbol"] for c in p.cancels] == ["BBB"]


def test_morning_leaves_correct_stop_alone():
    t = tgt({"AAA": 100}, stops={"AAA": 95.0})
    p = plan_morning(t, {"AAA": Position("AAA", 100, 99.0)}, [bot_order("AAA", "sell", "stop", 100, 95.0)],
                     {}, LIM, "m1")
    assert p.orders == [] and p.cancels == []


def test_morning_blocks_position_without_any_known_stop():
    p = plan_morning(tgt({}), {"AAA": Position("AAA", 10, 99.0)}, [], {}, LIM, "m1")
    assert p.orders == [] and p.blocked


# ----------------------------------------------------- calendar freshness
def test_last_completed_session_waits_two_hours_after_close():
    ny = ZoneInfo("America/New_York")
    cal = [{"date": "2026-11-24", "open": "09:30", "close": "16:00"},
           {"date": "2026-11-25", "open": "09:30", "close": "16:00"}]
    assert last_completed_session(cal, dt.datetime(2026, 11, 25, 17, 30, tzinfo=ny)) == "2026-11-24"
    assert last_completed_session(cal, dt.datetime(2026, 11, 25, 18, 5, tzinfo=ny)) == "2026-11-25"


# ------------------------------------------------- end-to-end, many days
def _universe(n=900):
    data, groups = {}, {}
    for k, s in enumerate(sorted(U)):
        df = synthetic_ohlc(n, seed=40 + k, vol=0.013 + 0.003 * k, drift=0.0004)
        df["Volume"] = 1e7
        data[s], groups[s] = df, "g" if k < 2 else "h"
    return data, groups


def test_full_daily_cycle_against_fake_broker(tmp_path):
    """Run evening -> open (fills, stops) -> morning for 300 sessions.
    Invariants every day: never short, one bot stop per position covering
    all shares, no margin, and holdings track the engine."""
    data, groups = _universe()
    start = str(data["AAA"].index[500].date())
    fb = FakeBroker(cash=100_000.0)
    matched, days = 0, 0
    for k in range(500, 800):
        hist = {s: d.iloc[:k + 1] for s, d in data.items()}
        fb.is_open = False
        rep = run_evening(fb, hist, groups, None, start, 100_000.0, tmp_path, execute_orders=True)
        assert not any(b.startswith("ALL:") for b in rep["blocked"]), rep["blocked"]
        nxt = {s: d.iloc[k + 1] for s, d in data.items()}
        fb.open_session({s: r.Open for s, r in nxt.items()}, {s: r.Low for s, r in nxt.items()})
        fb.is_open = True
        run_morning(fb, groups, tmp_path, execute_orders=True)
        # ---- invariants
        pos = fb.positions()
        assert all(p.qty > 0 for p in pos.values())
        stops = [o for o in fb.open_orders() if o.type == "stop"]
        for s, p in pos.items():
            mine = [o for o in stops if o.symbol == s]
            assert len(mine) == 1 and mine[0].qty == p.qty
        closes = {s: r.Close for s, r in nxt.items()}
        fb.mark(closes)
        gross = sum(p.qty * closes[s] for s, p in pos.items())
        assert gross <= 1.0 * fb.account().equity * 1.15            # 1x at fill; prices move after
        engine = PortfolioBacktester(ModernConfig()).run({s: d.iloc[:k + 2] for s, d in data.items()},
                                                         groups, None, start)
        want = {p["ticker"]: p["shares"] for p in engine.open_positions}
        days += 1
        matched += want == {s: p.qty for s, p in pos.items()}
    assert days == 300 and matched / days > 0.9
    assert math.isclose(fb.account().equity, engine.state["equity"], rel_tol=0.01)


def test_dry_run_and_kill_switch_send_nothing(tmp_path):
    data, groups = _universe(650)
    start = str(data["AAA"].index[500].date())
    for k in range(500, 640):
        hist = {s: d.iloc[:k + 1] for s, d in data.items()}
        res = PortfolioBacktester(ModernConfig()).run(hist, groups, None, start)
        if res.pending_orders:
            break
    fb = FakeBroker()
    rep = run_evening(fb, hist, groups, None, start, 100_000.0, tmp_path, execute_orders=False)
    assert rep["orders"] and fb.sent == []
    (tmp_path / "KILL").write_text("")
    rep = run_evening(fb, hist, groups, None, start, 100_000.0, tmp_path, execute_orders=True)
    assert rep["orders"] and fb.sent == []


def test_rerunning_the_evening_does_not_duplicate_orders(tmp_path):
    data, groups = _universe(650)
    start = str(data["AAA"].index[500].date())
    for k in range(500, 640):
        hist = {s: d.iloc[:k + 1] for s, d in data.items()}
        if PortfolioBacktester(ModernConfig()).run(hist, groups, None, start).pending_orders:
            break
    fb = FakeBroker()
    for _ in range(3):
        run_evening(fb, hist, groups, None, start, 100_000.0, tmp_path, execute_orders=True)
    buys = [o for o in fb.open_orders() if o.side == "buy"]
    assert len(buys) == len({o.symbol for o in buys}) and buys


def test_evening_refuses_while_market_open_and_morning_while_closed(tmp_path):
    data, groups = _universe(600)
    fb = FakeBroker(is_open=True)
    rep = run_evening(fb, data, groups, None, str(data["AAA"].index[500].date()), 100_000.0, tmp_path, True)
    assert any("market is open" in b for b in rep["blocked"]) and fb.sent == []
    fb.is_open = False
    rep = run_morning(fb, groups, tmp_path, True)
    assert any("market is closed" in b for b in rep["blocked"])


def test_evening_keeps_an_identical_live_stop_instead_of_replacing_it():
    t = tgt({"AAA": 50}, stops={"AAA": 95.0})
    live = Order("s1", "tt-old-AAA-stop", "AAA", "sell", "stop", 50, "gtc", 95.0, "new")
    p = plan_evening(t, {"AAA": Position("AAA", 50, 99.0)}, [live], 100_000, LIM, "r2", False)
    assert p.cancels == [] and p.orders == []


def test_failed_submit_after_cancel_is_reported_and_morning_restores_stop(tmp_path):
    """If a replacement stop is rejected, the result says so, and the
    morning run puts a stop back on the position."""
    t = tgt({"AAA": 50}, stops={"AAA": 96.0})
    fb = FakeBroker(pos={"AAA": Position("AAA", 50, 99.0)}, is_open=False)
    old = fb.submit("AAA", 50, "sell", "stop", "gtc", "tt-old-AAA-stop", 95.0)
    from turtle_bot.bot import execute
    p = plan_evening(t, fb.positions(), fb.open_orders(), 100_000, LIM, "r3", False)
    fb.submit("XXX", 1, "buy", "market", "day", p.orders[0]["client_order_id"])   # force a duplicate id
    res = execute(p, fb, True, tmp_path)
    assert any("submit_failed" in r for r in res)
    assert not [o for o in fb.open_orders() if o.symbol == "AAA"]              # unprotected now ...
    fb.is_open = True
    m = plan_morning(t, fb.positions(), fb.open_orders(), {}, LIM, "m3")
    execute(m, fb, True, tmp_path)
    assert [o.stop_price for o in fb.open_orders() if o.symbol == "AAA"] == [96.0]   # ... restored
