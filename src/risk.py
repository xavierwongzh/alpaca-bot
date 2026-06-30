"""
Risk: sizing and limit checks.

The model proposes a weight; THIS module is the authority on whether a trade
happens and how big it is. It converts a proposed weight into a share quantity
and validates against:
  - max position size (% of equity)
  - max concurrent positions
  - available buying power
  - daily-loss halt (kill switch)
  - minimum order notional
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from config import MiddayConfig, RiskConfig
from src.analytics import PortfolioView
from src.decision import Decision
from src.logger import get_logger

log = get_logger()


@dataclass
class SizedOrder:
    ticker: str
    action: str                 # "buy" | "sell"
    qty: float
    entry_price: float
    stop_price: float
    target_price: float
    notional: float
    rationale: str
    confidence: float
    decision_id: str = ""       # join key -> Alpaca client_order_id + decision record

    def as_dict(self) -> dict:
        return self.__dict__.copy()


@dataclass
class RejectedOrder:
    ticker: str
    action: str
    reason: str


def daily_loss_halt_triggered(account_day_pnl_pct: float, risk: RiskConfig) -> bool:
    """True once the portfolio's day P&L breaches the max daily loss threshold."""
    return account_day_pnl_pct <= risk.max_daily_loss_pct


def apply_midday_filter(
    decisions: list[Decision],
    *,
    position_age_days: dict[str, Optional[int]],
    flow_by_ticker: dict[str, dict],
    held_tickers: set[str],
    midday: MiddayConfig,
) -> tuple[list[Decision], list[RejectedOrder]]:
    """
    Conservative midday policy, ENFORCED IN CODE (not just the LLM prompt).

    Filters LLM proposals BEFORE sizing/execution so the midday pass cannot
    relitigate the morning's trades:
      - No same-day reversals: drop sells of positions younger than MIN_HOLD_DAYS.
        Sells of older positions are allowed only above SELL_MIN_CONFIDENCE.
      - Higher bar for entries: a buy must clear MIN_CONFIDENCE and its flow
        composite score must clear MIN_COMPOSITE.
      - Cap new entries: at most MAX_NEW_POSITIONS new names opened at midday
        (the existing 8-position / 15% / daily-loss rails still apply in sizing).

    `position_age_days[ticker]` is the age in days of the current position, or
    None if it predates our lookback (treated as old enough to sell).
    Returns (kept_decisions, dropped) where dropped are RejectedOrder records for
    transparency in the dashboard/logs.
    """
    kept: list[Decision] = []
    dropped: list[RejectedOrder] = []
    new_buys: list[Decision] = []

    for d in decisions:
        if d.action == "hold":
            kept.append(d)
            continue

        if d.action == "sell":
            age = position_age_days.get(d.ticker)
            if age is not None and age < midday.MIN_HOLD_DAYS:
                dropped.append(RejectedOrder(
                    d.ticker, "sell",
                    f"midday: position too young to sell (age {age}d < {midday.MIN_HOLD_DAYS}d)",
                ))
                continue
            if d.confidence < midday.SELL_MIN_CONFIDENCE:
                dropped.append(RejectedOrder(
                    d.ticker, "sell",
                    f"midday: sell confidence {d.confidence:.2f} < {midday.SELL_MIN_CONFIDENCE}",
                ))
                continue
            kept.append(d)
            continue

        # buy
        if d.confidence < midday.MIN_CONFIDENCE:
            dropped.append(RejectedOrder(
                d.ticker, "buy",
                f"midday: confidence {d.confidence:.2f} < {midday.MIN_CONFIDENCE}",
            ))
            continue
        composite = (flow_by_ticker.get(d.ticker) or {}).get("composite_score")
        if composite is None or composite < midday.MIN_COMPOSITE:
            dropped.append(RejectedOrder(
                d.ticker, "buy",
                f"midday: flow composite {composite if composite is not None else 'n/a'} "
                f"< {midday.MIN_COMPOSITE}",
            ))
            continue

        if d.ticker in held_tickers:
            kept.append(d)            # adding to an existing name is not a "new" position
        else:
            new_buys.append(d)

    # Cap NEW positions: keep the highest-confidence ones up to MAX_NEW_POSITIONS.
    new_buys.sort(key=lambda x: x.confidence, reverse=True)
    for i, d in enumerate(new_buys):
        if i < midday.MAX_NEW_POSITIONS:
            kept.append(d)
        else:
            dropped.append(RejectedOrder(
                d.ticker, "buy",
                f"midday: exceeds max new positions ({midday.MAX_NEW_POSITIONS})",
            ))

    return kept, dropped


