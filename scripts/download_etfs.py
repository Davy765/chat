"""Download daily OHLC (with Adj Close) to data/<TICKER>.csv. Requires
yfinance. Read-only market data; no broker.

    python scripts/download_etfs.py            # default universe
    python scripts/download_etfs.py QQQ TLT    # specific tickers

Only COMPLETED sessions are saved: a bar dated today (New York time) is
dropped unless it is after 18:00 ET. An intraday "daily" bar has a
provisional close/high/low; backtesting or generating signals on it is a
look-ahead bug, and it makes results irreproducible.

The CSVs are committed to the repo as a frozen snapshot so every report can
be reproduced exactly. Re-downloading changes them: Yahoo re-adjusts old
prices for dividends from time to time.
"""

import datetime as dt
import pathlib
import sys
from zoneinfo import ZoneInfo

import yfinance as yf

UNIVERSE = ["SPY", "QQQ", "IWM", "DIA", "EFA", "EEM", "TLT", "GLD"]
out_dir = pathlib.Path(__file__).resolve().parents[1] / "data"
out_dir.mkdir(exist_ok=True)

now_et = dt.datetime.now(ZoneInfo("America/New_York"))
last_complete = now_et.date() if now_et.hour >= 18 else now_et.date() - dt.timedelta(days=1)

for t in sys.argv[1:] or UNIVERSE:
    df = yf.download(t, start="1993-01-01", end=str(last_complete + dt.timedelta(days=1)),
                     auto_adjust=False, progress=False)
    if hasattr(df.columns, "levels"):
        df.columns = df.columns.get_level_values(0)
    df = df[["Open", "High", "Low", "Close", "Adj Close", "Volume"]].dropna()
    df = df[df.index.date <= last_complete]
    df.index.name = "Date"
    df.to_csv(out_dir / f"{t}.csv")
    print(f"{t}: {len(df)} rows, {df.index[0].date()} -> {df.index[-1].date()}")
