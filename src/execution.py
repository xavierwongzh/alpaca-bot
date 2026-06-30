"""
Execution: bracket-order placement on the Alpaca PAPER account.

Buys are submitted as BRACKET orders (entry + stop-loss + take-profit) so risk
is enforced at the broker. Sells are plain market orders to close.

Every order placed / filled / rejected is logged to the trade log.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from alpaca.trading.enums import OrderSide, OrderClass, TimeInForce
from alpaca.trading.requests import (
    MarketOrderRequest,
    StopLossRequest,
    TakeProfitRequest,
)

from config import Paths
from src.broker import Broker
from src.logger import get_logger, log_trade_event
from src.risk import SizedOrder

log = get_logger()


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


def place_orders(
    broker: Broker,
    sized_orders: list[SizedOrder],
    paths: Paths,
    dry_run: bool = False,
    mode: str = "open",
) -> list[ExecutionResult]:
    results: list[ExecutionResult] = []
    for order in sized_orders:
        if dry_run:
            log.info("[yellow]DRY-RUN[/yellow] (%s) would place %s %s x%s",
                     mode, order.action, order.ticker, order.qty)
            log_trade_event(
                paths.trade_log_csv, mode=mode, event="skipped", ticker=order.ticker,
                side=order.action, qty=order.qty, entry_price=order.entry_price,
                stop_price=order.stop_price, target_price=order.target_price,
                notional=order.notional, detail="dry_run",
            )
            results.append(ExecutionResult(order.ticker, order.action, order.qty,
                                           "skipped", None, "dry_run",
                                           client_order_id=order.decision_id,
                                           decision_id=order.decision_id))
            continue

        # Tag the order with the decision id so it can be joined back to its
        # stored decision record (and so it's visible in Alpaca). Empty -> omit.
        coid = order.decision_id or None

        try:
            if order.action == "buy":
                req = MarketOrderRequest(
                    symbol=order.ticker,
                    qty=order.qty,
                    side=OrderSide.BUY,
                    time_in_force=TimeInForce.DAY,
                    order_class=OrderClass.BRACKET,
                    take_profit=TakeProfitRequest(limit_price=order.target_price),
                    stop_loss=StopLossRequest(stop_price=order.stop_price),
                    client_order_id=coid,
                )
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
            log.info("[green]Placed[/green] (%s) %s %s x%s (order %s)",
                     mode, order.action, order.ticker, order.qty, oid)
            log_trade_event(
                paths.trade_log_csv, mode=mode, event="placed", ticker=order.ticker,
                side=order.action, qty=order.qty, entry_price=order.entry_price,
                stop_price=order.stop_price, target_price=order.target_price,
                notional=order.notional, order_id=oid, detail=order.rationale,
            )
            results.append(ExecutionResult(order.ticker, order.action, order.qty,
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
