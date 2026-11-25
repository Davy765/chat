# Turtle Trading Bot — SPY (stage 1: underlying only)

Research and backtesting code for the original Turtle Trading rules applied to
SPY. **No broker connection. Nothing here places real orders.**

Current stage: signals and simulation on SPY daily prices. Later stages: CALL/PUT
contracts (60–90 DTE), historical option data, and portfolio risk management.

See **[ROBUSTNESS.md](ROBUSTNESS.md)** for sub-period, parameter, cost, walk-forward and random-entry tests.

See **[AUDIT.md](AUDIT.md)** for the technical audit: verification of Donchian
entries and exits, N/ATR and 2N stops, look-ahead bias and simulator realism,
plus a defect checklist by severity.

## Rules implemented

| Rule | S1 | S2 |
|---|---|---|
| Entry | break of the 20-day high/low (stop order) | 55-day |
| Exit | 10-day opposite channel | 20-day |
| Filter | skip if the last 20-day breakout was a winner (shadow trade), with a 55-day failsafe | none |
| N | 20-day Wilder ATR | same |
| Stop | 2N from the latest unit, shared by all units | same |
| Unit | 1% of equity / (N × $ per point) | same |
| Pyramiding | +1 unit every ½N, max 4 | same |

Documented deviations (see `turtle_bot/config.py`): gross leverage cap (default
1x), slippage of 2 bp per fill, whole shares, worst-case intrabar ordering.

## Layout

```
turtle_bot/
  config.py      all parameters; deviations labeled
  data.py        CSV loading, adjustment, validation, synthetic data
  indicators.py  true range, Wilder N, Donchian (shifted)
  backtest.py    daily event-driven simulator
  metrics.py     CAGR, drawdown, Sharpe, trade stats
scripts/
  download_spy.py    fetch SPY daily data (needs yfinance)
  run_backtest.py    run a backtest and print a summary
  inflation_demo.py  measure how common bugs inflate results
  robustness.py      sub-period / parameter / cost / walk-forward / random-entry tests
reports/             generated robustness tables
tests/test_turtle.py
```

## Usage

```bash
pip install -r requirements.txt
python scripts/download_spy.py
python -m pytest -q
python scripts/run_backtest.py data/SPY.csv --system S1 --start 2000-01-01
python scripts/run_backtest.py data/SPY.csv --system S2 --no-short --max-leverage 4
```