def _held_qty(portfolio: PortfolioView, ticker: str) -> float:
    for p in portfolio.positions:
        if p.ticker == ticker:
            return p.qty
    return 0.0


def size_and_validate(
    decisions: list[Decision],
    portfolio: PortfolioView,
    last_prices: dict[str, float],
    buying_power: float,
    risk: RiskConfig,
    halt: bool,
) -> tuple[list[SizedOrder], list[RejectedOrder]]:
    """
    Turn validated decisions into concrete sized orders or rejections.

    Sells/holds for existing positions are handled first (they free capital and
    a sell is exempt from the buying-power/position-count checks).
    """
    accepted: list[SizedOrder] = []
    rejected: list[RejectedOrder] = []

    equity = portfolio.equity
    current_position_count = portfolio.num_positions
    held_tickers = {p.ticker for p in portfolio.positions}
    remaining_bp = buying_power

    # Process sells/holds first.
    ordered = sorted(decisions, key=lambda d: 0 if d.action in ("sell", "hold") else 1)

    for d in ordered:
        ticker = d.ticker
        price = last_prices.get(ticker)

        if d.action == "hold":
            continue

        if d.action == "sell":
            held = _held_qty(portfolio, ticker)
            if held <= 0:
                rejected.append(RejectedOrder(ticker, "sell", "no open position to sell"))
                continue
            if not price or price <= 0:
                rejected.append(RejectedOrder(ticker, "sell", "no price available"))
                continue
            accepted.append(
                SizedOrder(
                    ticker=ticker, action="sell", qty=held, entry_price=price,
                    stop_price=0.0, target_price=0.0, notional=held * price,
                    rationale=d.rationale, confidence=d.confidence, decision_id=d.id,
                )
            )
            current_position_count -= 1
            continue

        # --- buy path ---
        if halt:
            rejected.append(RejectedOrder(ticker, "buy", "trading halted (kill switch / daily loss)"))
            continue
        if not price or price <= 0:
            rejected.append(RejectedOrder(ticker, "buy", "no price available"))
            continue

        is_new = ticker not in held_tickers
        if is_new and current_position_count >= risk.max_concurrent_positions:
            rejected.append(RejectedOrder(ticker, "buy", "max concurrent positions reached"))
            continue

        # Convert proposed weight -> notional, capped at max position size.
        target_weight = min(d.proposed_weight, risk.max_position_pct)
        target_notional = target_weight * equity

        # If adding to an existing name, don't exceed the per-name cap in total.
        if not is_new:
            existing_mv = next((p.market_value for p in portfolio.positions if p.ticker == ticker), 0.0)
            room = max(0.0, risk.max_position_pct * equity - existing_mv)
            target_notional = min(target_notional, room)

        if target_notional < risk.min_order_notional:
            rejected.append(RejectedOrder(ticker, "buy", f"notional ${target_notional:.0f} below minimum"))
            continue
        if target_notional > remaining_bp:
            # Trim to available buying power if still meaningful, else reject.
            if remaining_bp >= risk.min_order_notional:
                target_notional = remaining_bp
            else:
                rejected.append(RejectedOrder(ticker, "buy", "insufficient buying power"))
                continue

        qty = int(target_notional // price)  # whole shares for bracket orders
        if qty < 1:
            rejected.append(RejectedOrder(ticker, "buy", "sized to <1 share"))
            continue

        notional = qty * price
        stop_price = round(price * (1 + d.stop_pct), 2)
        target_price = round(price * (1 + d.target_pct), 2)

        accepted.append(
            SizedOrder(
                ticker=ticker, action="buy", qty=qty, entry_price=price,
                stop_price=stop_price, target_price=target_price, notional=notional,
                rationale=d.rationale, confidence=d.confidence, decision_id=d.id,
            )
        )
        remaining_bp -= notional
        if is_new:
            current_position_count += 1
            held_tickers.add(ticker)

    return accepted, rejected
