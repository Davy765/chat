import math
import pathlib
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from turtle_bot import TurtleBacktester, TurtleConfig  # noqa: E402
from turtle_bot.data import adjust_ohlc, synthetic_ohlc, validate_ohlc  # noqa: E402
from turtle_bot.indicators import donchian, true_range, wilder_atr  # noqa: E402

FRICTIONLESS = dict(slippage_bps=0.0, commission_per_share=0.0)


def bars(rows, start="2020-01-01"):
    """rows: list of (open, high, low, close)."""
    idx = pd.bdate_range(start, periods=len(rows))
    return pd.DataFrame(rows, columns=["Open", "High", "Low", "Close"], index=idx)


def flat_then(rows_after, n_flat=60, px=100.0, rng=1.0):
    """n_flat quiet bars (H-L = rng, so N ~= rng) followed by custom bars."""
    flat = [(px, px + rng / 2, px - rng / 2, px)] * n_flat
    return bars(flat + rows_after)


def flat_deep_low_then(rows_after):
    """Like flat_then, but one bar 9 days before the end of the flat period
    spikes down to 90, so the 10-day exit channel is far away and the 2N stop
    is the binding exit level. Use with allow_short=False."""
    flat = [(100.0, 100.5, 99.5, 100.0)] * 60
    flat[-9] = (100.0, 100.5, 90.0, 100.0)
    return bars(flat + rows_after)


LONG_ONLY = dict(allow_short=False, use_skip_filter=False)


# ---------------------------------------------------------------- indicators
def test_true_range_uses_previous_close():
    df = bars([(10, 11, 9, 10), (14, 15, 13, 14)])  # gap up
    tr = true_range(df)
    assert tr.iloc[0] == 2
    assert tr.iloc[1] == 5  # |15 - 10|, not 15 - 13


def test_wilder_atr_matches_hand_calculation():
    df = synthetic_ohlc(60, seed=3)
    tr = true_range(df).to_numpy()
    n = wilder_atr(df, 20).to_numpy()
    assert np.isnan(n[18]) and math.isclose(n[19], tr[:20].mean())
    expect = n[19]
    for i in range(20, 60):
        expect = (19 * expect + tr[i]) / 20
        assert math.isclose(n[i], expect)


def test_donchian_excludes_current_bar():
    df = synthetic_ohlc(50, seed=1)
    up, lo = donchian(df, 20)
    for i in range(20, 50):
        assert up.iloc[i] == df["High"].iloc[i - 20:i].max()
        assert lo.iloc[i] == df["Low"].iloc[i - 20:i].min()
    assert up.iloc[:20].isna().all()


# ------------------------------------------------------------------- entries
def test_long_entry_fills_at_breakout_level_not_close():
    # 60 quiet bars (High = 100.5), then a bar trading through 100.5 to 103.
    df = flat_then([(100.2, 103, 100.2, 102.8)] + [(102.8, 103, 102.5, 102.9)] * 3)
    res = TurtleBacktester(TurtleConfig(system="S1", max_leverage=100, **FRICTIONLESS)).run(df)
    pos = res.daily["position"]
    assert pos.iloc[59] == 0 and pos.iloc[60] > 0
    # first unit at the channel (100.5); pyramids at +0.5N = 101.0, 101.5, 102.0
    assert res.open_position["units"] == 4
    assert math.isclose(res.open_position["avg_entry"], (100.5 + 101 + 101.5 + 102) / 4, rel_tol=1e-9)


def test_gap_entry_fills_at_open():
    df = flat_then([(104, 105, 103.8, 104.5)] + [(104.5, 104.8, 104.2, 104.5)] * 3)
    res = TurtleBacktester(TurtleConfig(system="S2", max_units=1, max_leverage=100, **FRICTIONLESS,
                                        entry_len=20, exit_len=10)).run(df)
    assert math.isclose(res.open_position["avg_entry"], 104.0)


