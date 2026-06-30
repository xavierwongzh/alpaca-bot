"""
Layer 1: Data and valuation.

Pulls quotes and daily bars from Alpaca, computes per-position P&L and basic
technicals (20/50 SMA, RSI-14, distance from 52-week low/high).

All data is fetched once per run and cached on the MarketData instance.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import numpy as np
import pandas as pd
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import (
    StockBarsRequest,
    StockLatestQuoteRequest,
    StockLatestTradeRequest,
)
from alpaca.data.timeframe import TimeFrame

from config import Secrets
from src.logger import get_logger

log = get_logger()


@dataclass
class Technicals:
    ticker: str
    last_price: float = float("nan")
    sma20: float = float("nan")
    sma50: float = float("nan")
    rsi14: float = float("nan")
    high_52w: float = float("nan")
    low_52w: float = float("nan")
    pct_from_52w_low: float = float("nan")
    pct_from_52w_high: float = float("nan")
    avg_dollar_vol_20d: float = float("nan")
    trend: str = "n/a"          # "above_50sma" | "below_50sma" | "n/a"

    def as_dict(self) -> dict[str, Any]:
        return {
            "ticker": self.ticker,
            "last_price": _round(self.last_price),
            "sma20": _round(self.sma20),
            "sma50": _round(self.sma50),
            "rsi14": _round(self.rsi14, 1),
            "high_52w": _round(self.high_52w),
            "low_52w": _round(self.low_52w),
            "pct_from_52w_low": _round(self.pct_from_52w_low, 3),
            "pct_from_52w_high": _round(self.pct_from_52w_high, 3),
            "avg_dollar_vol_20d": _round(self.avg_dollar_vol_20d, 0),
            "trend": self.trend,
        }


def _round(x: float, n: int = 2) -> Optional[float]:
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return None
    return round(float(x), n)


def _rsi(close: pd.Series, period: int = 14) -> float:
    if len(close) < period + 1:
        return float("nan")
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period).mean().iloc[-1]
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period).mean().iloc[-1]
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return float(100.0 - (100.0 / (1.0 + rs)))


class MarketData:
    def __init__(self, secrets: Secrets, lookback_days: int = 260):
        # Market-data client uses the same keys; data endpoint is read-only.
        self.client = StockHistoricalDataClient(
            api_key=secrets.alpaca_api_key,
            secret_key=secrets.alpaca_secret_key,
        )
        self.lookback_days = lookback_days
        self._bars_cache: dict[str, pd.DataFrame] = {}
        self._tech_cache: dict[str, Technicals] = {}

    # -- bars ---------------------------------------------------------------
    def get_bars(self, tickers: list[str]) -> dict[str, pd.DataFrame]:
        """Daily bars for each ticker, cached. Missing tickers are skipped."""
        to_fetch = [t for t in tickers if t not in self._bars_cache]
        if to_fetch:
            start = datetime.now(timezone.utc) - timedelta(days=int(self.lookback_days * 1.6))
            req = StockBarsRequest(
                symbol_or_symbols=to_fetch,
                timeframe=TimeFrame.Day,
                start=start,
            )
            try:
                bars = self.client.get_stock_bars(req)
                df = bars.df  # MultiIndex (symbol, timestamp)
            except Exception as e:  # noqa: BLE001
                log.warning("Bar fetch failed for %s: %s", to_fetch, e)
                df = pd.DataFrame()
            for t in to_fetch:
                if not df.empty and t in df.index.get_level_values(0):
                    self._bars_cache[t] = df.xs(t, level=0).copy()
                else:
                    self._bars_cache[t] = pd.DataFrame()
        return {t: self._bars_cache.get(t, pd.DataFrame()) for t in tickers}

    # -- quotes -------------------------------------------------------------
    # Max accepted bid/ask spread (as a fraction of mid) before a quote is
    # treated as unreliable. The free IEX feed posts very wide quotes overnight
    # (e.g. NVDA bid 194.8 / ask 211.65), whose mid is meaningless — so the last
    # *trade* is the primary source and a quote mid is only a sanity-checked
    # fallback.
    MAX_QUOTE_SPREAD = 0.025

    def get_last_prices(self, tickers: list[str]) -> dict[str, float]:
        """
        Latest price per ticker. Priority:
          1. latest trade price (most representative)
          2. quote mid, only if the spread is sane (<= MAX_QUOTE_SPREAD)
          3. last daily bar close
        """
        prices: dict[str, float] = {}

        # 1. Latest trade.
        try:
            trades = self.client.get_stock_latest_trade(
                StockLatestTradeRequest(symbol_or_symbols=tickers)
            )
            for t, tr in trades.items():
                px = float(tr.price or 0)
                if px > 0:
                    prices[t] = px
        except Exception as e:  # noqa: BLE001
            log.warning("Trade fetch failed: %s", e)

        # 2. Quote mid (spread-guarded) for anything still missing.
        missing = [t for t in tickers if t not in prices]
        if missing:
            try:
                quotes = self.client.get_stock_latest_quote(
                    StockLatestQuoteRequest(symbol_or_symbols=missing)
                )
                for t, q in quotes.items():
                    bid, ask = float(q.bid_price or 0), float(q.ask_price or 0)
                    if bid > 0 and ask > 0:
                        mid = (bid + ask) / 2
                        if (ask - bid) / mid <= self.MAX_QUOTE_SPREAD:
                            prices[t] = mid
            except Exception as e:  # noqa: BLE001
                log.warning("Quote fetch failed: %s", e)

        # 3. Fall back to last daily close.
        missing = [t for t in tickers if t not in prices]
        if missing:
            for t, df in self.get_bars(missing).items():
                if not df.empty:
                    prices[t] = float(df["close"].iloc[-1])
        return prices

    # -- technicals ---------------------------------------------------------
    def technicals(self, tickers: list[str]) -> dict[str, Technicals]:
        need = [t for t in tickers if t not in self._tech_cache]
        if need:
            bars = self.get_bars(need)
            prices = self.get_last_prices(need)
            for t in need:
                self._tech_cache[t] = self._compute_technicals(t, bars[t], prices.get(t))
        return {t: self._tech_cache[t] for t in tickers}

    def _compute_technicals(
        self, ticker: str, df: pd.DataFrame, last_price: Optional[float]
    ) -> Technicals:
        tech = Technicals(ticker=ticker)
        if df is None or df.empty:
            if last_price:
                tech.last_price = last_price
            return tech
        close = df["close"].astype(float)
        tech.last_price = float(last_price) if last_price else float(close.iloc[-1])
        if len(close) >= 20:
            tech.sma20 = float(close.tail(20).mean())
        if len(close) >= 50:
            tech.sma50 = float(close.tail(50).mean())
            tech.trend = "above_50sma" if tech.last_price >= tech.sma50 else "below_50sma"
        tech.rsi14 = _rsi(close)
        window_52w = close.tail(252)
        tech.high_52w = float(window_52w.max())
        tech.low_52w = float(window_52w.min())
        if tech.low_52w:
            tech.pct_from_52w_low = (tech.last_price - tech.low_52w) / tech.low_52w
        if tech.high_52w:
            tech.pct_from_52w_high = (tech.last_price - tech.high_52w) / tech.high_52w
        if "volume" in df.columns and len(df) >= 20:
            vol20 = df["volume"].tail(20).astype(float).mean()
            tech.avg_dollar_vol_20d = float(vol20 * tech.last_price)
        return tech
