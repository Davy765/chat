"""Turtle Trading Bot - SPY (underlying only, research/backtest stage).

No broker connectivity. Nothing in this package places real orders.
"""

from .backtest import BacktestResult, TurtleBacktester
from .config import TurtleConfig

__all__ = ["TurtleBacktester", "TurtleConfig", "BacktestResult"]
