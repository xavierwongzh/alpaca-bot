"""
Decision records — the data behind the dashboard's trade drill-down.

For every order the bot places (or would place, in dry-run), we persist a
structured record capturing WHY: the model's rationale plus the full signal
context at decision time. Records are appended to data/decisions/decisions.jsonl
(one JSON object per line) and joined to Alpaca orders by `client_order_id`,
which equals the record id.

Important: a reasoning model's raw chain-of-thought is NOT returned by the API.
The stored `rationale` (the model's written justification) together with this
signal context IS the explanation surfaced to the user — there is no hidden
reasoning trace to show.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from src.execution import ExecutionResult
from src.logger import get_logger
from src.risk import SizedOrder

log = get_logger()


def build_decision_records(
    *,
    sized_orders: list[SizedOrder],
    exec_results: list[ExecutionResult],
    mode: str,
    model: str,
    reasoning_effort: str,
    technicals: dict[str, Any],          # ticker -> Technicals.as_dict()
    flow_by_ticker: dict[str, dict],     # ticker -> FlowSignal.as_dict()
    macro: dict[str, Any],
    market_summary: str,
) -> list[dict[str, Any]]:
    """Assemble one record per sized order, enriched with its execution result."""
    # Index execution results by decision id for an exact join.
    by_decision = {r.decision_id: r for r in exec_results if r.decision_id}
    ts = datetime.now(timezone.utc).isoformat()
    records: list[dict[str, Any]] = []

    for o in sized_orders:
        res = by_decision.get(o.decision_id)
        flow = flow_by_ticker.get(o.ticker)
        record = {
            "id": o.decision_id,
            "timestamp": ts,
            "mode": mode,
            "model": model,
            "reasoning_effort": reasoning_effort,
            "ticker": o.ticker,
            "action": o.action,
            "direction": (flow or {}).get("direction", "long"),
            "confidence": o.confidence,
            "rationale": o.rationale,
            "entry_price": round(o.entry_price, 2),
            "qty": o.qty,
            "stop_price": round(o.stop_price, 2),
            "target_price": round(o.target_price, 2),
            # Signal context at decision time:
            "flow_signal": flow,                       # composite, vol/OI, aggression, cp ratio, IV, top contract
            "technicals": technicals.get(o.ticker),    # price, 20/50 MA, RSI, 52w distance
            "market_context": {"macro": macro, "summary": market_summary},
            # Execution outcome:
            "client_order_id": o.decision_id,
            "order_status": res.status if res else "unknown",
            "alpaca_order_id": (res.order_id if res else None),
        }
        records.append(record)
    return records


def append_decision_records(path: str, records: list[dict[str, Any]]) -> None:
    """Append records to the JSONL log (one object per line)."""
    if not records:
        return
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            for r in records:
                f.write(json.dumps(r, default=str) + "\n")
        log.info("Wrote %d decision record(s) to %s", len(records), os.path.basename(path))
    except Exception as e:  # noqa: BLE001
        log.warning("Failed to write decision records: %s", e)