def test_no_entry_when_high_only_equals_channel():
    df = flat_then([(100, 100.5, 99.6, 100.2)] * 3)
    res = TurtleBacktester(TurtleConfig(**FRICTIONLESS)).run(df)
    assert (res.daily["position"] == 0).all()


def test_unit_size_uses_prior_equity_and_prior_n():
    df = flat_then([(100.2, 100.8, 100.0, 100.7)] * 2)
    cfg = TurtleConfig(max_units=1, max_leverage=100, **FRICTIONLESS)
    res = TurtleBacktester(cfg).run(df)
    n_prev = res.daily["N"].iloc[59]
    expected = math.floor(0.01 * 100_000 / n_prev)
    assert res.open_position["shares"] == expected


def test_leverage_cap_limits_notional():
    df = flat_then([(100.2, 103, 100.0, 102.8)] + [(102.8, 103, 102.5, 102.9)] * 3)
    res = TurtleBacktester(TurtleConfig(max_leverage=1.0, **FRICTIONLESS)).run(df)
    notional = res.open_position["shares"] * df["Close"].iloc[-1]
    assert notional <= 1.0 * 100_000 * 1.03  # cap measured at fill price


# --------------------------------------------------------------------- stops
def test_stop_is_two_n_below_entry_and_fills_at_stop():
    # Enter at 100.5 with N ~ 1 -> stop ~ 98.5. Next bar trades down to 97.
    df = flat_deep_low_then([(100.2, 100.8, 100.1, 100.7), (100.6, 100.7, 97.0, 97.5)])
    res = TurtleBacktester(TurtleConfig(max_units=1, max_leverage=100, **LONG_ONLY, **FRICTIONLESS)).run(df)
    t = res.trades[0]
    n = res.daily["N"].iloc[59]
    assert math.isclose(t.avg_entry, 100.5)
    assert math.isclose(t.exit_price, 100.5 - 2 * n)
    assert t.exit_reason == "stop_2N"
    assert math.isclose(t.r_multiple, -1.0)


def test_stop_gap_fills_at_open_not_at_stop():
    df = flat_deep_low_then([(100.2, 100.8, 100.1, 100.7), (95.0, 95.5, 94.0, 95.2)])
    res = TurtleBacktester(TurtleConfig(max_units=1, max_leverage=100, **LONG_ONLY, **FRICTIONLESS)).run(df)
    t = res.trades[0]
    assert t.exit_price == 95.0 and t.exit_reason == "stop_2N_gap"
    assert t.r_multiple < -1.0  # gap losses exceed 1R; a simulator that caps at -1R is inflating


def test_pyramid_raises_stop_for_all_units():
    df = flat_then([(100.2, 101.2, 100.1, 101.1)] + [(101.1, 101.2, 101.0, 101.1)] * 2)
    res = TurtleBacktester(TurtleConfig(max_leverage=100, **FRICTIONLESS)).run(df)
    n = res.daily["N"].iloc[59]
    assert res.open_position["units"] == 2  # 100.5 and 101.0
    assert math.isclose(res.open_position["stop"], 101.0 - 2 * n)


def test_exit_channel_10_day_low():
    # Enter, drift up slowly (no stop hit), then break the 10-day low.
    up = [(100.6 + 0.05 * k, 100.8 + 0.05 * k, 100.5 + 0.05 * k, 100.7 + 0.05 * k) for k in range(15)]
    df = flat_then([(100.2, 100.8, 100.1, 100.7)] + up + [(101.2, 101.3, 100.0, 100.2)])
    res = TurtleBacktester(TurtleConfig(max_units=1, stop_n=10, max_leverage=100, **FRICTIONLESS)).run(df)
    t = res.trades[0]
    assert t.exit_reason == "exit_channel"
    assert math.isclose(t.exit_price, df["Low"].iloc[-11:-1].min())


