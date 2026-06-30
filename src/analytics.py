"""
Layer 2: Portfolio analytics.

Builds a whole-portfolio view from Alpaca positions + the account summary:
cash vs invested, concentration, total unrealized P&L, and how each open
position sits relative to its stop and target.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from config import RiskConfig
from src.broker import AccountSummary


@dataclass
class PositionView:
    ticker: str
    qty: float
    side: str
    avg_entry: float
    last_price: float
    market_value: float
    cost_basis: float
    unrealized_pl: float
    unrealized_pl_pct: float
    weight: float                 # fraction of portfolio value
    stop_price: float
    target_price: float
    dist_to_stop_pct: float       # how far price is above the stop (positive = safe)
    dist_to_target_pct: float     # how far below the target (positive = room to run)

    def as_dict(self) -> dict[str, Any]:
        return {
            "ticker": self.ticker,
            "qty": self.qty,
            "side": self.side,
            "avg_entry": round(self.avg_entry, 2),
            "last_price": round(self.last_price, 2),
            "market_value": round(self.market_value, 2),
            "unrealized_pl": round(self.unrealized_pl, 2),
            "unrealized_pl_pct": round(self.unrealized_pl_pct, 4),
            "weight": round(self.weight, 4),
            "stop_price": round(self.stop_price, 2),
            "target_price": round(self.target_price, 2),
            "dist_to_stop_pct": round(self.dist_to_stop_pct, 4),
            "dist_to_target_pct": round(self.dist_to_target_pct, 4),
        }


@dataclass
class PortfolioView:
    equity: float
    cash: float
    invested: float
    cash_pct: float
    invested_pct: float
    total_unrealized_pl: float
    largest_position_weight: float
    num_positions: int
    positions: list[PositionView] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "equity": round(self.equity, 2),
            "cash": round(self.cash, 2),
            "invested": round(self.invested, 2),
            "cash_pct": round(self.cash_pct, 4),
            "invested_pct": round(self.invested_pct, 4),
            "total_unrealized_pl": round(self.total_unrealized_pl, 2),
            "largest_position_weight": round(self.largest_position_weight, 4),
            "num_positions": self.num_positions,
            "positions": [p.as_dict() for p in self.positions],
        }


def build_portfolio_view(
    account: AccountSummary,
    raw_positions: list[Any],
    risk: RiskConfig,
) -> PortfolioView:
    equity = account.equity
    positions: list[PositionView] = []
    invested = 0.0
    total_upl = 0.0

    for p in raw_positions:
        qty = float(p.qty)
        avg_entry = float(p.avg_entry_price)
        last_price = float(p.current_price) if p.current_price else avg_entry
        market_value = float(p.market_value) if p.market_value else qty * last_price
        cost_basis = float(p.cost_basis) if p.cost_basis else qty * avg_entry
        upl = float(p.unrealized_pl) if p.unrealized_pl is not None else market_value - cost_basis
        upl_pct = float(p.unrealized_plpc) if p.unrealized_plpc is not None else (
            upl / cost_basis if cost_basis else 0.0
        )
        weight = market_value / equity if equity else 0.0

        stop_price = avg_entry * (1 + risk.stop_loss_pct)
        target_price = avg_entry * (1 + risk.profit_target_pct)
        dist_to_stop = (last_price - stop_price) / last_price if last_price else 0.0
        dist_to_target = (target_price - last_price) / last_price if last_price else 0.0

        positions.append(
            PositionView(
                ticker=p.symbol,
                qty=qty,
                side=str(p.side).split(".")[-1].lower(),
                avg_entry=avg_entry,
                last_price=last_price,
                market_value=market_value,
                cost_basis=cost_basis,
                unrealized_pl=upl,
                unrealized_pl_pct=upl_pct,
                weight=weight,
                stop_price=stop_price,
                target_price=target_price,
                dist_to_stop_pct=dist_to_stop,
                dist_to_target_pct=dist_to_target,
            )
        )
        invested += market_value
        total_upl += upl

    cash = account.cash
    largest_weight = max((p.weight for p in positions), default=0.0)

    return PortfolioView(
        equity=equity,
        cash=cash,
        invested=invested,
        cash_pct=cash / equity if equity else 0.0,
        invested_pct=invested / equity if equity else 0.0,
        total_unrealized_pl=total_upl,
        largest_position_weight=largest_weight,
        num_positions=len(positions),
        positions=positions,
    )
