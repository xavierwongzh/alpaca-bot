"""
AI-managed exits with code-enforced guardrails.

Each run the AI proposes ONE exit action per open position; this module validates
every proposal against hard rules the AI cannot override, then makes the broker
enforce the result — a fresh GTC OCO at the new levels, or a sell. The AI manages
the levels; the broker enforces them between runs, so a position is never left
unprotected.

`resolve_exit()` is a PURE function (offline-tested): given a position's numbers,
its current live levels, its age, and config, it returns a validated
ExitResolution — the action actually taken, the levels actually set, any sell
quantity, and a note when the AI's proposal was downgraded or clamped.

Guardrails enforced here (never in the model):
  * Stop is a one-way ratchet — it may only move up, never down/wider.
  * A hard maximum-loss stop always exists at entry*(1+hard_max_loss_pct); the
    AI can neither remove it nor widen beyond it.
  * Long sanity: stop below current price, target above (and above the old
    target when raising). Invalid proposals are rejected, keeping prior levels.
  * A TAKE_FULL of a position opened THIS session needs high confidence.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from config import ExitConfig, RiskConfig
from src.decision import ExitAction
from src.execution import (
    _poll_fill, attach_protection_oco, cancel_protection, market_sell,
)
from src.logger import get_logger

log = get_logger()

_EPS = 1e-6


@dataclass
class PositionSnapshot:
    ticker: str
    qty: int
    entry: float                       # average entry price
    current_price: float
    current_stop: Optional[float]      # live resting stop, or None if missing
    current_target: Optional[float]    # live resting target, or None if missing
    age_days: Optional[int]            # 0 = opened today; None = older than lookback


@dataclass
class ExitResolution:
    ticker: str
    requested_action: str
    action: str                        # resolved action (may downgrade to HOLD)
    old_stop: Optional[float]
    old_target: Optional[float]
    new_stop: float
    new_target: float
    sell_qty: int                      # >0 only for TAKE_PARTIAL / TAKE_FULL
    remaining_qty: int                 # shares still held after a partial
    confidence: float
    rationale: str
    note: str                          # why the proposal was adjusted/rejected
    changed: bool                      # broker action needed this run?


def _ratchet_stop(proposed: Optional[float], baseline: float,
                  current_price: float, hard_floor: float) -> tuple[float, str]:
    """A stop may only move UP, stay below price, and never below the hard floor."""
    if proposed is None:
        return baseline, "no stop level provided; kept current"
    if proposed < baseline - _EPS:
        return baseline, f"rejected stop loosen ({proposed:.2f} < current {baseline:.2f})"
    if proposed >= current_price - _EPS:
        return baseline, f"rejected stop >= price ({proposed:.2f} >= {current_price:.2f})"
    return max(proposed, hard_floor), ""


def _raise_target(proposed: Optional[float], baseline: float,
                  current_price: float, min_gain: float) -> tuple[float, str]:
    """A raised target must be higher than the old one and clear of the price."""
    if proposed is None:
        return baseline, "no target level provided; kept current"
    if proposed <= baseline + _EPS:
        return baseline, f"rejected target not higher ({proposed:.2f} <= {baseline:.2f})"
    floor = current_price * (1 + min_gain)
    if proposed < floor - _EPS:
        return baseline, f"rejected target too close to price ({proposed:.2f} < {floor:.2f})"
    return proposed, ""


def resolve_exit(
    pos: PositionSnapshot,
    action: str,
    new_stop: Optional[float],
    new_target: Optional[float],
    confidence: float,
    rationale: str,
    risk: RiskConfig,
    exits: ExitConfig,
) -> ExitResolution:
    """Validate one AI exit proposal into a concrete, guardrail-safe resolution."""
    hard_floor = round(pos.entry * (1 + exits.hard_max_loss_pct), 2)
    # Baseline = the levels currently live at the broker; if a leg is missing we
    # fall back to the config defaults off entry (so protection is (re)established).
    base_stop = pos.current_stop if pos.current_stop is not None else hard_floor
    base_target = (pos.current_target if pos.current_target is not None
                   else round(pos.entry * (1 + risk.profit_target_pct), 2))

    resolved = action
    stop, target = base_stop, base_target
    sell_qty = 0
    note = ""

    if action == "HOLD":
        pass
    elif action == "TIGHTEN_STOP":
        stop, note = _ratchet_stop(new_stop, base_stop, pos.current_price, hard_floor)
    elif action == "MOVE_TO_BREAKEVEN":
        stop, note = _ratchet_stop(pos.entry, base_stop, pos.current_price, hard_floor)
        if note:  # underwater -> entry is at/above price -> can't move to BE yet
            resolved = "HOLD"
            note = "MOVE_TO_BREAKEVEN not possible (not in profit); kept current stop"
    elif action == "RAISE_TARGET":
        target, note = _raise_target(new_target, base_target, pos.current_price,
                                     exits.min_target_gain_pct)
    elif action == "TAKE_PARTIAL":
        if pos.qty <= 1:
            resolved, note = "HOLD", "position too small to partial; held"
        else:
            sell_qty = max(1, int(pos.qty * exits.partial_exit_fraction))
            sell_qty = min(sell_qty, pos.qty - 1)   # always leave something to protect
            # remainder keeps the current levels (optionally a tightened stop)
            stop, _ = _ratchet_stop(new_stop, base_stop, pos.current_price, hard_floor)
    elif action == "TAKE_FULL":
        same_day = pos.age_days == 0
        if same_day and confidence < exits.same_day_full_exit_min_confidence:
            resolved = "HOLD"
            note = (f"same-day TAKE_FULL blocked: confidence {confidence:.2f} < "
                    f"{exits.same_day_full_exit_min_confidence:.2f}; kept protection")
        else:
            sell_qty = pos.qty

    stop = round(stop, 2)
    target = round(target, 2)
    remaining = pos.qty - sell_qty

    # Does the broker need to do anything? Yes if we're selling, or the levels
    # changed, or protection was missing entirely (must be (re)established).
    levels_changed = (
        pos.current_stop is None or pos.current_target is None
        or abs(stop - (pos.current_stop or 0)) > 0.005
        or abs(target - (pos.current_target or 0)) > 0.005
    )
    changed = sell_qty > 0 or levels_changed

    return ExitResolution(
        ticker=pos.ticker, requested_action=action, action=resolved,
        old_stop=pos.current_stop, old_target=pos.current_target,
        new_stop=stop, new_target=target, sell_qty=sell_qty,
        remaining_qty=remaining, confidence=confidence, rationale=rationale,
        note=note, changed=changed,
    )


def resolve_all(
    positions: list[PositionSnapshot],
    exit_actions: list[ExitAction],
    risk: RiskConfig,
    exits: ExitConfig,
) -> list[ExitResolution]:
    """
    Resolve an exit for EVERY open position. A position the AI didn't return an
    action for defaults to HOLD, so it still gets its protection kept/enforced.
    """
    by_ticker = {a.ticker.upper(): a for a in exit_actions}
    out: list[ExitResolution] = []
    for pos in positions:
        a = by_ticker.get(pos.ticker.upper())
        if a is None:
            out.append(resolve_exit(pos, "HOLD", None, None, 0.0,
                                    "no AI action returned; protection kept", risk, exits))
        else:
            out.append(resolve_exit(pos, a.action, a.new_stop, a.new_target,
                                    a.confidence, a.rationale, risk, exits))
    return out


def apply_exits(
    broker: Any,
    resolutions: list[ExitResolution],
    *,
    mode: str,
    regime: dict[str, Any],
    dry_run: bool,
) -> list[dict[str, Any]]:
    """
    Enforce each resolution at the broker: re-attach a fresh GTC OCO at the new
    levels, or sell (partial/full and re-protect the remainder). Returns one log
    record per position. In dry-run nothing is submitted.
    """
    ts = datetime.now(timezone.utc).isoformat()
    records: list[dict[str, Any]] = []

    for r in resolutions:
        applied = False
        order_id = ""
        error = ""

        if not dry_run and r.changed:
            try:
                if r.sell_qty > 0:
                    # Cancel protection first so the shares are free to sell.
                    cancel_protection(broker, r.ticker)
                    ok, oid, err = market_sell(broker, r.ticker, r.sell_qty)
                    order_id, error = oid, err
                    applied = ok
                    if ok and r.action == "TAKE_PARTIAL" and r.remaining_qty >= 1:
                        _poll_fill(broker, oid)
                        pok, _pid, perr = attach_protection_oco(
                            broker, r.ticker, r.remaining_qty, r.new_target, r.new_stop)
                        if not pok:
                            error = (error + "; " if error else "") + f"re-protect failed: {perr}"
                else:
                    # Level change only: replace the OCO at the new levels.
                    cancel_protection(broker, r.ticker)
                    ok, oid, err = attach_protection_oco(
                        broker, r.ticker, r.remaining_qty, r.new_target, r.new_stop)
                    order_id, error, applied = oid, err, ok
            except Exception as e:  # noqa: BLE001
                error = str(e)
                log.warning("Exit apply failed for %s: %s", r.ticker, e)

        level_txt = f"stop {r.old_stop}->{r.new_stop}, target {r.old_target}->{r.new_target}"
        if r.sell_qty > 0:
            log.info("[cyan]EXIT[/cyan] %s %s x%d (%s) conf %.2f%s",
                     r.action, r.ticker, r.sell_qty, "sold" if applied else "planned",
                     r.confidence, f" — {r.note}" if r.note else "")
        else:
            log.info("[cyan]EXIT[/cyan] %s %s (%s) conf %.2f%s",
                     r.action, r.ticker, level_txt, r.confidence,
                     f" — {r.note}" if r.note else "")

        records.append({
            "timestamp": ts,
            "mode": mode,
            "ticker": r.ticker,
            "requested_action": r.requested_action,
            "action": r.action,
            "old_stop": r.old_stop,
            "old_target": r.old_target,
            "new_stop": r.new_stop,
            "new_target": r.new_target,
            "sell_qty": r.sell_qty,
            "remaining_qty": r.remaining_qty,
            "confidence": r.confidence,
            "rationale": r.rationale,
            "note": r.note,
            "regime": regime,
            "changed": r.changed,
            "applied": applied,
            "order_id": order_id,
            "error": error,
        })
    return records


def append_exit_records(path: str, records: list[dict[str, Any]]) -> None:
    """Append exit-action records to the JSONL log (one object per line)."""
    if not records:
        return
    import json
    import os
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            for r in records:
                f.write(json.dumps(r, default=str) + "\n")
        log.info("Wrote %d exit record(s) to %s", len(records), os.path.basename(path))
    except Exception as e:  # noqa: BLE001
        log.warning("Failed to write exit records: %s", e)