def test_same_bar_entry_and_stop_is_pessimistic():
    # One bar breaks out to 101 and also trades down to 97: assume stopped out.
    df = flat_deep_low_then([(100.0, 101.0, 97.0, 98.0)])
    res = TurtleBacktester(TurtleConfig(max_units=1, max_leverage=100, **LONG_ONLY, **FRICTIONLESS)).run(df)
    assert len(res.trades) == 1 and res.trades[0].same_bar_stop
    assert res.trades[0].pnl < 0


# ------------------------------------------------------------- look-ahead
@pytest.mark.parametrize("system", ["S1", "S2"])
@pytest.mark.parametrize("cut", [150, 400, 777])
def test_no_lookahead_truncation_invariance(system, cut):
    """Results up to day k must not depend on any bar after k."""
    df = synthetic_ohlc(1000, seed=11, vol=0.015)
    cfg = TurtleConfig(system=system, max_leverage=3)
    full = TurtleBacktester(cfg).run(df)
    part = TurtleBacktester(cfg).run(df.iloc[:cut])
    pd.testing.assert_series_equal(full.equity.iloc[:cut], part.equity, check_freq=False)
    closed_before = [t for t in full.trades if t.exit_date <= df.index[cut - 1]]
    assert [t.__dict__ for t in closed_before] == [t.__dict__ for t in part.trades]


def test_no_lookahead_future_shock():
    """Replace all bars after k with a crash: nothing before k may change."""
    df = synthetic_ohlc(800, seed=5)
    shocked = df.copy()
    shocked.iloc[500:] *= 0.5
    a = TurtleBacktester(TurtleConfig()).run(df)
    b = TurtleBacktester(TurtleConfig()).run(shocked)
    pd.testing.assert_series_equal(a.equity.iloc[:500], b.equity.iloc[:500])


# ---------------------------------------------------- accounting / inflation
@pytest.mark.parametrize("seed", [0, 1, 2])
def test_equity_identity(seed):
    """final equity = initial + closed P&L + open P&L (no money created).
    Open P&L is recomputed independently from shares, avg entry and last close."""
    comm = 0.005
    df = synthetic_ohlc(1500, seed=seed, vol=0.014)
    res = TurtleBacktester(TurtleConfig(commission_per_share=comm)).run(df)
    closed = sum(t.pnl for t in res.trades)
    open_pnl = 0.0
    if res.open_position:
        op = res.open_position
        open_pnl = (op["direction"] * (df["Close"].iloc[-1] - op["avg_entry"]) * op["shares"]
                    - op["shares"] * comm)  # entry commissions already paid
    assert math.isclose(res.equity.iloc[-1], 100_000 + closed + open_pnl, rel_tol=1e-9)


def test_costs_reduce_results():
    df = synthetic_ohlc(1500, seed=7, vol=0.014)
    free = TurtleBacktester(TurtleConfig(**FRICTIONLESS)).run(df).equity.iloc[-1]
    costly = TurtleBacktester(TurtleConfig(slippage_bps=5, commission_per_share=0.01)).run(df).equity.iloc[-1]
    assert costly < free


def test_no_edge_on_random_walk():
    """On driftless random walks the strategy must not show a systematic edge.
    A buggy (look-ahead) simulator typically prints large profits here."""
    finals = []
    for seed in range(20):
        df = synthetic_ohlc(1500, seed=100 + seed, drift=0.0, vol=0.012)
        finals.append(TurtleBacktester(TurtleConfig()).run(df).equity.iloc[-1] / 100_000 - 1)
    assert np.mean(finals) < 0.10


def test_skip_filter_skips_after_winner_and_failsafe_exists():
    df = synthetic_ohlc(3000, seed=21, vol=0.014)
    with_f = TurtleBacktester(TurtleConfig(system="S1", use_skip_filter=True)).run(df)
    without = TurtleBacktester(TurtleConfig(system="S1", use_skip_filter=False)).run(df)
    assert len(with_f.trades) < len(without.trades)
    assert any(t.entry_reason.startswith("failsafe") for t in with_f.trades)
    assert not any(t.entry_reason.startswith("failsafe") for t in without.trades)


