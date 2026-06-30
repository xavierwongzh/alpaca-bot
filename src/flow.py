"""
Options-flow scanner.

Scans option contracts across the configured WATCHLIST, drops noise with
per-contract filters, scores each qualifying contract with a weighted composite,
aggregates to a per-ticker direction, and emits the top-N signals.

ALL thresholds live in config.FlowConfig; nothing is hardcoded here.

Data sources (config.flow.source):
  - "alpaca": live option-chain snapshots via alpaca-py
  - "csv":    data/flow_contracts.csv (offline / testing)
  - "auto":   try alpaca, fall back to csv

Because the bot trades the underlying equity LONG-ONLY in v1, only bullish
ticker signals become candidate long entries; bearish signals are passed to the
decision engine as caution context, never as short orders.
"""
from __future__ import annotations

import csv
import json
import os
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Optional

from config import FlowConfig, Paths, Secrets
from src.logger import get_logger

log = get_logger()

# OCC option symbol: ROOT + YYMMDD + C/P + strike(8 digits, price*1000)
_OCC_RE = re.compile(r"^([A-Z]+)(\d{6})([CP])(\d{8})$")


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------
@dataclass
class OptionContract:
    underlying: str
    symbol: str
    type: str                 # "call" | "put"
    strike: float
    expiry: date
    dte: int
    spot: float
    contract_price: float
    bid: float
    ask: float
    volume: float
    open_interest: float
    implied_volatility: Optional[float] = None
    # derived
    vol_oi_ratio: float = 0.0
    notional: float = 0.0
    moneyness: float = 0.0            # (strike - spot) / spot
    aggression: Optional[float] = None
    is_spec_otm_call: bool = False
    composite_score: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "underlying": self.underlying,
            "symbol": self.symbol,
            "type": self.type,
            "strike": self.strike,
            "expiry": self.expiry.isoformat(),
            "dte": self.dte,
            "contract_price": round(self.contract_price, 4),
            "volume": self.volume,
            "open_interest": self.open_interest,
            "vol_oi_ratio": round(self.vol_oi_ratio, 2),
            "notional": round(self.notional, 0),
            "moneyness": round(self.moneyness, 4),
            "aggression": round(self.aggression, 3) if self.aggression is not None else None,
            "is_spec_otm_call": self.is_spec_otm_call,
            "implied_volatility": round(self.implied_volatility, 4) if self.implied_volatility else None,
            "composite_score": round(self.composite_score, 1),
        }


@dataclass
class FlowSignal:
    ticker: str
    direction: str                    # "bullish" | "bearish"
    composite_score: float
    top_contract: dict[str, Any]
    vol_oi_ratio: float
    notional: float                   # total qualifying notional on the ticker
    aggression: Optional[float]
    call_put_notional_ratio: float
    iv: Optional[float]
    rationale: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "ticker": self.ticker,
            "direction": self.direction,
            "composite_score": round(self.composite_score, 1),
            "top_contract": self.top_contract,
            "vol_oi_ratio": round(self.vol_oi_ratio, 2),
            "notional": round(self.notional, 0),
            "aggression": round(self.aggression, 3) if self.aggression is not None else None,
            "call_put_notional_ratio": round(self.call_put_notional_ratio, 2),
            "iv": round(self.iv, 4) if self.iv else None,
            "rationale": self.rationale,
        }


# ---------------------------------------------------------------------------
# Symbol parsing
# ---------------------------------------------------------------------------
def parse_occ_symbol(symbol: str) -> Optional[tuple[str, str, float, date]]:
    """(underlying, 'call'|'put', strike, expiry) from an OCC symbol, or None."""
    m = _OCC_RE.match(symbol.strip().upper())
    if not m:
        return None
    root, yymmdd, cp, strike_raw = m.groups()
    try:
        expiry = datetime.strptime(yymmdd, "%y%m%d").date()
    except ValueError:
        return None
    strike = int(strike_raw) / 1000.0
    return root, ("call" if cp == "C" else "put"), strike, expiry


