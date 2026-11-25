"""Download the 3-month T-bill rate (FRED series DTB3, % per year) to
data/DTB3.csv. Used as the interest rate on idle cash. Read-only public data."""

import pathlib
import urllib.request

URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DTB3"
out = pathlib.Path(__file__).resolve().parents[1] / "data" / "DTB3.csv"
out.parent.mkdir(exist_ok=True)
with urllib.request.urlopen(URL, timeout=60) as r:
    out.write_bytes(r.read())
print(f"saved {sum(1 for _ in out.open()) - 1} rows to {out}")
