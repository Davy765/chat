"""Price data loading and validation.

All OHLC columns must be on the SAME adjustment basis. Mixing split/dividend
adjusted closes with unadjusted highs/lows creates fake gaps and fake
breakouts around every ex-dividend date (a classic silent bug).
"""

from __future__ import annotations

import pathlib

import numpy as np
import pandas as pd

DATA_DIR = pathlib.Path(__file__).resolve().parents[1] / "data"

REQUIRED = ["Open", "High", "Low", "Close"]


def adjust_ohlc(df: pd.DataFrame) -> pd.DataFrame:
    """Scale Open/High/Low/Close by Adj Close / Close so every column is on
    the total-return (dividend + split adjusted) basis.

    Note: back-adjusted prices are fine for signals and P&L in percent terms,
    but the absolute price level of old bars is NOT what traded at the time.
    When we move to options, strikes must be chosen from UNADJUSTED prices.
    """
    if "Adj Close" not in df.columns:
        return df.copy()
    out = df.copy()
    factor = out["Adj Close"] / out["Close"]
    for col in REQUIRED:
        out[col] = out[col] * factor
    return out.drop(columns=["Adj Close"])


def validate_ohlc(df: pd.DataFrame) -> pd.DataFrame:
    missing = [c for c in REQUIRED if c not in df.columns]
    if missing:
        raise ValueError(f"missing columns: {missing}")
    if not isinstance(df.index, pd.DatetimeIndex):
        raise ValueError("index must be a DatetimeIndex")
    if not df.index.is_monotonic_increasing:
        raise ValueError("index must be sorted ascending (oldest first)")
    if df.index.has_duplicates:
        raise ValueError("duplicate dates in index")
    if df[REQUIRED].isna().any().any():
        raise ValueError("NaN in OHLC data")
    if (df[REQUIRED] <= 0).any().any():
        raise ValueError("non-positive prices")
    tol = 1e-9 * df["Close"].abs()
    bad_hi = df["High"] + tol < df[["Open", "Close"]].max(axis=1)
    bad_lo = df["Low"] - tol > df[["Open", "Close"]].min(axis=1)
    if bad_hi.any() or bad_lo.any():
        dates = list(df.index[bad_hi | bad_lo][:5])
        raise ValueError(f"inconsistent OHLC (High/Low outside Open/Close) on {dates}")
    return df


def load_csv(path: str, adjust: bool = True) -> pd.DataFrame:
    df = pd.read_csv(path, parse_dates=["Date"], index_col="Date").sort_index()
    df = adjust_ohlc(df) if adjust else df.drop(columns=["Adj Close"], errors="ignore")
    return validate_ohlc(df[REQUIRED + [c for c in ["Volume"] if c in df.columns]])


def synthetic_ohlc(n: int = 1500, seed: int = 0, start_price: float = 100.0,
                   drift: float = 0.0003, vol: float = 0.012) -> pd.DataFrame:
    """Random-walk OHLC data for tests and demos (no edge by construction)."""
    rng = np.random.default_rng(seed)
    rets = rng.normal(drift, vol, n)
    close = start_price * np.exp(np.cumsum(rets))
    open_ = np.empty(n)
    open_[0] = start_price
    open_[1:] = close[:-1] * np.exp(rng.normal(0, vol / 3, n - 1))
    hi = np.maximum(open_, close) * np.exp(np.abs(rng.normal(0, vol / 2, n)))
    lo = np.minimum(open_, close) * np.exp(-np.abs(rng.normal(0, vol / 2, n)))
    idx = pd.bdate_range("2010-01-01", periods=n)
    return pd.DataFrame({"Open": open_, "High": hi, "Low": lo, "Close": close}, index=idx)


def load_rate_csv(path: str) -> pd.Series:
    """Annual cash rate in percent, indexed by date (e.g. FRED DTB3, the
    3-month T-bill rate). FRED marks holidays with '.' or blanks; those rows
    are dropped and the engine carries the last known value forward."""
    raw = pd.read_csv(path)
    date_col = raw.columns[0]
    s = pd.to_numeric(raw.iloc[:, 1], errors="coerce")
    s.index = pd.to_datetime(raw[date_col])
    return s.dropna().sort_index().rename("cash_rate_pct")


def default_cash_rate() -> pd.Series | None:
    """The 3-month T-bill series in data/DTB3.csv if it has been downloaded
    (scripts/download_rates.py), else None (no interest)."""
    path = DATA_DIR / "DTB3.csv"
    return load_rate_csv(str(path)) if path.exists() else None
