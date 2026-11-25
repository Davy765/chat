"""Download the 3-month T-bill rate (FRED series DTB3, % per year) to
data/DTB3.csv. Used as the interest rate on idle cash. Read-only public data."""

import pathlib
import sys
import urllib.request

URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DTB3"
# optional: --out data/live
root = pathlib.Path(__file__).resolve().parents[1]
out_dir = root / (sys.argv[sys.argv.index("--out") + 1] if "--out" in sys.argv else "data")
out = out_dir / "DTB3.csv"
out.parent.mkdir(exist_ok=True)
with urllib.request.urlopen(URL, timeout=60) as r:
    out.write_bytes(r.read())
print(f"saved {sum(1 for _ in out.open()) - 1} rows to {out}")
