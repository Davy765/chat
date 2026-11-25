"""Run the SPY Turtle backtest from a CSV (Date,Open,High,Low,Close,Adj Close,Volume).

    python scripts/run_backtest.py data/SPY.csv --system S1
    python scripts/run_backtest.py data/SPY.csv --system S2 --max-leverage 4
"""

from __future__ import annotations

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from turtle_bot import TurtleBacktester, TurtleConfig  # noqa: E402
from turtle_bot.data import load_csv  # noqa: E402
from turtle_bot.metrics import summarize  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("csv")
    ap.add_argument("--system", default="S1", choices=["S1", "S2"])
    ap.add_argument("--start")
    ap.add_argument("--end")
    ap.add_argument("--max-leverage", type=float, default=1.0)
    ap.add_argument("--slippage-bps", type=float, default=2.0)
    ap.add_argument("--no-short", action="store_true")
    ap.add_argument("--no-skip-filter", action="store_true")
    ap.add_argument("--trades-out")
    args = ap.parse_args()

    df = load_csv(args.csv).loc[args.start:args.end]
    cfg = TurtleConfig(system=args.system, max_leverage=args.max_leverage,
                       slippage_bps=args.slippage_bps, allow_short=not args.no_short,
                       use_skip_filter=not args.no_skip_filter)
    res = TurtleBacktester(cfg).run(df)
    for k, v in summarize(res, df).items():
        print(f"{k:>22}: {v}")
    if args.trades_out:
        res.trades_frame().to_csv(args.trades_out, index=False)
        print(f"trades written to {args.trades_out}")


if __name__ == "__main__":
    main()
