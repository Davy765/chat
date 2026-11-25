"""Download SPY daily OHLC (with Adj Close) to data/SPY.csv. Requires yfinance.
Read-only market data; no broker connection."""

import pathlib

import yfinance as yf

out = pathlib.Path(__file__).resolve().parents[1] / "data" / "SPY.csv"
df = yf.download("SPY", start="1993-01-29", auto_adjust=False, progress=False)
if hasattr(df.columns, "levels"):
    df.columns = df.columns.get_level_values(0)
df = df[["Open", "High", "Low", "Close", "Adj Close", "Volume"]]
df.index.name = "Date"
out.parent.mkdir(exist_ok=True)
df.to_csv(out)
print(f"saved {len(df)} rows to {out}")
