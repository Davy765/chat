"""Run the SPY Turtle backtest from a CSV (Date,Open,High,Low,Close,Adj Close,Volume).

    python scripts/run_backtest.py data/SPY.csv --system S1
    python scripts/run_backtest.py data/SPY.csv --system S2 --max-leverage 4
    python scripts/run_backtest.py data/SPY.csv --system S1+S2      # interest on if data/DTB3.csv exists
    python scripts/run_backtest.py data/SPY.csv --no-cash-rate
"""

from __future__ import annotations

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from turtle_bot import TurtleBacktester, TurtleConfig  # noqa: E402
from turtle_bot.data import default_cash_rate, load_csv, load_rate_csv  # noqa: E402
from turtle_bot.metrics import summarize  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("csv")
    ap.add_argument("--system", default="S1", choices=["S1", "S2", "S1+S2"])
    ap.add_argument("--s1-weight", type=float, default=0.5, help="S1+S2 only")
    ap.add_argument("--start")
    ap.add_argument("--end")
    ap.add_argument("--max-leverage", type=float, default=1.0)
    ap.add_argument("--slippage-bps", type=float, default=2.0)
    ap.add_argument("--no-short", action="store_true")
    ap.add_argument("--no-skip-filter", action="store_true")
    ap.add_argument("--no-dd-rule", action="store_true", help="disable the drawdown sizing rule")
    ap.add_argument("--dd-reference", default="year_start", choices=["year_start", "initial", "peak"])
    ap.add_argument("--cash-rate", help="CSV of annual cash rate in %% (default: data/DTB3.csv if present)")
    ap.add_argument("--no-cash-rate", action="store_true", help="no interest on idle cash")
    ap.add_argument("--trades-out")
    args = ap.parse_args()

    df = load_csv(args.csv).loc[args.start:args.end]
    cfg = TurtleConfig(system=args.system, max_leverage=args.max_leverage,
                       slippage_bps=args.slippage_bps, allow_short=not args.no_short,
                       use_skip_filter=not args.no_skip_filter, s1_weight=args.s1_weight,
                       drawdown_rule=not args.no_dd_rule, dd_reference=args.dd_reference)
    if args.no_cash_rate:
        rate = None
    elif args.cash_rate:
        rate = load_rate_csv(args.cash_rate)
    else:
        rate = default_cash_rate()
    print(f"{'cash interest':>22}: {'off' if rate is None else 'on (T-bill)'}")
    res = TurtleBacktester(cfg).run(df, cash_rate=rate)
    for k, v in summarize(res, df).items():
        print(f"{k:>22}: {v}")
    if args.trades_out:
        res.trades_frame().to_csv(args.trades_out, index=False)
        print(f"trades written to {args.trades_out}")


if __name__ == "__main__":
    main()
