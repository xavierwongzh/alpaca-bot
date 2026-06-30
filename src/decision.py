"""
Layer 4 (part 1): the decision engine.

Sends the full context (positions, candidates with Layer 1/2 data, flow signals,
risk params) to OpenAI and gets back an array of decision objects via STRUCTURED
OUTPUTS (response_format with a strict JSON schema). The model proposes; the
code (risk.py / execution.py) decides and sizes.

One batched decision call per run, retried once on failure.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Optional
from uuid import uuid4

from config import CostConfig, ModelConfig, RiskConfig, Secrets
from src.logger import get_logger

log = get_logger()

VALID_ACTIONS = {"buy", "sell", "hold"}

# JSON schema enforced by OpenAI structured outputs.
DECISION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "decisions": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "action": {"type": "string", "enum": ["buy", "sell", "hold"]},
                    "ticker": {"type": "string"},
                    "side": {"type": "string", "enum": ["long"]},
                    "proposed_weight": {"type": "number"},
                    "stop_pct": {"type": "number"},
                    "target_pct": {"type": "number"},
                    "confidence": {"type": "number"},
                    "rationale": {"type": "string"},
                },
                "required": [
                    "action", "ticker", "side", "proposed_weight",
                    "stop_pct", "target_pct", "confidence", "rationale",
                ],
            },
        }
    },
    "required": ["decisions"],
}


@dataclass
class Decision:
    action: str
    ticker: str
    side: str
    proposed_weight: float
    stop_pct: float
    target_pct: float
    confidence: float
    rationale: str
    # Stable per-decision id; used as the Alpaca client_order_id so the dashboard
    # can join an order back to its stored decision record. (Kept last with a
    # default so positional construction in tests/other call sites still works.)
    id: str = field(default_factory=lambda: uuid4().hex)

    def as_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


def _build_messages(
    portfolio: dict[str, Any],
    candidates: list[dict[str, Any]],
    flow_signals: list[dict[str, Any]],
    market_summary: str,
    risk: RiskConfig,
    mode: str = "open",
) -> list[dict[str, str]]:
    system = (
        "You are a disciplined swing-trading portfolio manager for a $10k PAPER "
        "account. You blend two signal sources: (1) a catalyst screen of "
        "beaten-down but sound names with forward catalysts, diversified across "
        "sectors; (2) options-flow signals where large ask-side sweeps with high "
        "volume-to-open-interest are bullish triggers on the underlying equity.\n\n"
        "Return an array of decision objects. Rules:\n"
        "- Only LONG positions on liquid US equities/ETFs.\n"
        f"- proposed_weight is a fraction of equity, max {risk.max_position_pct:.2f} "
        "per name; the code enforces final sizing.\n"
        f"- Respect a max of {risk.max_concurrent_positions} concurrent positions.\n"
        f"- Default stop_pct {risk.stop_loss_pct} and target_pct {risk.profit_target_pct}; "
        "you may tighten but keep stop negative and target positive.\n"
        "- confidence in [0,1]. Prefer fewer, higher-conviction ideas.\n"
        "- Use 'sell' to exit/trim an existing position, 'hold' to keep it, "
        "'buy' to open/add. Do not invent tickers outside the provided data.\n"
        "- rationale: a short paragraph (2-4 sentences) explaining WHY this "
        "specific entry now — reference the flow signal and/or technicals, the "
        "catalyst, and the risk/reward. This is the explanation a human will read "
        "later, so make it self-contained; no fluff."
    )
    if mode == "midday":
        system += (
            "\n\nRUN MODE: MIDDAY (conservative second pass). The morning run already "
            "did the full entry scan and management. This midday pass exists ONLY to "
            "catch a genuinely standout NEW afternoon options sweep. Therefore:\n"
            "- Do NOT re-open, reverse, or second-guess this morning's positions; the "
            "broker bracket stop/target already manage their exits.\n"
            "- Do NOT sell a position just opened today on intraday noise.\n"
            "- Propose a 'buy' only for an exceptional, high-conviction new flow signal; "
            "otherwise prefer 'hold'. Fewer, stronger ideas only.\n"
            "(Note: the code independently enforces these limits, so weak ideas will be "
            "filtered out regardless.)"
        )
    user_payload = {
        "run_mode": mode,
        "risk_params": {
            "max_position_pct": risk.max_position_pct,
            "max_concurrent_positions": risk.max_concurrent_positions,
            "default_stop_pct": risk.stop_loss_pct,
            "default_target_pct": risk.profit_target_pct,
        },
        "market_summary": market_summary,
        "current_portfolio": portfolio,
        "candidates": candidates,
        "flow_signals": flow_signals,
    }
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(user_payload, default=str)},
    ]


def _validate(raw: dict[str, Any], risk: RiskConfig) -> list[Decision]:
    """Guard against missing fields / out-of-range values even with structured outputs."""
    decisions: list[Decision] = []
    for d in raw.get("decisions", []):
        try:
            action = str(d["action"]).lower().strip()
            ticker = str(d["ticker"]).upper().strip()
            if action not in VALID_ACTIONS or not ticker:
                continue
            weight = float(d.get("proposed_weight", 0.0))
            # Clamp weight into [0, max_position_pct]; code re-sizes anyway.
            weight = max(0.0, min(weight, risk.max_position_pct))
            stop_pct = float(d.get("stop_pct", risk.stop_loss_pct))
            target_pct = float(d.get("target_pct", risk.profit_target_pct))
            # Enforce sane signs/bounds.
            if stop_pct >= 0:
                stop_pct = risk.stop_loss_pct
            if target_pct <= 0:
                target_pct = risk.profit_target_pct
            conf = float(d.get("confidence", 0.0))
            conf = max(0.0, min(conf, 1.0))
            decisions.append(
                Decision(
                    action=action,
                    ticker=ticker,
                    side="long",
                    proposed_weight=weight,
                    stop_pct=stop_pct,
                    target_pct=target_pct,
                    confidence=conf,
                    rationale=str(d.get("rationale", "")).strip(),
                )
            )
        except (KeyError, ValueError, TypeError) as e:
            log.warning("Dropping malformed decision %s: %s", d, e)
    return decisions


def _is_reasoning_model(model: str) -> bool:
    """GPT-5 / o-series are reasoning models with different param support."""
    m = model.lower()
    return m.startswith("gpt-5") or m.startswith("o1") or m.startswith("o3") or m.startswith("o4")


def _build_request_params(model_cfg: ModelConfig, messages: list[dict[str, str]]) -> dict[str, Any]:
    """
    Assemble create() kwargs, adjusting for reasoning models:
      - reasoning models (gpt-5/o-series) reject a custom `temperature` -> omit it,
        and instead pass `reasoning_effort`.
      - non-reasoning models (e.g. gpt-4o) keep `temperature`.
    Structured outputs (json_schema) are used in both cases.
    """
    params: dict[str, Any] = {
        "model": model_cfg.decision_model,
        "messages": messages,
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "trade_decisions",
                "strict": True,
                "schema": DECISION_SCHEMA,
            },
        },
    }
    if _is_reasoning_model(model_cfg.decision_model):
        if model_cfg.reasoning_effort:
            params["reasoning_effort"] = model_cfg.reasoning_effort
    else:
        params["temperature"] = model_cfg.temperature
    return params


def get_decisions(
    secrets: Secrets,
    model_cfg: ModelConfig,
    risk: RiskConfig,
    portfolio: dict[str, Any],
    candidates: list[dict[str, Any]],
    flow_signals: list[dict[str, Any]],
    market_summary: str,
    mode: str = "open",
    cost_cfg: Optional[CostConfig] = None,
) -> tuple[list[Decision], Optional["Usage"]]:
    """
    Single batched decision call with one retry.

    Returns (decisions, usage). `usage` is the priced token accounting for the
    successful call (or None on total failure / no cost config).

    Prompt-caching note: the static system prompt + JSON schema come FIRST and are
    identical across runs of a given mode, so OpenAI prompt caching can serve that
    prefix at the discounted cached rate; only the dynamic user payload (positions,
    candidates, flow) changes per run.
    """
    from openai import OpenAI
    from src.cost import extract_usage

    client = OpenAI(api_key=secrets.openai_api_key)
    messages = _build_messages(portfolio, candidates, flow_signals, market_summary, risk, mode)
    params = _build_request_params(model_cfg, messages)

    attempts = model_cfg.max_retries + 1
    last_err: Optional[Exception] = None
    for attempt in range(1, attempts + 1):
        try:
            resp = client.chat.completions.create(**params)
            raw = json.loads(resp.choices[0].message.content)
            decisions = _validate(raw, risk)
            log.info("Decision call ok (attempt %d): %d decisions", attempt, len(decisions))
            usage = extract_usage(resp, "decision", model_cfg.decision_model, cost_cfg) if cost_cfg else None
            return decisions, usage
        except Exception as e:  # noqa: BLE001
            last_err = e
            log.warning("Decision call attempt %d failed: %s", attempt, e)

    log.error("Decision engine failed after %d attempts: %s", attempts, last_err)
    return [], None