# ---------------------------------------------------------------- data
def test_adjust_ohlc_scales_all_columns():
    df = bars([(10, 11, 9, 10)])
    df["Adj Close"] = 5.0
    adj = adjust_ohlc(df)
    assert list(adj.iloc[0]) == [5.0, 5.5, 4.5, 5.0]


def test_validate_rejects_bad_ohlc():
    with pytest.raises(ValueError):
        validate_ohlc(bars([(10, 9, 8, 10)]))


# ======================================================= step 1 additions
# ---------------------------------------------------------- drawdown rule
@pytest.mark.parametrize("equity,expected", [
    (100_000, 100_000), (95_000, 95_000),           # < 10% down: actual equity
    (90_000, 80_000), (85_000, 80_000),             # 10-20% down: 80% of reference
    (80_000, 64_000), (75_000, 64_000),             # 20-30% down: 64%
    (120_000, 120_000),                             # above reference
])
def test_drawdown_rule_sizing_equity(equity, expected):
    bt = TurtleBacktester(TurtleConfig())
    assert math.isclose(bt._sizing_equity(equity, 100_000), expected)


def test_drawdown_rule_off_uses_actual_equity():
    assert TurtleBacktester(TurtleConfig(drawdown_rule=False))._sizing_equity(85_000, 100_000) == 85_000


@pytest.mark.parametrize("ref", ["year_start", "initial", "peak"])
def test_sizing_equity_series_matches_rule(ref):
    """Recompute the sizing basis independently from the equity curve."""
    df = synthetic_ohlc(1500, seed=4, vol=0.016)
    res = TurtleBacktester(TurtleConfig(dd_reference=ref)).run(df)
    eq, sizing = res.equity.to_numpy(), res.daily["sizing_equity"].to_numpy()
    bt = TurtleBacktester(TurtleConfig(dd_reference=ref))
    r = 100_000.0
    for i in range(1, len(df)):
        if ref == "year_start" and df.index[i].year != df.index[i - 1].year:
            r = eq[i - 1]
        elif ref == "peak":
            r = max(r, eq[i - 1])
        assert math.isclose(sizing[i], bt._sizing_equity(eq[i - 1], r))


def test_drawdown_rule_shrinks_units_after_losses():
    df = synthetic_ohlc(2000, seed=9, vol=0.016, drift=-0.0004)
    on = TurtleBacktester(TurtleConfig(max_leverage=100)).run(df)
    off = TurtleBacktester(TurtleConfig(max_leverage=100, drawdown_rule=False)).run(df)
    assert (on.daily["sizing_equity"] < on.equity.shift(1) - 1e-6).any()
    assert not (off.daily["sizing_equity"] < off.equity.shift(1) - 1e-6).any()


# ------------------------------------------------------------- S1 + S2
def test_combined_trades_both_systems_in_one_account():
    df = synthetic_ohlc(2500, seed=12, vol=0.014)
    res = TurtleBacktester(TurtleConfig(system="S1+S2")).run(df)
    systems = set(res.trades_frame().system)
    assert systems == {"S1", "S2"}


def test_combined_respects_gross_leverage_cap():
    df = synthetic_ohlc(2500, seed=12, vol=0.014)
    res = TurtleBacktester(TurtleConfig(system="S1+S2", max_leverage=1.0)).run(df)
    d = res.daily
    # gross shares <= cap * prior equity / cheapest price traded that day
    bound = 1.0 * d["equity"].shift(1) / df["Low"]
    held_before = d["gross_position"].shift(1).fillna(0)
    new_or_flat = d["gross_position"] <= np.maximum(bound, held_before) + 1
    assert new_or_flat.iloc[1:].all()


