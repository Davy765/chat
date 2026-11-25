import math
import pathlib
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from turtle_bot.data import synthetic_ohlc  # noqa: E402
from turtle_bot.portfolio import ModernConfig, PortfolioBacktester, _Market, sizing_equity  # noqa: E402

FREE = dict(slippage_bps=0.0, commission_per_share=0.0, use_trend_filter=False)
VOL = 1e7


def bars(rows, start="2020-01-01", volume=VOL):
    idx = pd.bdate_range(start, periods=len(rows))
    df = pd.DataFrame(rows, columns=["Open", "High", "Low", "Close"], index=idx)
    df["Volume"] = volume
    return df


def flat_then(after, n_flat=110, px=100.0):
    """Quiet bars with H-L = 1 (so N = 1) followed by custom bars."""
    return bars([(px, px + 0.5, px - 0.5, px)] * n_flat + after)


def n_at(df, j):
    """The engine's N (with floor) at bar j's close."""
    return _Market("X", "g", df, ModernConfig(**FREE)).n[j]


BRK = 110          # index of the breakout bar in flat_then fixtures


def run1(df, **kw):
    cfg = ModernConfig(**{**FREE, **kw})
    return PortfolioBacktester(cfg).run({"X": df}, {"X": "g"})


# ------------------------------------------------------------- entries
def test_entry_on_close_breakout_fills_next_open():
    df = flat_then([(100.2, 101.2, 100.1, 101.0), (101.3, 101.6, 101.1, 101.5)])
    res = run1(df, max_units=1, use_trailing=False)
    pos = res.open_positions[0]
    n = n_at(df, BRK)
    assert pos["avg_entry"] == 101.3                 # next open, not the breakout level
    assert math.isclose(pos["stop"], 101.3 - 3 * n)
    assert pos["shares"] == math.floor(0.005 * 100_000 / (3 * n))


def test_intraday_poke_without_close_is_not_an_entry():
    df = flat_then([(100.2, 103.0, 100.1, 100.4)] + [(100.4, 100.5, 100.3, 100.4)] * 3)
    assert run1(df).open_positions == [] and run1(df).trades == []


def test_trend_filter_blocks_longs_in_downtrend():
    down = [(150 - k * 0.25,) * 4 for k in range(200)]
    down = [(a, a + 0.5, a - 0.5, a) for (a, _, _, _) in down]
    flat = [(100.0, 100.5, 99.5, 100.0)] * 60
    brk = [(100.2, 101.2, 100.1, 101.0), (101.3, 101.6, 101.1, 101.5)]
    df = bars(down + flat + brk)
    assert run1(df, use_trend_filter=True).open_positions == []
    assert len(run1(df, use_trend_filter=False).open_positions) == 1


# --------------------------------------------------------------- stops
def test_stop_fills_at_stop_intraday_and_at_open_on_gap():
    entry = [(100.2, 101.2, 100.1, 101.0), (101.3, 101.6, 101.1, 101.5)]
    df = flat_then(entry + [(101.0, 101.2, 97.0, 97.5)])
    touch = run1(df, max_units=1, use_trailing=False).trades[0]
    assert touch.exit_reason == "stop" and math.isclose(touch.exit_price, 101.3 - 3 * n_at(df, BRK))
    gap = run1(flat_then(entry + [(95.0, 95.5, 94.0, 95.2)]), max_units=1).trades[0]
    assert gap.exit_reason == "stop_gap" and gap.exit_price == 95.0
    assert gap.r_multiple < -1.0


def test_trailing_stop_ratchets_to_highest_close_minus_3n():
    entry = [(100.2, 101.2, 100.1, 101.0), (101.3, 101.6, 101.1, 101.5)]
    rally = [(101.5 + k, 102.0 + k, 101.4 + k, 101.9 + k) for k in range(1, 6)]   # closes up to 106.9
    df = flat_then(entry + rally + [(106.5, 106.6, 103.0, 103.5)])
    t = run1(df, max_units=1).trades[0]
    assert math.isclose(t.exit_price, 106.9 - 3 * n_at(df, BRK))          # N frozen at entry
    assert t.exit_reason == "stop"


