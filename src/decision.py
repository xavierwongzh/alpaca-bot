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

# Exit actions the AI may return per OPEN position. The code (src/exits.py)
# validates every one against the guardrails before it touches the broker.
VALID_EXIT_ACTIONS = {
    "HOLD", "TIGHTEN_STOP", "MOVE_TO_BREAKEVEN",
    "RAISE_TARGET", "TAKE_PARTIAL", "TAKE_FULL",
}

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
        },
        # One exit action per OPEN position (position management).
        "exit_actions": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "ticker": {"type": "string"},
                    "action": {
                        "type": "string",
                        "enum": ["HOLD", "TIGHTEN_STOP", "MOVE_TO_BREAKEVEN",
                                 "RAISE_TARGET", "TAKE_PARTIAL", "TAKE_FULL"],
                    },
                    # Absolute price levels (null when the action doesn't set one).
                    "new_stop": {"type": ["number", "null"]},
                    "new_target": {"type": ["number", "null"]},
                    "confidence": {"type": "number"},
                    "rationale": {"type": "string"},
                },
                "required": [
                    "ticker", "action", "new_stop", "new_target",
                    "confidence", "rationale",
                ],
            },
        },
    },
    "required": ["decisions", "exit_actions"],
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


@dataclass
class ExitAction:
    """The AI's proposed management action for one open position (pre-validation)."""
    ticker: str
    action: str                       # one of VALID_EXIT_ACTIONS
    new_stop: Optional[float]
    new_target: Optional[float]
    confidence: float
    rationale: str

    def as_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


def _build_messages(
    portfolio: dict[str, Any],
    candidates: list[dict[str, Any]],
    flow_signals: list[dict[str, Any]],
    market_summary: str,
    risk: RiskConfig,
    mode: str = "open",
    positions_management: Optional[list[dict[str, Any]]] = None,
    regime: Optional[dict[str, Any]] = None,
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
        "later, so make it self-contained; no fluff.\n\n"
        "EXIT MANAGEMENT (exit_actions): return exactly ONE exit action for EACH "
        "open position in open_positions (match by ticker). You manage the levels; "
        "the broker keeps a live GTC stop+target resting between runs. Actions:\n"
        "- HOLD: keep the current stop/target.\n"
        "- TIGHTEN_STOP: raise the stop (set new_stop, ABSOLUTE price). A stop is a "
        "one-way ratchet — it may only move UP, never down/wider; the code rejects "
        "any loosening.\n"
        "- MOVE_TO_BREAKEVEN: set the stop to the entry price (only valid when in profit).\n"
        "- RAISE_TARGET: raise the take-profit (set new_target, ABSOLUTE price, above "
        "current price and above the existing target).\n"
        "- TAKE_PARTIAL: scale out part of the position now.\n"
        "- TAKE_FULL: close the position now. NOTE: fully exiting a position opened "
        "THIS SESSION requires high confidence; low-confidence same-day full exits "
        "are rejected by the code, so only propose one on a strong signal.\n"
        "- new_stop/new_target are ABSOLUTE dollar prices (not percentages); use null "
        "when the action doesn't set that level. Never leave a position unprotected — "
        "the code guarantees a hard max-loss stop regardless."
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
        "regime": regime or {},
        "current_portfolio": portfolio,
        # Per-open-position management context (entry, current, uP&L, holding
        # days, current live stop/target, fresh signal) -> drives exit_actions.
        "open_positions": positions_management or [],
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


def _validate_exit_actions(raw: dict[str, Any]) -> list[ExitAction]:
    """Coerce/validate the exit_actions array; drop malformed entries."""
    out: list[ExitAction] = []
    for e in raw.get("exit_actions", []):
        try:
            ticker = str(e["ticker"]).upper().strip()
            action = str(e["action"]).upper().strip()
            if not ticker or action not in VALID_EXIT_ACTIONS:
                continue

            def _num(v):
                if v is None:
                    return None
                try:
                    return float(v)
                except (TypeError, ValueError):
                    return None

            conf = float(e.get("confidence", 0.0))
            conf = max(0.0, min(conf, 1.0))
            out.append(ExitAction(
                ticker=ticker,
                action=action,
                new_stop=_num(e.get("new_stop")),
                new_target=_num(e.get("new_target")),
                confidence=conf,
                rationale=str(e.get("rationale", "")).strip(),
            ))
        except (KeyError, ValueError, TypeError) as ex:
            log.warning("Dropping malformed exit action %s: %s", e, ex)
    return out


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
    positions_management: Optional[list[dict[str, Any]]] = None,
    regime: Optional[dict[str, Any]] = None,
) -> tuple[list[Decision], list[ExitAction], Optional["Usage"]]:
    """
    Single batched decision call with one retry — returns BOTH new-entry decisions
    and per-position exit actions (position management folded into the same call,
    no extra round-trip).

    Returns (decisions, exit_actions, usage). `usage` is the priced token
    accounting for the successful call (or None on total failure / no cost config).

    Prompt-caching note: the static system prompt + JSON schema come FIRST and are
    identical across runs of a given mode, so OpenAI prompt caching can serve that
    prefix at the discounted cached rate; only the dynamic user payload (positions,
    candidates, flow) changes per run.
    """
    from openai import OpenAI
    from src.cost import extract_usage

    client = OpenAI(api_key=secrets.openai_api_key)
    messages = _build_messages(portfolio, candidates, flow_signals, market_summary,
                               risk, mode, positions_management, regime)
    params = _build_request_params(model_cfg, messages)

    attempts = model_cfg.max_retries + 1
    last_err: Optional[Exception] = None
    for attempt in range(1, attempts + 1):
        try:
            resp = client.chat.completions.create(**params)
            raw = json.loads(resp.choices[0].message.content)
            decisions = _validate(raw, risk)
            exit_actions = _validate_exit_actions(raw)
            log.info("Decision call ok (attempt %d): %d decisions, %d exit actions",
                     attempt, len(decisions), len(exit_actions))
            usage = extract_usage(resp, "decision", model_cfg.decision_model, cost_cfg) if cost_cfg else None
            return decisions, exit_actions, usage
        except Exception as e:  # noqa: BLE001
            last_err = e
            log.warning("Decision call attempt %d failed: %s", attempt, e)

    log.error("Decision engine failed after %d attempts: %s", attempts, last_err)
    return [], [], None
