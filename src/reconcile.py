"""
Closed-trade reconciliation.

Reconstructs realized round-trips from Alpaca FILL activities using FIFO lot
matching, infers an exit reason, joins each trade back to its decision record by
order id / client_order_id, and appends to data/closed_trades.jsonl.

Idempotent: every closed trade has a deterministic `trade_key`; trades already
present in the file are skipped, so re-runs never double-record.
"""
from __future__ import annotations

import json
import os
from collections import deque
from datetime import date, datetime
from typing import Any, Optional

import numpy as np

from src.broker import Broker
from src.logger import get_logger
from src.sectors import get_sector

log = get_logger()


def _f(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _as_date(dt: Any) -> Optional[date]:
    if isinstance(dt, datetime):
        return dt.date()
    if isinstance(dt, str):
        try:
            return datetime.fromisoformat(dt.replace("Z", "+00:00")).date()
        except ValueError:
            return None
    return None


def _exit_reason(order_type: str) -> str:
    t = (order_type or "").lower()
    if "limit" in t and "stop" not in t:
        return "target"      # bracket take-profit
    if "stop" in t:
        return "stop"        # bracket stop-loss
    if "market" in t:
        return "manual"      # discretionary close
    return "manual"


def _load_decision_maps(decisions_jsonl: str) -> tuple[dict[str, dict], dict[str, dict]]:
    """Return (by_alpaca_order_id, by_client_order_id) from decisions.jsonl."""
    by_order: dict[str, dict] = {}
    by_coid: dict[str, dict] = {}
    if not os.path.exists(decisions_jsonl):
        return by_order, by_coid
    with open(decisions_jsonl, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if r.get("alpaca_order_id"):
                by_order[str(r["alpaca_order_id"])] = r
            if r.get("client_order_id"):
                by_coid[str(r["client_order_id"])] = r
    return by_order, by_coid


def latest_entry_by_symbol(decisions_jsonl: str) -> dict[str, dict]:
    """
    Most recent ENTRY (buy) decision record per symbol, ANY run mode. Used by
    protection reconciliation (for stored stop/target) and the dashboard join.
    """
    out: dict[str, dict] = {}
    if not os.path.exists(decisions_jsonl):
        return out
    with open(decisions_jsonl, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if str(r.get("action", "")).lower() != "buy":
                continue
            sym = str(r.get("ticker", "")).upper()
            if not sym:
                continue
            prev = out.get(sym)
            if not prev or str(r.get("timestamp", "")) > str(prev.get("timestamp", "")):
                out[sym] = r
    return out


def _parse_dt(v: Any) -> Optional[datetime]:
    if isinstance(v, datetime):
        return v
    if isinstance(v, str):
        try:
            return datetime.fromisoformat(v.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _load_exit_history(path: str) -> dict[str, list[dict]]:
    """symbol -> list of exit-action records (from data/decisions/exits.jsonl)."""
    hist: dict[str, list[dict]] = {}
    if not os.path.exists(path):
        return hist
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            sym = str(r.get("ticker", "")).upper()
            if sym:
                hist.setdefault(sym, []).append(r)
    return hist


def _exit_actions_in_window(
    hist: dict[str, list[dict]], symbol: str, entry_time: Any, exit_time: Any,
) -> list[dict]:
    """Compact exit-action history for a symbol between its entry and exit times."""
    ed, xd = _parse_dt(entry_time), _parse_dt(exit_time)
    out: list[dict] = []
    for r in hist.get(symbol, []):
        adt = _parse_dt(r.get("timestamp"))
        if adt is None or (ed and adt < ed) or (xd and adt > xd):
            continue
        out.append({
            "timestamp": r.get("timestamp"),
            "mode": r.get("mode"),
            "action": r.get("action"),
            "new_stop": r.get("new_stop"),
            "new_target": r.get("new_target"),
            "confidence": r.get("confidence"),
            "applied": r.get("applied"),
        })
    return out


def _existing_keys(path: str) -> set[str]:
    keys: set[str] = set()
    if not os.path.exists(path):
        return keys
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                keys.add(json.loads(line)["trade_key"])
            except (json.JSONDecodeError, KeyError):
                continue
    return keys


def reconcile_closed_trades(broker: Broker, paths: Any) -> list[dict[str, Any]]:
    """
    Build closed-trade records from fills, skip ones already recorded, append the
    new ones to closed_trades.jsonl, and return the new records.
    """
    fills = broker.get_fills()
    if not fills:
        return []

    by_order, by_coid = _load_decision_maps(paths.decisions_jsonl)
    exit_history = _load_exit_history(getattr(paths, "exits_jsonl", ""))
    existing = _existing_keys(paths.closed_trades_jsonl)

    open_lots: dict[str, deque] = {}
    new_records: list[dict[str, Any]] = []

    for a in fills:
        symbol = a.get("symbol")
        if not symbol:
            continue
        side = a.get("side", "")
        qty = _f(a.get("qty", 0))
        price = _f(a.get("price", 0))
        when = a.get("time")
        order_id = str(a.get("order_id", "") or "")
        order_type = a.get("order_type", "")
        client_order_id = str(a.get("client_order_id", "") or "")
        if qty <= 0 or price <= 0:
            continue

        if side == "buy":
            open_lots.setdefault(symbol, deque()).append(
                {"qty": qty, "price": price, "time": when, "order_id": order_id,
                 "client_order_id": client_order_id}
            )
            continue

        if side != "sell":
            continue

        # Sell: match FIFO against open buy lots.
        remaining = qty
        lots = open_lots.setdefault(symbol, deque())
        while remaining > 1e-9 and lots:
            lot = lots[0]
            matched = min(lot["qty"], remaining)
            trade_key = f"{order_id}:{lot['order_id']}:{matched:.4f}"

            if trade_key not in existing:
                reason = _exit_reason(order_type)

                entry_price = lot["price"]
                exit_price = price
                ret_pct = (exit_price - entry_price) / entry_price if entry_price else 0.0
                pnl = (exit_price - entry_price) * matched

                # Join to the decision behind the ENTRY (by client_order_id, then order id).
                rec = by_coid.get(lot.get("client_order_id", "")) or by_order.get(lot["order_id"])
                ed, xd = _as_date(lot["time"]), _as_date(when)
                holding_days = int(np.busday_count(ed, xd)) if ed and xd and xd > ed else 0

                client_oid = lot.get("client_order_id", "") or (rec or {}).get("client_order_id", "")
                signal_type = (
                    "flow" if (rec and rec.get("flow_signal")) else
                    ("catalyst" if rec else "unknown")
                )
                record = {
                    "trade_key": trade_key,
                    "ticker": symbol,
                    "entry_price": round(entry_price, 4),
                    "exit_price": round(exit_price, 4),
                    "qty": matched,
                    "realized_return_pct": round(ret_pct, 6),
                    "realized_pnl": round(pnl, 2),
                    "entry_time": lot["time"].isoformat() if lot["time"] else None,
                    "exit_time": when.isoformat() if when else None,
                    "holding_days": holding_days,
                    "exit_reason": reason,
                    # AI exit-action history over this trade's holding window, so we
                    # can compare AI-managed exits vs a plain mechanical trailing stop.
                    "exit_actions": _exit_actions_in_window(
                        exit_history, symbol, lot["time"], when),
                    # tags carried from the decision record
                    "signal_type": signal_type,
                    "run_mode": (rec or {}).get("mode", "unknown"),
                    "confidence": (rec or {}).get("confidence"),
                    "sector": get_sector(symbol),
                    "entry_order_id": lot["order_id"],
                    "exit_order_id": order_id,
                    "client_order_id": client_oid,
                }
                new_records.append(record)
                existing.add(trade_key)

            lot["qty"] -= matched
            remaining -= matched
            if lot["qty"] <= 1e-9:
                lots.popleft()

    if new_records:
        with open(paths.closed_trades_jsonl, "a", encoding="utf-8") as f:
            for r in new_records:
                f.write(json.dumps(r, default=str) + "\n")
        log.info("Reconciled %d newly-closed trade(s)", len(new_records))
    return new_records