def test_channel_exit_on_close_fills_next_open():
    entry = [(100.2, 101.2, 100.1, 101.0), (101.3, 101.6, 101.1, 101.5)]
    hold = [(101.5, 102.0, 101.0, 101.5)] * 25
    brk = [(101.2, 101.3, 100.5, 100.8), (100.6, 100.9, 100.4, 100.7)]          # close < 20d low (101.0)
    df = flat_then(entry + hold + brk)
    t = run1(df, max_units=1, use_trailing=False, stop_n=10).trades[0]
    assert t.exit_reason == "channel_exit" and t.exit_price == 100.6


# ---------------------------------------------------------------- adds
def test_adds_every_1n_on_close_max_3_units_and_shared_stop():
    entry = [(100.2, 101.2, 100.1, 101.0), (101.3, 101.6, 101.1, 101.5)]
    up = [(102.4, 102.9, 102.3, 102.8), (102.9, 103.0, 102.8, 102.9),      # close >= 101.3+1 -> add next open
          (103.4, 103.9, 103.3, 103.8), (103.9, 104.0, 103.8, 103.9),
          (104.4, 105.9, 104.3, 105.8), (105.9, 106.0, 105.8, 105.9)]
    df = flat_then(entry + up)
    res = run1(df, use_trailing=False)
    pos = res.open_positions[0]
    # fills: 101.3; close 102.8 >= 101.3+1.01 -> 102.9; close 103.9 < 102.9+1.01 (no add);
    # close 105.8 -> 105.9. Shared stop = newest fill - 3N(entry).
    assert pos["units"] == 3
    assert math.isclose(pos["stop"], 105.9 - 3 * n_at(df, BRK))


# ------------------------------------------------------- portfolio caps
def _many(n, **kw):
    brk = [(100.2, 101.2, 100.1, 101.0), (101.3, 101.6, 101.1, 101.5)]
    data = {f"M{k}": flat_then(brk) for k in range(n)}
    return PortfolioBacktester(ModernConfig(**{**FREE, **kw})).run(data, kw.pop("groups", {t: "g" for t in data}))


def test_heat_cap_limits_simultaneous_entries():
    res = _many(10, heat_cap=0.02, group_unit_cap=99, total_long_units=99, max_leverage=99)
    assert len(res.open_positions) == 4                                     # 4 x ~0.5% <= 2%


def test_group_cap():
    res = _many(8, group_unit_cap=6, heat_cap=1.0, total_long_units=99, max_leverage=99)
    assert len(res.open_positions) == 6


def test_total_long_unit_cap():
    data = {f"M{k}": flat_then([(100.2, 101.2, 100.1, 101.0), (101.3, 101.6, 101.1, 101.5)]) for k in range(15)}
    groups = {t: t for t in data}
    res = PortfolioBacktester(ModernConfig(**FREE, heat_cap=1.0, max_leverage=99)).run(data, groups)
    assert len(res.open_positions) == 12


def test_leverage_cap_shrinks_orders():
    res = _many(10, heat_cap=1.0, group_unit_cap=99, total_long_units=99, max_leverage=1.0)
    gross = sum(p["shares"] * 101.5 for p in res.open_positions)
    assert gross <= 1.0 * 100_000 * 1.01


# -------------------------------------------------------------- drawdown
@pytest.mark.parametrize("eq,expected", [(100, 100), (95, 95), (90, 72), (85, 68), (80, 48), (70, 28), (120, 120)])
def test_rulebook_drawdown_sizing(eq, expected):
    assert math.isclose(sizing_equity(ModernConfig(), eq, 100), expected)


def test_drawdown_pause_blocks_new_trades_for_10_sessions():
    a_entry = [(100.2, 101.2, 100.1, 101.0), (101.3, 101.6, 101.1, 101.5), (101.0, 101.1, 97.0, 97.5)]
    quiet = [(100.0, 100.5, 99.5, 100.0)]
    brk = [(100.2, 101.2, 100.1, 101.0)]
    # market B breaks out 3 sessions after A's stop-out (inside the pause) ...
    b_rows = quiet * 3 + brk + [(101.3, 101.6, 101.1, 101.5)] * 2
    a = flat_then(a_entry + quiet * 3)
    b = flat_then(b_rows)
    cfg = dict(pause_dd=0.004, max_units=1)
    res = PortfolioBacktester(ModernConfig(**FREE, **cfg)).run({"A": a, "B": b}, {"A": "x", "B": "y"})
    assert any("paused" in m for m in res.log)
    assert all(p["ticker"] != "B" for p in res.open_positions)
    # ... and is taken without the pause
    res2 = PortfolioBacktester(ModernConfig(**FREE, max_units=1)).run({"A": a, "B": b}, {"A": "x", "B": "y"})
    assert any(p["ticker"] == "B" for p in res2.open_positions)


