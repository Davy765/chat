"""Indicators: True Range, Wilder ATR ("N") and Donchian channels.

Timing convention (the single most important thing in this file):

* ``true_range`` / ``wilder_atr`` at row t use bar t itself, i.e. they are
  only KNOWN AFTER THE CLOSE of day t. To size or place stops on day t you
  must use the value from row t-1.
* ``donchian`` at row t is computed from bars t-L .. t-1 (shifted by one), so
  it IS known before day t opens and can be used as a stop-order level that
  triggers intraday on day t.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def true_range(df: pd.DataFrame) -> pd.Series:
    prev_close = df["Close"].shift(1)
    tr = pd.concat(
        [
            df["High"] - df["Low"],
            (df["High"] - prev_close).abs(),
            (df["Low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    # First bar has no previous close: TR = High - Low.
    tr.iloc[0] = df["High"].iloc[0] - df["Low"].iloc[0]
    return tr


def wilder_atr(df: pd.DataFrame, length: int = 20) -> pd.Series:
    """Turtle "N": N_t = ((length - 1) * N_{t-1} + TR_t) / length.

    Seeded with the simple average of the first ``length`` true ranges. Values
    before the seed are NaN (no trading until N exists).

    Note: ``tr.rolling(20).mean()`` (a simple ATR) and
    ``tr.ewm(span=20)`` (alpha = 2/21) are NOT the Turtle N. Wilder smoothing
    is alpha = 1/20.
    """
    tr = true_range(df).to_numpy()
    n = np.full(len(tr), np.nan)
    if len(tr) < length:
        return pd.Series(n, index=df.index, name="N")
    n[length - 1] = tr[:length].mean()
    for i in range(length, len(tr)):
        n[i] = ((length - 1) * n[i - 1] + tr[i]) / length
    return pd.Series(n, index=df.index, name="N")


def donchian(df: pd.DataFrame, length: int) -> tuple[pd.Series, pd.Series]:
    """Highest high / lowest low of the PREVIOUS ``length`` bars (excludes
    today). Using ``rolling(length).max()`` without ``shift(1)`` includes
    today's high, which makes a breakout impossible by construction
    (High_t > max(..., High_t) is never true) or, if compared against Close,
    leaks today's range into today's signal."""
    upper = df["High"].rolling(length, min_periods=length).max().shift(1)
    lower = df["Low"].rolling(length, min_periods=length).min().shift(1)
    return upper, lower
