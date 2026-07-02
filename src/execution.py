"""
Execution on the Alpaca PAPER account.

A buy is a market ENTRY, then — once filled — a GTC OCO exit (take-profit limit +
stop-loss stop, one-cancels-other) is attached so protection PERSISTS until hit
or explicitly canceled (a bracket's DAY legs would expire at the close and leave
the position unprotected). Sells are plain market orders to close.

Every order placed / filled / rejected is logged to the trade log.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Optional

from alpaca.trading.enums import OrderSide, OrderClass, TimeInForce
from alpaca.trading.requests import (
    LimitOrderRequest,
    MarketOrderRequest,
    StopLossRequest,
    TakeProfitRequest,
)

from config import Paths
from src.broker import Broker
from src.logger import get_logger, log_trade_event
from src.risk import SizedOrder

log = get_logger()


def _poll_fill(broker: Broker, order_id: str, timeout_s: float = 15.0,
               interval_s: float = 1.5) -> float:
    """Poll an order until it fills (or terminates); return filled qty."""
    deadline = time.time() + timeout_s
    filled = 0.0
    while time.time() < deadline:
        try:
            o = broker.client.get_order_by_id(order_id)
        except Exception:  # noqa: BLE001
            time.sleep(interval_s)
            continue
        status = str(getattr(o, "status", "")).split(".")[-1].lower()
        filled = float(getattr(o, "filled_qty", 0) or 0)
        if status == "filled" and filled > 0:
            return filled
        if status in ("canceled", "rejected", "expired"):
            return filled
        time.sleep(interval_s)
    return filled


def attach_protection_oco(
    broker: Broker, symbol: str, qty: float,
    target_price: float, stop_price: float,
) -> tuple[bool, str, str]:
    """
    Attach a GTC OCO to an open long: a take-profit LIMIT and a stop-loss STOP as
    a one-cancels-other pair, so both survive past the market close and one
    cancels the other when hit. Returns (ok, order_id, error).
    """
    q = int(qty)
    if q < 1:
        return False, "", "qty < 1"
    try:
        req = LimitOrderRequest(
            symbol=symbol,
            qty=q,
            side=OrderSide.SELL,
            time_in_force=TimeInForce.GTC,
            order_class=OrderClass.OCO,
            limit_price=round(target_price, 2),
            take_profit=TakeProfitRequest(limit_price=round(target_price, 2)),
            stop_loss=StopLossRequest(stop_price=round(stop_price, 2)),
        )
        o = broker.client.submit_order(req)
        return True, str(getattr(o, "id", "")), ""
    except Exception as e:  # noqa: BLE001
        return False, "", str(e)


@dataclass
class ExecutionResult:
    ticker: str
    action: str
    qty: float
    status: str             # "placed" | "rejected" | "skipped" | "error"
    order_id: Optional[str]
    detail: str
    client_order_id: str = ""   # == decision id; join key for the dashboard
    decision_id: str = ""


def _open_sell_orders_by_symbol(broker: Broker) -> tuple[dict[str, list], list]:
    """
    Map symbol -> list of open SELL orders/legs (flattened, incl. OCO legs), plus
    the raw top-level orders (for cancellation). An OCO shows as a parent LIMIT
    with a child STOP leg, so we flatten both.
    """
    from alpaca.trading.requests import GetOrdersRequest
    from alpaca.trading.enums import QueryOrderStatus

    try:
        top = list(broker.client.get_orders(
            filter=GetOrdersRequest(status=QueryOrderStatus.OPEN, nested=True)))
    except Exception as e:  # noqa: BLE001
        log.warning("Could not fetch open orders for protection check: %s", e)
        return {}, []

    by_sym: dict[str, list] = {}
    for o in top:
        flat = [o] + list(getattr(o, "legs", None) or [])
        for leg in flat:
            if str(getattr(leg, "side", "")).split(".")[-1].lower() != "sell":
                continue
            sym = getattr(leg, "symbol", None)
            if sym:
                by_sym.setdefault(sym, []).append(leg)
    return by_sym, top


def live_protection_by_symbol(broker: Broker) -> dict[str, dict]:
    """
    Current live protective levels per symbol, read from the open OCO sell legs:
    {symbol: {"stop": float|None, "target": float|None}}. The stop leg carries
    stop_price; the take-profit leg carries limit_price (and no stop_price).
    """
    by_sym, _ = _open_sell_orders_by_symbol(broker)
    out: dict[str, dict] = {}
    for sym, legs in by_sym.items():
        stop = next((float(getattr(o, "stop_price")) for o in legs
                     if getattr(o, "stop_price", None)), None)
        target = next((float(getattr(o, "limit_price")) for o in legs
                       if getattr(o, "limit_price", None) and not getattr(o, "stop_price", None)),
                      None)
        out[sym] = {"stop": stop, "target": target}
    return out


def cancel_protection(broker: Broker, symbol: str) -> None:
    """Cancel all open SELL orders (OCO/bracket parents + legs) for one symbol."""
    _, top_orders = _open_sell_orders_by_symbol(broker)
    for o in top_orders:
        if getattr(o, "symbol", None) == symbol and \
           str(getattr(o, "side", "")).split(".")[-1].lower() == "sell":
            try:
                broker.client.cancel_order_by_id(str(o.id))
            except Exception as e:  # noqa: BLE001
                log.debug("Cancel protection failed for %s: %s", symbol, e)


def market_sell(broker: Broker, symbol: str, qty: float,
                coid: Optional[str] = None) -> tuple[bool, str, str]:
    """Submit a plain market SELL to reduce/close a long. Returns (ok, order_id, error)."""
    q = int(qty)
    if q < 1:
        return False, "", "qty < 1"
    try:
        req = MarketOrderRequest(
            symbol=symbol, qty=q, side=OrderSide.SELL,
            time_in_force=TimeInForce.DAY, client_order_id=coid or None,
        )
        o = broker.client.submit_order(req)
        return True, str(getattr(o, "id", "")), ""
    except Exception as e:  # noqa: BLE001
        return False, "", str(e)


def reconcile_protection(broker: Broker, cfg: Any, positions: list,
                         entries_by_symbol: dict[str, dict]) -> list[dict]:
    """
    INVARIANT: every open long must have a live GTC stop-loss AND take-profit.

    For each position missing either leg, cancel any stale sell legs and attach a
    fresh GTC OCO — using the stored decision's stop/target if available, else the
    config default stop/target pcts off the current average entry.
    """
    repaired: list[dict] = []
    by_sym, top_orders = _open_sell_orders_by_symbol(broker)

    for p in positions:
        sym = p.symbol
        legs = by_sym.get(sym, [])
        has_target = any(getattr(o, "limit_price", None) for o in legs)
        has_stop = any(getattr(o, "stop_price", None) for o in legs)
        if has_target and has_stop:
            continue  # already protected

        log.warning("[bold yellow]%s is missing protection[/bold yellow] "
                    "(target=%s, stop=%s) — re-attaching GTC OCO.", sym, has_target, has_stop)

        # Cancel any stale sell orders for this symbol (canceling an OCO/bracket
        # parent cancels its legs).
        for o in top_orders:
            if getattr(o, "symbol", None) == sym and \
               str(getattr(o, "side", "")).split(".")[-1].lower() == "sell":
                try:
                    broker.client.cancel_order_by_id(str(o.id))
                except Exception as e:  # noqa: BLE001
                    log.debug("Cancel stale leg failed for %s: %s", sym, e)

        # Prices: stored decision, else config defaults off avg entry.
        avg_entry = float(p.avg_entry_price)
        rec = entries_by_symbol.get(sym)
        if rec and rec.get("target_price") and rec.get("stop_price"):
            target = float(rec["target_price"])
            stop = float(rec["stop_price"])
        else:
            target = round(avg_entry * (1 + cfg.risk.profit_target_pct), 2)
            stop = round(avg_entry * (1 + cfg.risk.stop_loss_pct), 2)

        qty = int(float(p.qty))
        ok, oid, err = attach_protection_oco(broker, sym, qty, target, stop)
        if ok:
            log.info("[green]Re-attached protection[/green] %s GTC OCO "
                     "(target %.2f / stop %.2f)", sym, target, stop)
            repaired.append({"ticker": sym, "target": target, "stop": stop, "order_id": oid})
        else:
            log.warning("[red]Failed to re-attach protection[/red] to %s: %s", sym, err)
    return repaired


def place_orders(
    broker: Broker,
    sized_orders: list[SizedOrder],
    paths: Paths,
    dry_run: bool = False,
    mode: str = "open",
    skip_detail: str = "dry_run",
) -> list[ExecutionResult]:
    """
    Submit sized orders as bracket/market orders. When `dry_run` is True, nothing
    is submitted and each order is logged as skipped with `skip_detail` as the
    reason (e.g. "dry_run" or "market_closed").
    """
    results: list[ExecutionResult] = []
    for order in sized_orders:
        if dry_run:
            log.info("[yellow]SKIP[/yellow] (%s/%s) would place %s %s x%s",
                     mode, skip_detail, order.action, order.ticker, order.qty)
            log_trade_event(
                paths.trade_log_csv, mode=mode, event="skipped", ticker=order.ticker,
                side=order.action, qty=order.qty, entry_price=order.entry_price,
                stop_price=order.stop_price, target_price=order.target_price,
                notional=order.notional, detail=skip_detail,
            )
            results.append(ExecutionResult(order.ticker, order.action, order.qty,
                                           "skipped", None, skip_detail,
                                           client_order_id=order.decision_id,
                                           decision_id=order.decision_id))
            continue

        # Tag the order with the decision id so it can be joined back to its
        # stored decision record (and so it's visible in Alpaca). Empty -> omit.
        coid = order.decision_id or None

        try:
            if order.action == "buy":
                # 1) Market entry (DAY is fine for a market order that fills now).
                entry = MarketOrderRequest(
                    symbol=order.ticker,
                    qty=order.qty,
                    side=OrderSide.BUY,
                    time_in_force=TimeInForce.DAY,
                    client_order_id=coid,
                )
                submitted = broker.client.submit_order(entry)
                oid = str(submitted.id)
                log.info("[green]Placed entry[/green] (%s) buy %s x%s (order %s)",
                         mode, order.ticker, order.qty, oid)

                # 2) Wait for the fill, then 3) attach GTC OCO protection.
                filled = _poll_fill(broker, oid)
                if filled > 0:
                    ok, prot_id, err = attach_protection_oco(
                        broker, order.ticker, filled, order.target_price, order.stop_price)
                    if ok:
                        log.info("[green]Protection attached[/green] %s GTC OCO "
                                 "(target %.2f / stop %.2f, order %s)",
                                 order.ticker, order.target_price, order.stop_price, prot_id)
                    else:
                        log.warning("[bold red]Protection attach FAILED[/bold red] for %s: %s "
                                    "— reconciliation will retry next run.", order.ticker, err)
                else:
                    log.warning("[yellow]Entry not filled yet[/yellow] for %s; protection will "
                                "be attached on the next run's reconciliation.", order.ticker)

                log_trade_event(
                    paths.trade_log_csv, mode=mode, event="placed", ticker=order.ticker,
                    side="buy", qty=order.qty, entry_price=order.entry_price,
                    stop_price=order.stop_price, target_price=order.target_price,
                    notional=order.notional, order_id=oid, detail=order.rationale,
                )
                results.append(ExecutionResult(order.ticker, "buy", order.qty,
                                               "placed", oid, order.rationale,
                                               client_order_id=order.decision_id,
                                               decision_id=order.decision_id))
            else:  # sell -> close position at market
                req = MarketOrderRequest(
                    symbol=order.ticker,
                    qty=order.qty,
                    side=OrderSide.SELL,
                    time_in_force=TimeInForce.DAY,
                    client_order_id=coid,
                )
                submitted = broker.client.submit_order(req)
                oid = str(submitted.id)
                log.info("[green]Placed[/green] (%s) sell %s x%s (order %s)",
                         mode, order.ticker, order.qty, oid)
                log_trade_event(
                    paths.trade_log_csv, mode=mode, event="placed", ticker=order.ticker,
                    side="sell", qty=order.qty, entry_price=order.entry_price,
                    stop_price=order.stop_price, target_price=order.target_price,
                    notional=order.notional, order_id=oid, detail=order.rationale,
                )
                results.append(ExecutionResult(order.ticker, "sell", order.qty,
                                               "placed", oid, order.rationale,
                                               client_order_id=order.decision_id,
                                               decision_id=order.decision_id))
        except Exception as e:  # noqa: BLE001
            log.error("[red]Order rejected[/red] (%s) %s %s: %s", mode, order.action, order.ticker, e)
            log_trade_event(
                paths.trade_log_csv, mode=mode, event="rejected", ticker=order.ticker,
                side=order.action, qty=order.qty, entry_price=order.entry_price,
                notional=order.notional, detail=str(e),
            )
            results.append(ExecutionResult(order.ticker, order.action, order.qty,
                                           "error", None, str(e),
                                           client_order_id=order.decision_id,
                                           decision_id=order.decision_id))
    return results