# ----------------------------------------------------------------- data
def test_illiquid_market_never_trades():
    df = flat_then([(100.2, 101.2, 100.1, 101.0), (101.3, 101.6, 101.1, 101.5)])
    df["Volume"] = 100                                                     # ~$10k a day
    assert run1(df).open_positions == []


def test_zero_volume_bad_print_is_ignored():
    entry = [(100.2, 101.2, 100.1, 101.0), (101.3, 101.6, 101.1, 101.5)]
    df = flat_then(entry + [(60.0, 60.0, 60.0, 60.0), (101.5, 101.8, 101.4, 101.7)])
    df.loc[df.index[-2], "Volume"] = 0                                     # CPER-style bad print
    res = run1(df, max_units=1)
    assert res.trades == [] and len(res.open_positions) == 1


# ------------------------------------------------- look-ahead & accounting
def _universe(seed=0, n=1500):
    data, groups = {}, {}
    for k in range(5):
        df = synthetic_ohlc(n, seed=seed * 10 + k, vol=0.012 + 0.004 * k, drift=0.0002)
        df["Volume"] = VOL
        data[f"S{k}"] = df
        groups[f"S{k}"] = "eq" if k < 3 else f"g{k}"
    return data, groups


@pytest.mark.parametrize("cut", [400, 1111])
def test_no_lookahead_truncation(cut):
    data, groups = _universe(1)
    rate = pd.Series(np.linspace(0.5, 5, 1500), index=data["S0"].index)
    cfg = ModernConfig(max_leverage=2.0)
    full = PortfolioBacktester(cfg).run(data, groups, rate)
    part = PortfolioBacktester(cfg).run({t: d.iloc[:cut] for t, d in data.items()}, groups, rate.iloc[:cut])
    pd.testing.assert_series_equal(full.equity.iloc[:cut], part.equity, check_freq=False)
    closed = [t.__dict__ for t in full.trades if t.exit_date <= data["S0"].index[cut - 1]]
    assert closed == [t.__dict__ for t in part.trades]


def test_no_lookahead_future_shock():
    data, groups = _universe(2)
    shocked = {t: d.copy() for t, d in data.items()}
    for d in shocked.values():
        d.iloc[900:, :4] *= 0.6
    a = PortfolioBacktester().run(data, groups)
    b = PortfolioBacktester().run(shocked, groups)
    pd.testing.assert_series_equal(a.equity.iloc[:900], b.equity.iloc[:900])


@pytest.mark.parametrize("lev", [1.0, 2.0])
def test_equity_identity(lev):
    comm = 0.005
    data, groups = _universe(3)
    rate = pd.Series(3.0, index=data["S0"].index)
    res = PortfolioBacktester(ModernConfig(commission_per_share=comm, max_leverage=lev)).run(data, groups, rate)
    closed = sum(t.pnl for t in res.trades)
    open_pnl = sum(p["unrealized_pnl"] - p["commissions"] for p in res.open_positions)
    interest = res.daily["interest"].sum()
    assert math.isclose(res.equity.iloc[-1], 100_000 + closed + open_pnl + interest, rel_tol=1e-9)


def test_margin_is_charged_when_borrowing():
    data, groups = _universe(4)
    rate = pd.Series(4.0, index=data["S0"].index)
    cfg = ModernConfig(max_leverage=2.0, heat_cap=1.0, risk_per_unit=0.03)
    res = PortfolioBacktester(cfg).run(data, groups, rate)
    d = res.daily
    borrowing = (d["gross_leverage"].shift(1) > 1.0)
    assert (d.loc[borrowing, "interest"] < 0).all() and borrowing.any()


def test_start_date_resets_capital():
    data, groups = _universe(5)
    start = data["S0"].index[700]
    res = PortfolioBacktester().run(data, groups, start=str(start.date()))
    assert res.equity.index[0] == start
    assert all(t.entry_date >= start for t in res.trades)