# ---------------------------------------------------------------------------
# Scoring helpers (all thresholds from FlowConfig)
# ---------------------------------------------------------------------------
def _aggression(last: float, bid: float, ask: float) -> Optional[float]:
    """(last - bid) / (ask - bid), clamped to [0, 1]; None if spread invalid."""
    spread = ask - bid
    if spread <= 0 or bid <= 0 or ask <= 0 or last <= 0:
        return None
    return max(0.0, min(1.0, (last - bid) / spread))


def _composite_score(c: OptionContract, cfg: FlowConfig) -> float:
    vol_oi_n = min(c.vol_oi_ratio / cfg.VOL_OI_CAP, 1.0) if cfg.VOL_OI_CAP else 0.0
    notional_n = min(c.notional / cfg.NOTIONAL_CAP, 1.0) if cfg.NOTIONAL_CAP else 0.0
    aggr = c.aggression if c.aggression is not None else 0.0
    span = max(cfg.DTE_MAX - cfg.DTE_MIN, 1)
    dte_urgency = max(0.0, min(1.0, (cfg.DTE_MAX - c.dte) / span))
    score = (
        cfg.W_VOL_OI * vol_oi_n
        + cfg.W_NOTIONAL * notional_n
        + cfg.W_AGGRESSION * aggr
        + cfg.W_DTE * dte_urgency
    )
    return 100.0 * score


def _passes_filters(c: OptionContract, cfg: FlowConfig) -> bool:
    if c.volume < cfg.MIN_CONTRACT_VOLUME:
        return False
    if c.open_interest <= 0 or c.vol_oi_ratio < cfg.MIN_VOL_OI_RATIO:
        return False
    if c.notional < cfg.MIN_NOTIONAL_USD:
        return False
    if not (cfg.DTE_MIN <= c.dte <= cfg.DTE_MAX):
        return False
    if abs(c.moneyness) > cfg.MONEYNESS_MAX:
        return False
    return True


def _enrich(c: OptionContract, cfg: FlowConfig) -> OptionContract:
    c.vol_oi_ratio = c.volume / c.open_interest if c.open_interest > 0 else 0.0
    c.notional = c.volume * c.contract_price * 100.0
    c.moneyness = (c.strike - c.spot) / c.spot if c.spot > 0 else 0.0
    c.is_spec_otm_call = (
        c.type == "call" and 0.0 <= c.moneyness <= cfg.OTM_CALL_SPEC_MAX
    )
    c.aggression = _aggression(c.contract_price, c.bid, c.ask)
    c.composite_score = _composite_score(c, cfg)
    return c


# ---------------------------------------------------------------------------
# Data sources
# ---------------------------------------------------------------------------
def _to_float(v: Any, default: float = 0.0) -> float:
    try:
        return float(str(v).replace(",", "").strip())
    except (ValueError, TypeError, AttributeError):
        return default


def fetch_contracts_csv(path: str, spot_prices: dict[str, float]) -> list[OptionContract]:
    """Read per-contract rows from a CSV (offline source)."""
    if not os.path.exists(path):
        log.warning("Flow contracts CSV not found at %s", path)
        return []
    today = datetime.now(timezone.utc).date()
    out: list[OptionContract] = []
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            underlying = (row.get("underlying") or "").strip().upper()
            if not underlying:
                continue
            try:
                expiry = datetime.strptime((row.get("expiry") or "").strip(), "%Y-%m-%d").date()
            except ValueError:
                continue
            spot = spot_prices.get(underlying) or _to_float(row.get("spot"))
            if spot <= 0:
                continue
            out.append(OptionContract(
                underlying=underlying,
                symbol=(row.get("option_symbol") or "").strip().upper(),
                type=(row.get("type") or "").strip().lower(),
                strike=_to_float(row.get("strike")),
                expiry=expiry,
                dte=(expiry - today).days,
                spot=spot,
                contract_price=_to_float(row.get("contract_price")),
                bid=_to_float(row.get("bid")),
                ask=_to_float(row.get("ask")),
                volume=_to_float(row.get("volume")),
                open_interest=_to_float(row.get("open_interest")),
                implied_volatility=_to_float(row.get("implied_volatility")) or None,
            ))
    return out


