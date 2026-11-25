"""Performance metrics. Everything is computed from the daily mark-to-market
equity curve (not from closed trades only, which hides open drawdowns)."""

from __future__ import annotations

import numpy as np
import pandas as pd

from .backtest import BacktestResult

TRADING_DAYS = 252


def max_drawdown(equity: pd.Series) -> float:
    peak = equity.cummax()
    return float((equity / peak - 1).min())


def summarize(res: BacktestResult, prices: pd.DataFrame | None = None) -> dict:
    eq = res.equity.dropna()
    rets = eq.pct_change().dropna()
    years = (eq.index[-1] - eq.index[0]).days / 365.25
    total = eq.iloc[-1] / eq.iloc[0] - 1
    cagr = (eq.iloc[-1] / eq.iloc[0]) ** (1 / years) - 1 if years > 0 else np.nan
    sharpe = rets.mean() / rets.std() * np.sqrt(TRADING_DAYS) if rets.std() > 0 else np.nan
    tf = res.trades_frame()
    out = {
        "start": eq.index[0].date(), "end": eq.index[-1].date(),
        "final_equity": round(float(eq.iloc[-1]), 2),
        "total_return_pct": round(100 * total, 2),
        "cagr_pct": round(100 * cagr, 2),
        "max_drawdown_pct": round(100 * max_drawdown(eq), 2),
        "sharpe": round(float(sharpe), 2),
        "exposure_pct": round(100 * float((res.daily["gross_position"] != 0).mean()), 1),
        "interest_earned": round(float(res.daily["interest"].sum()), 2),
        "trades": len(tf),
    }
    if len(tf):
        wins, losses = tf[tf.pnl > 0], tf[tf.pnl <= 0]
        out.update({
            "win_rate_pct": round(100 * len(wins) / len(tf), 1),
            "profit_factor": round(wins.pnl.sum() / -losses.pnl.sum(), 2) if len(losses) and losses.pnl.sum() < 0 else np.inf,
            "avg_R": round(float(tf.r_multiple.mean()), 2),
            "total_costs": round(float(tf.costs.sum()), 2),
            "same_bar_stop_trades": int(tf.same_bar_stop.sum()),
            "long_trades": int((tf.direction > 0).sum()),
            "short_trades": int((tf.direction < 0).sum()),
            **({f"trades_{k}": int(v) for k, v in tf.system.value_counts().items()}
               if tf.system.nunique() > 1 else {}),
        })
    if prices is not None:
        px = prices["Close"].loc[eq.index[0]:eq.index[-1]]
        bh = px.iloc[-1] / px.iloc[0] - 1
        out["buy_hold_return_pct"] = round(100 * bh, 2)
        out["buy_hold_max_dd_pct"] = round(100 * max_drawdown(px), 2)
    if res.open_positions:
        out["open_positions"] = res.open_positions
    return out
