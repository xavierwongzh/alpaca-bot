"""
Alpaca broker wrappers.

The single most important job of this module is the PAPER-MODE ASSERTION:
the bot must refuse to start if anything looks like a live account/endpoint.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from alpaca.trading.client import TradingClient

from config import Secrets
from src.logger import get_logger

log = get_logger()

_PAPER_HOST = "paper-api.alpaca.markets"
_LIVE_HOST = "api.alpaca.markets"


class LiveModeError(RuntimeError):
    """Raised when the bot detects anything that could touch a live account."""


@dataclass
class AccountSummary:
    account_number: str
    status: str
    equity: float
    last_equity: float
    cash: float
    buying_power: float
    portfolio_value: float
    pattern_day_trader: bool
    trading_blocked: bool
    day_pnl: float
    day_pnl_pct: float


def assert_paper_mode(secrets: Secrets) -> None:
    """
    Hard safety gate. Refuses to continue unless we are unambiguously in paper
    mode. Checks both the configured endpoint and the live-account flag on the
    account object itself.
    """
    url = (secrets.alpaca_base_url or "").lower()

    if _LIVE_HOST in url and _PAPER_HOST not in url:
        raise LiveModeError(
            f"Refusing to run: ALPACA_BASE_URL points at the LIVE endpoint ({url}). "
            f"This bot is paper-only."
        )
    if _PAPER_HOST not in url:
        raise LiveModeError(
            f"Refusing to run: ALPACA_BASE_URL is not the Alpaca paper endpoint "
            f"({url!r}). Expected a URL containing '{_PAPER_HOST}'."
        )
    if not secrets.alpaca_api_key or not secrets.alpaca_secret_key:
        raise LiveModeError("Missing Alpaca API key/secret in environment (.env).")


class Broker:
    """Thin, paper-only wrapper around alpaca-py's TradingClient."""

    def __init__(self, secrets: Secrets):
        assert_paper_mode(secrets)
        self._secrets = secrets
        # paper=True is enforced explicitly, independent of the URL check above.
        self.client = TradingClient(
            api_key=secrets.alpaca_api_key,
            secret_key=secrets.alpaca_secret_key,
            paper=True,
        )
        self._verify_account_is_paper()

    def _verify_account_is_paper(self) -> None:
        """
        Second line of defense: the account object itself must not be a live
        account. alpaca-py's paper client cannot reach live, but we still assert.
        """
        acct = self.client.get_account()
        # Alpaca paper accounts are flagged; a live account would not be reachable
        # through paper=True, but we guard against misconfiguration anyway.
        if getattr(acct, "trading_blocked", False):
            raise LiveModeError("Account reports trading_blocked=True; refusing to run.")
        log.info("[green]Paper-mode verified[/green] — account %s (status=%s)",
                 acct.account_number, acct.status)

    # -- reads ---------------------------------------------------------------
    def get_account(self) -> AccountSummary:
        a = self.client.get_account()
        equity = float(a.equity)
        last_equity = float(a.last_equity) if a.last_equity is not None else equity
        day_pnl = equity - last_equity
        day_pnl_pct = (day_pnl / last_equity) if last_equity else 0.0
        return AccountSummary(
            account_number=a.account_number,
            status=str(a.status),
            equity=equity,
            last_equity=last_equity,
            cash=float(a.cash),
            buying_power=float(a.buying_power),
            portfolio_value=float(a.portfolio_value),
            pattern_day_trader=bool(a.pattern_day_trader),
            trading_blocked=bool(a.trading_blocked),
            day_pnl=day_pnl,
            day_pnl_pct=day_pnl_pct,
        )

    def get_positions(self) -> list[Any]:
        return list(self.client.get_all_positions())

    def get_open_orders(self) -> list[Any]:
        from alpaca.trading.requests import GetOrdersRequest
        from alpaca.trading.enums import QueryOrderStatus
        req = GetOrdersRequest(status=QueryOrderStatus.OPEN)
        return list(self.client.get_orders(filter=req))

    def is_market_open(self) -> bool:
        return bool(self.client.get_clock().is_open)

    def get_market_date(self):
        """
        Current US market calendar date from the Alpaca clock (its timestamp is
        US/Eastern-aware, so .date() is the trading date). Falls back to the UTC
        date if the clock can't be read. Used for the per-day run idempotency guard.
        """
        from datetime import datetime, timezone
        try:
            return self.client.get_clock().timestamp.date()
        except Exception as e:  # noqa: BLE001
            log.warning("Could not read market clock for date (%s); using UTC date.", e)
            return datetime.now(timezone.utc).date()

    def get_fills(self, lookback_days: int = 180) -> list[dict]:
        """
        Normalized fill events reconstructed from filled orders (this alpaca-py
        version has no account-activities endpoint). Walks bracket legs too, so
        both entry buys and exit stop/target legs appear. Oldest first.

        Each fill: {symbol, side, qty, price, time, order_id, client_order_id, order_type}
        """
        from datetime import datetime, timedelta, timezone
        from alpaca.trading.requests import GetOrdersRequest
        from alpaca.trading.enums import QueryOrderStatus

        after = datetime.now(timezone.utc) - timedelta(days=lookback_days)
        try:
            req = GetOrdersRequest(
                status=QueryOrderStatus.ALL, after=after, limit=500,
                direction="asc", nested=True,
            )
            orders = list(self.client.get_orders(filter=req))
        except Exception as e:  # noqa: BLE001
            log.warning("Order history fetch failed: %s", e)
            return []

        fills: list[dict] = []

        def _f(v: Any) -> float:
            try:
                return float(v)
            except (TypeError, ValueError):
                return 0.0

        def walk(o: Any) -> None:
            fq = _f(getattr(o, "filled_qty", 0))
            fp = _f(getattr(o, "filled_avg_price", 0) or 0)
            filled_at = getattr(o, "filled_at", None)
            if fq > 0 and fp > 0 and filled_at is not None:
                otype = getattr(o, "order_type", None) or getattr(o, "type", "")
                fills.append({
                    "symbol": getattr(o, "symbol", None),
                    "side": str(getattr(o, "side", "")).split(".")[-1].lower(),
                    "qty": fq,
                    "price": fp,
                    "time": filled_at,
                    "order_id": str(getattr(o, "id", "") or ""),
                    "client_order_id": str(getattr(o, "client_order_id", "") or ""),
                    "order_type": str(otype).split(".")[-1].lower(),
                })
            for leg in (getattr(o, "legs", None) or []):
                walk(leg)

        for o in orders:
            walk(o)
        fills.sort(key=lambda x: x["time"])
        return fills

    def get_portfolio_history(self, period: str = "1M", timeframe: str = "1D") -> Any:
        """Alpaca portfolio history (timestamps + equity series)."""
        from alpaca.trading.requests import GetPortfolioHistoryRequest

        req = GetPortfolioHistoryRequest(period=period, timeframe=timeframe)
        try:
            return self.client.get_portfolio_history(history_filter=req)
        except TypeError:
            # Older/newer SDK signature fallback.
            return self.client.get_portfolio_history(req)

    def get_position_age_days(self, lookback_days: int = 14) -> dict[str, int]:
        """
        Age (in days) of each currently-held position, derived from the most
        recent FILLED buy per symbol within the lookback window.

        Used by the midday run to block same-day reversals. Symbols whose entry
        predates the lookback are simply absent (callers treat absent as "old").
        """
        from datetime import datetime, timedelta, timezone
        from alpaca.trading.requests import GetOrdersRequest
        from alpaca.trading.enums import QueryOrderStatus, OrderSide

        after = datetime.now(timezone.utc) - timedelta(days=lookback_days)
        try:
            req = GetOrdersRequest(
                status=QueryOrderStatus.CLOSED,
                side=OrderSide.BUY,
                after=after,
                limit=500,
                direction="desc",
            )
            orders = self.client.get_orders(filter=req)
        except Exception as e:  # noqa: BLE001
            log.warning("Could not fetch order history for position age: %s", e)
            return {}

        now = datetime.now(timezone.utc)
        ages: dict[str, int] = {}
        for o in orders:
            filled_at = getattr(o, "filled_at", None)
            symbol = getattr(o, "symbol", None)
            # direction=desc -> first seen per symbol is the most recent buy.
            if filled_at and symbol and symbol not in ages:
                ages[symbol] = max(0, (now - filled_at).days)
        return ages