def fetch_contracts_alpaca(
    secrets: Secrets, tickers: list[str], spot_prices: dict[str, float], cfg: FlowConfig
) -> list[OptionContract]:
    """Pull option-chain snapshots from Alpaca. Returns [] if unavailable."""
    try:
        from alpaca.data.historical.option import OptionHistoricalDataClient
        from alpaca.data.requests import OptionChainRequest
        from alpaca.data.enums import OptionsFeed
    except Exception as e:  # noqa: BLE001
        log.warning("alpaca-py options API unavailable: %s", e)
        return []

    feed = OptionsFeed.OPRA if cfg.options_feed.lower() == "opra" else OptionsFeed.INDICATIVE
    client = OptionHistoricalDataClient(secrets.alpaca_api_key, secrets.alpaca_secret_key)
    today = datetime.now(timezone.utc).date()
    out: list[OptionContract] = []

    for t in tickers:
        spot = spot_prices.get(t, 0.0)
        if spot <= 0:
            continue
        try:
            chain = client.get_option_chain(OptionChainRequest(underlying_symbol=t, feed=feed))
        except Exception as e:  # noqa: BLE001
            log.debug("Option chain fetch failed for %s: %s", t, e)
            continue
        for symbol, snap in (chain or {}).items():
            parsed = parse_occ_symbol(symbol)
            if not parsed:
                continue
            _root, opt_type, strike, expiry = parsed
            daily_bar = getattr(snap, "daily_bar", None)
            quote = getattr(snap, "latest_quote", None)
            trade = getattr(snap, "latest_trade", None)
            oi = getattr(snap, "open_interest", None)
            volume = _to_float(getattr(daily_bar, "volume", 0)) if daily_bar else 0.0
            last = _to_float(getattr(trade, "price", 0)) if trade else 0.0
            if not last and daily_bar:
                last = _to_float(getattr(daily_bar, "close", 0))
            bid = _to_float(getattr(quote, "bid_price", 0)) if quote else 0.0
            ask = _to_float(getattr(quote, "ask_price", 0)) if quote else 0.0
            if oi is None or volume <= 0 or last <= 0:
                # Can't assess new positioning without OI/volume/price; skip.
                continue
            out.append(OptionContract(
                underlying=t,
                symbol=symbol,
                type=opt_type,
                strike=strike,
                expiry=expiry,
                dte=(expiry - today).days,
                spot=spot,
                contract_price=last,
                bid=bid,
                ask=ask,
                volume=volume,
                open_interest=_to_float(oi),
                implied_volatility=_to_float(getattr(snap, "implied_volatility", None)) or None,
            ))
    return out


# ---------------------------------------------------------------------------
# Aggregation -> ticker signals
# ---------------------------------------------------------------------------
def _build_ticker_signal(
    ticker: str, contracts: list[OptionContract], cfg: FlowConfig
) -> Optional[FlowSignal]:
    if not contracts:
        return None
    call_notional = sum(c.notional for c in contracts if c.type == "call")
    put_notional = sum(c.notional for c in contracts if c.type == "put")
    ratio = call_notional / max(put_notional, 1.0)
    total_notional = call_notional + put_notional

    has_aggr_calls = any(
        c.type == "call" and c.aggression is not None and c.aggression >= cfg.AGGRESSION_BUY
        for c in contracts
    )
    has_aggr_puts = any(
        c.type == "put" and c.aggression is not None and c.aggression >= cfg.AGGRESSION_BUY
        for c in contracts
    )

    if ratio >= cfg.BULLISH_CP_RATIO and has_aggr_calls:
        direction = "bullish"
        rationale = (
            f"Dominant aggressive call buying (call/put notional {ratio:.1f}x); "
            f"treated as a long entry trigger on {ticker}."
        )
    elif ratio <= cfg.BEARISH_CP_RATIO and has_aggr_puts:
        direction = "bearish"
        rationale = (
            f"Heavy put buying (call/put notional {ratio:.2f}x). Caution, not a short: "
            f"put flow can be hedging. Consider avoiding/trimming {ticker}."
        )
    else:
        return None

    top = max(contracts, key=lambda c: c.composite_score)
    return FlowSignal(
        ticker=ticker,
        direction=direction,
        composite_score=top.composite_score,
        top_contract={
            "symbol": top.symbol, "type": top.type,
            "strike": top.strike, "expiry": top.expiry.isoformat(),
        },
        vol_oi_ratio=top.vol_oi_ratio,
        notional=total_notional,
        aggression=top.aggression,
        call_put_notional_ratio=ratio,
        iv=top.implied_volatility,
        rationale=rationale,
    )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------