def test_combined_rejects_custom_channels():
    with pytest.raises(ValueError):
        TurtleConfig(system="S1+S2", entry_len=30).resolved()


def test_r_multiple_uses_full_unit_when_first_fill_is_capped():
    df = synthetic_ohlc(2500, seed=12, vol=0.014)
    res = TurtleBacktester(TurtleConfig(system="S1+S2")).run(df)
    assert res.trades_frame().r_multiple.abs().max() < 30


# ----------------------------------------------------------- cash interest
def test_interest_on_idle_cash_compounds_daily():
    df = flat_then([(100, 100.5, 99.5, 100)] * 200)   # never breaks out
    rate = pd.Series(5.0, index=df.index)
    res = TurtleBacktester(TurtleConfig()).run(df, cash_rate=rate)
    days = np.diff(df.index.to_numpy()).astype("timedelta64[D]").astype(int)
    expected = 100_000 * np.prod(1 + 0.05 * days / 365)  # day-0 rate usable from day 1
    assert len(res.trades) == 0
    assert math.isclose(res.equity.iloc[-1], expected, rel_tol=1e-12)


def test_interest_uses_previous_days_rate():
    df = flat_then([(100, 100.5, 99.5, 100)] * 20)
    rate = pd.Series(0.0, index=df.index)
    rate.iloc[-1] = 50.0                                   # published on the last day
    res = TurtleBacktester(TurtleConfig()).run(df, cash_rate=rate)
    assert res.equity.iloc[-1] == 100_000                  # not usable until the next bar


def test_interest_only_on_idle_equity():
    df = synthetic_ohlc(1500, seed=2, vol=0.014)
    rate = pd.Series(4.0, index=df.index)
    res = TurtleBacktester(TurtleConfig(allow_short=False)).run(df, cash_rate=rate)
    d = res.daily
    idle = (d["equity"].shift(1) - d["gross_position"].shift(1) * df["Close"].shift(1)).clip(lower=0)
    dt = pd.Series(df.index, index=df.index).diff().dt.days
    expected = (idle * 0.04 * dt / 365).fillna(0)          # day-0 rate usable from day 1
    np.testing.assert_allclose(d["interest"].to_numpy(), expected.to_numpy(), rtol=1e-12, atol=1e-9)


# ------------------------------------------- look-ahead with all features on
@pytest.mark.parametrize("cut", [300, 901])
def test_no_lookahead_complete_rules(cut):
    df = synthetic_ohlc(1200, seed=31, vol=0.015)
    rate = pd.Series(np.linspace(1, 6, len(df)), index=df.index)
    cfg = TurtleConfig(system="S1+S2", dd_reference="peak")
    full = TurtleBacktester(cfg).run(df, cash_rate=rate)
    part = TurtleBacktester(cfg).run(df.iloc[:cut], cash_rate=rate.iloc[:cut])
    pd.testing.assert_series_equal(full.equity.iloc[:cut], part.equity, check_freq=False)
    closed = [t.__dict__ for t in full.trades if t.exit_date <= df.index[cut - 1]]
    assert closed == [t.__dict__ for t in part.trades]


@pytest.mark.parametrize("seed", [0, 3])
def test_equity_identity_complete_rules(seed):
    """equity = initial + closed P&L + open P&L + interest."""
    comm = 0.005
    df = synthetic_ohlc(1500, seed=seed, vol=0.014)
    rate = pd.Series(3.0, index=df.index)
    res = TurtleBacktester(TurtleConfig(system="S1+S2", commission_per_share=comm)).run(df, cash_rate=rate)
    closed = sum(t.pnl for t in res.trades)
    open_pnl = sum(op["direction"] * (df["Close"].iloc[-1] - op["avg_entry"]) * op["shares"]
                   - op["shares"] * comm for op in res.open_positions)
    interest = res.daily["interest"].sum()
    assert math.isclose(res.equity.iloc[-1], 100_000 + closed + open_pnl + interest, rel_tol=1e-9)
