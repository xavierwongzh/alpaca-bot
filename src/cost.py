"""
OpenAI usage accounting.

Reads the `usage` object off each OpenAI response, converts it to a real dollar
figure using the per-token rates in config.CostConfig, and appends a per-run row
to data/cost_log.csv. This replaces guesswork with the actual numbers.

Notes:
  - Reasoning models report `completion_tokens_details.reasoning_tokens`; those
    are billed as OUTPUT tokens (already included in completion_tokens).
  - `prompt_tokens_details.cached_tokens` are input tokens served from OpenAI's
    prompt cache and billed at the discounted cached rate.
"""
from __future__ import annotations

import csv
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from config import CostConfig
from src.logger import get_logger

log = get_logger()


@dataclass
class Usage:
    label: str            # "decision" | "summary"
    model: str
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    cost_usd: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "model": self.model,
            "input_tokens": self.input_tokens,
            "cached_input_tokens": self.cached_input_tokens,
            "output_tokens": self.output_tokens,
            "reasoning_tokens": self.reasoning_tokens,
            "cost_usd": round(self.cost_usd, 6),
        }


def _safe_int(v: Any) -> int:
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


def _cost(input_tokens: int, cached: int, output_tokens: int,
          in_rate: float, cached_rate: float, out_rate: float) -> float:
    non_cached = max(input_tokens - cached, 0)
    return (
        non_cached * in_rate / 1_000_000.0
        + cached * cached_rate / 1_000_000.0
        + output_tokens * out_rate / 1_000_000.0
    )


def extract_usage(resp: Any, label: str, model: str, cost_cfg: CostConfig) -> Usage:
    """Pull token counts off a Chat Completions response and price them."""
    u = Usage(label=label, model=model)
    usage = getattr(resp, "usage", None)
    if usage is None:
        return u
    u.input_tokens = _safe_int(getattr(usage, "prompt_tokens", 0))
    u.output_tokens = _safe_int(getattr(usage, "completion_tokens", 0))
    prompt_details = getattr(usage, "prompt_tokens_details", None)
    if prompt_details is not None:
        u.cached_input_tokens = _safe_int(getattr(prompt_details, "cached_tokens", 0))
    completion_details = getattr(usage, "completion_tokens_details", None)
    if completion_details is not None:
        u.reasoning_tokens = _safe_int(getattr(completion_details, "reasoning_tokens", 0))

    if label == "decision":
        u.cost_usd = _cost(u.input_tokens, u.cached_input_tokens, u.output_tokens,
                           cost_cfg.decision_input_per_m, cost_cfg.decision_cached_input_per_m,
                           cost_cfg.decision_output_per_m)
    else:
        u.cost_usd = _cost(u.input_tokens, u.cached_input_tokens, u.output_tokens,
                           cost_cfg.summary_input_per_m, cost_cfg.summary_cached_input_per_m,
                           cost_cfg.summary_output_per_m)
    return u


_COST_LOG_FIELDS = [
    "timestamp", "mode", "total_cost_usd",
    "decision_model", "decision_input_tokens", "decision_cached_tokens",
    "decision_output_tokens", "decision_reasoning_tokens", "decision_cost_usd",
    "summary_model", "summary_input_tokens", "summary_output_tokens", "summary_cost_usd",
]


def write_cost_log(path: str, mode: str,
                   decision: Optional[Usage], summary: Optional[Usage]) -> dict[str, Any]:
    """Append one row per run to cost_log.csv and return the same data as a dict."""
    d = decision or Usage("decision", "")
    s = summary or Usage("summary", "")
    total = d.cost_usd + s.cost_usd
    row = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "mode": mode,
        "total_cost_usd": round(total, 6),
        "decision_model": d.model,
        "decision_input_tokens": d.input_tokens,
        "decision_cached_tokens": d.cached_input_tokens,
        "decision_output_tokens": d.output_tokens,
        "decision_reasoning_tokens": d.reasoning_tokens,
        "decision_cost_usd": round(d.cost_usd, 6),
        "summary_model": s.model,
        "summary_input_tokens": s.input_tokens,
        "summary_output_tokens": s.output_tokens,
        "summary_cost_usd": round(s.cost_usd, 6),
    }
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        new = not os.path.exists(path)
        with open(path, "a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=_COST_LOG_FIELDS)
            if new:
                w.writeheader()
            w.writerow(row)
    except Exception as e:  # noqa: BLE001
        log.warning("Failed to write cost log: %s", e)
    log.info("Run cost: $%.4f (decision $%.4f, summary $%.4f)",
             total, d.cost_usd, s.cost_usd)
    return row
