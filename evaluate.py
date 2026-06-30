"""
Standalone evaluation entry point:  python evaluate.py

Reconciles any newly-closed trades, then computes and writes the evaluation
(data/evaluation/latest.json + summary.txt) and prints the human-readable summary.

The same logic runs automatically at the end of each `python main.py` run.
"""
from __future__ import annotations

import sys

from config import Secrets, get_config
from src.broker import Broker, LiveModeError
from src.evaluation import render_summary, run_evaluation
from src.logger import get_logger
from src.market_data import MarketData
from src.reconcile import reconcile_closed_trades

log = get_logger()


def main() -> int:
    cfg = get_config()
    secrets = Secrets.from_env()
    try:
        broker = Broker(secrets)
    except LiveModeError as e:
        print(f"SAFETY ABORT: {e}")
        return 2

    market_data = MarketData(secrets, cfg.universe.bars_lookback_days)
    reconcile_closed_trades(broker, cfg.paths)
    result = run_evaluation(secrets, cfg, broker=broker, market_data=market_data)
    print(render_summary(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
