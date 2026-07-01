"""
Per-day run idempotency markers.

The workflow fires each run mode from several redundant scheduled slots (so a
single dropped GitHub trigger can't cost the run). Only the FIRST successful run
per mode per US market date should do the work; later slots must skip before any
flow scan or OpenAI call, spending zero tokens.

Each successful run writes data/state/last_<mode>_run.txt containing the market
date. A later slot reads it and skips if it matches today. The marker is written
ONLY on success, so a slot that starts but fails before finishing does not block
a later slot from retrying. CI force-adds + commits data/state/ each run, so the
next slot's fresh checkout sees the marker.
"""
from __future__ import annotations

import os
from datetime import date
from typing import Any

from src.logger import get_logger

log = get_logger()


def _marker_path(paths: Any, mode: str) -> str:
    return os.path.join(paths.state_dir, f"last_{mode}_run.txt")


def already_ran_today(paths: Any, mode: str, market_date: date) -> bool:
    """True if a successful `mode` run already completed on `market_date`."""
    path = _marker_path(paths, mode)
    if not os.path.exists(path):
        return False
    try:
        with open(path, encoding="utf-8") as f:
            return f.read().strip() == market_date.isoformat()
    except OSError:
        return False


def mark_ran_today(paths: Any, mode: str, market_date: date) -> None:
    """Record that a `mode` run completed successfully on `market_date`."""
    path = _marker_path(paths, mode)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(market_date.isoformat() + "\n")
    except OSError as e:  # noqa: BLE001
        log.warning("Could not write run marker %s: %s", path, e)