def scan_flow(
    secrets: Secrets,
    cfg: FlowConfig,
    paths: Paths,
    tickers: list[str],
    spot_prices: dict[str, float],
) -> list[FlowSignal]:
    """
    Run the full scan: fetch -> filter -> score -> aggregate -> rank.
    Writes the full ranked signal list to data/flow_cache.json and returns the
    top-N FlowSignal objects.
    """
    # --- fetch contracts from the configured source ---
    raw: list[OptionContract] = []
    if cfg.source in ("alpaca", "auto"):
        raw = fetch_contracts_alpaca(secrets, tickers, spot_prices, cfg)
    if not raw and cfg.source in ("csv", "auto"):
        if cfg.source == "auto":
            log.info("No live option flow available; falling back to CSV source.")
        raw = fetch_contracts_csv(paths.flow_contracts_csv, spot_prices)

    # --- enrich + filter ---
    qualifying: list[OptionContract] = []
    for c in raw:
        if c.type not in ("call", "put") or c.spot <= 0 or c.contract_price <= 0:
            continue
        _enrich(c, cfg)
        if _passes_filters(c, cfg):
            qualifying.append(c)

    qualifying.sort(key=lambda c: c.composite_score, reverse=True)

    # --- aggregate to ticker signals ---
    by_ticker: dict[str, list[OptionContract]] = {}
    for c in qualifying:
        by_ticker.setdefault(c.underlying, []).append(c)

    signals: list[FlowSignal] = []
    for tk, contracts in by_ticker.items():
        sig = _build_ticker_signal(tk, contracts, cfg)
        if sig:
            signals.append(sig)
    signals.sort(key=lambda s: s.composite_score, reverse=True)

    # --- write full ranked cache for inspection ---
    _write_cache(paths.flow_cache_json, cfg, qualifying, signals)

    top = signals[: cfg.TOP_N_SIGNALS]
    bull = [s.ticker for s in top if s.direction == "bullish"]
    bear = [s.ticker for s in top if s.direction == "bearish"]
    log.info(
        "Flow scan: %d contracts -> %d qualifying -> %d signals "
        "(top %d: %d bullish %s, %d bearish %s)",
        len(raw), len(qualifying), len(signals), len(top),
        len(bull), bull or "-", len(bear), bear or "-",
    )
    return top


def _write_cache(
    path: str, cfg: FlowConfig,
    contracts: list[OptionContract], signals: list[FlowSignal],
) -> None:
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "thresholds": {
            "MIN_CONTRACT_VOLUME": cfg.MIN_CONTRACT_VOLUME,
            "MIN_VOL_OI_RATIO": cfg.MIN_VOL_OI_RATIO,
            "MIN_NOTIONAL_USD": cfg.MIN_NOTIONAL_USD,
            "DTE_MIN": cfg.DTE_MIN, "DTE_MAX": cfg.DTE_MAX,
            "MONEYNESS_MAX": cfg.MONEYNESS_MAX,
            "AGGRESSION_BUY": cfg.AGGRESSION_BUY,
            "weights": {"vol_oi": cfg.W_VOL_OI, "notional": cfg.W_NOTIONAL,
                        "aggression": cfg.W_AGGRESSION, "dte": cfg.W_DTE},
        },
        "signals_ranked": [s.as_dict() for s in signals],
        "qualifying_contracts_ranked": [c.as_dict() for c in contracts],
    }
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, default=str)
    except Exception as e:  # noqa: BLE001
        log.warning("Failed to write flow cache: %s", e)


def bullish_tickers(signals: list[FlowSignal]) -> list[str]:
    """Unique underlyings flagged bullish -> candidate long entries."""
    seen: list[str] = []
    for s in signals:
        if s.direction == "bullish" and s.ticker not in seen:
            seen.append(s.ticker)
    return seen
