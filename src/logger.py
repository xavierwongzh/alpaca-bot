"""
Logging utilities:
  - a standard console logger
  - an append-only CSV trade log
  - daily JSON portfolio snapshots
"""
from __future__ import annotations

import csv
import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

from rich.logging import RichHandler


_TRADE_LOG_FIELDS = [
    "timestamp",
    "mode",           # open | midday
    "event",          # placed | filled | rejected | skipped | error
    "ticker",
    "side",
    "qty",
    "entry_price",
    "stop_price",
    "target_price",
    "notional",
    "order_id",
    "detail",
]


def get_logger(name: str = "alpaca-bot") -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    handler = RichHandler(rich_tracebacks=True, show_path=False, markup=True)
    handler.setFormatter(logging.Formatter("%(message)s", datefmt="[%X]"))
    logger.addHandler(handler)
    logger.propagate = False
    return logger


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def init_trade_log(path: str) -> None:
    """
    Ensure the trade-log CSV exists with the current header. If an older log
    with a different header is found (e.g. after adding a column), it is backed
    up to <path>.bak so new rows can't misalign with a stale header.
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if not os.path.exists(path):
        with open(path, "w", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=_TRADE_LOG_FIELDS).writeheader()
        return

    # File exists — verify the header matches the current schema.
    with open(path, newline="", encoding="utf-8") as f:
        first = f.readline().strip()
    if first.split(",") != _TRADE_LOG_FIELDS:
        backup = f"{path}.bak"
        i = 1
        while os.path.exists(backup):
            backup = f"{path}.{i}.bak"
            i += 1
        os.replace(path, backup)
        get_logger().warning("Trade-log header changed; archived old log to %s", backup)
        with open(path, "w", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=_TRADE_LOG_FIELDS).writeheader()


def log_trade_event(path: str, **kwargs: Any) -> None:
    """Append a single row to the trade log. Unknown keys are ignored."""
    init_trade_log(path)
    row = {k: "" for k in _TRADE_LOG_FIELDS}
    row["timestamp"] = _utc_now_iso()
    for k, v in kwargs.items():
        if k in row:
            row[k] = v
    with open(path, "a", newline="", encoding="utf-8") as f:
        csv.DictWriter(f, fieldnames=_TRADE_LOG_FIELDS).writerow(row)


def write_snapshot(snapshots_dir: str, snapshot: dict[str, Any]) -> str:
    """Write a daily JSON portfolio snapshot. Returns the file path."""
    os.makedirs(snapshots_dir, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M%S")
    path = os.path.join(snapshots_dir, f"portfolio_{stamp}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, indent=2, default=str)
    return path
