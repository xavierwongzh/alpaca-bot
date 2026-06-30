"""
Layer 4 (part 2): alerts.

Raises console alerts when:
  - the daily-loss halt triggers (kill switch)
  - a target or stop has filled (detected from recently closed orders)
  - an open position has moved more than a configured threshold
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from config import RiskConfig
from src.analytics import PortfolioView
from src.logger import get_logger

log = get_logger()


@dataclass
class Alert:
    level: str          # "info" | "warning" | "critical"
    message: str

    def as_dict(self) -> dict[str, str]:
        return {"level": self.level, "message": self.message}


def build_alerts(
    portfolio: PortfolioView,
    account_day_pnl_pct: float,
    risk: RiskConfig,
    halt_triggered: bool,
    recently_filled: list[Any] | None = None,
) -> list[Alert]:
    alerts: list[Alert] = []

    if halt_triggered:
        alerts.append(Alert(
            "critical",
            f"DAILY-LOSS HALT: day P&L {account_day_pnl_pct:+.2%} breached "
            f"limit {risk.max_daily_loss_pct:+.0%}. No new buys today.",
        ))

    # Big-move alerts on open positions.
    for p in portfolio.positions:
        if abs(p.unrealized_pl_pct) >= risk.big_move_alert_pct:
            level = "warning" if p.unrealized_pl_pct < 0 else "info"
            alerts.append(Alert(
                level,
                f"{p.ticker} moved {p.unrealized_pl_pct:+.1%} "
                f"(uP&L ${p.unrealized_pl:+,.0f}).",
            ))
        # Proximity to stop / target.
        if p.dist_to_stop_pct <= 0.01:
            alerts.append(Alert("warning", f"{p.ticker} is at/through its stop."))
        elif p.dist_to_target_pct <= 0.01:
            alerts.append(Alert("info", f"{p.ticker} is at/through its target."))

    # Filled stop/target orders detected from broker (if provided).
    for o in recently_filled or []:
        try:
            otype = str(getattr(o, "order_type", "") or getattr(o, "type", "")).lower()
            sym = getattr(o, "symbol", "?")
            if "stop" in otype:
                alerts.append(Alert("warning", f"{sym}: STOP filled."))
            elif "limit" in otype:
                alerts.append(Alert("info", f"{sym}: TARGET (take-profit) filled."))
        except Exception:  # noqa: BLE001
            continue

    return alerts
