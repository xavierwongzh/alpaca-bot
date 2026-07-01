"""
Options-flow scanner.

Scans option contracts across the configured WATCHLIST, drops noise with
per-contract filters, scores each qualifying contract with a weighted composite,
aggregates to a per-ticker direction, and emits the top-N signals.

ALL thresholds live in config.FlowConfig; nothing is hardcoded here.

Live data sources (config.flow.source) — provider-adapter interface, so a paid
trade-level source can be swapped in later without touching the scorer:
  - "tradier":  Tradier API option chains (needs TRADIER_ACCESS_TOKEN)
  - "yfinance": free option chains
  - "auto":     Tradier if a token is configured, else yfinance (default)

There is NO stub/CSV production source: if the selected live source returns
nothing, the scanner yields zero signals rather than fabricating any. Both free
feeds (yfinance, the Tradier sandbox) are delayed ~15 min snapshots, not
tick-level, so the aggression proxy (last vs bid/ask) is an approximation, not
true sweep data — an accepted free-tier tradeoff.

Because the bot trades the underlying equity LONG-ONLY in v1, only bullish
ticker signals become candidate long entries; bearish signals are passed to the
decision engine as caution context, never as short orders.
"""
from __future__ import annotations

import csv
import json
import os
import re
from dataclasses import dataclass
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
    """
    Offline per-contract CSV loader. NOT a production `flow.source` — it exists
    only for deterministic tests and manual inspection. Live runs use the Tradier
    or yfinance adapters below.
    """
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


def fetch_contracts_yfinance(
    tickers: list[str], spot_prices: dict[str, float], cfg: FlowConfig
) -> list[OptionContract]:
    """
    Free options adapter via yfinance. For each ticker, pulls calls+puts for the
    expiries that fall inside the DTE window, with volume, OI, IV, bid, ask, last,
    strike and expiry.

    Note: yfinance option data is delayed ~15 minutes and is a SNAPSHOT, not
    tick-level — so the aggression proxy (last vs bid/ask) is weaker than true
    sweep data. Accepted free-tier tradeoff.

    Per-ticker errors are swallowed so one bad symbol never aborts the scan.
    """
    try:
        import yfinance as yf
    except Exception as e:  # noqa: BLE001
        log.warning("yfinance unavailable for options: %s", e)
        return []

    today = datetime.now(timezone.utc).date()
    out: list[OptionContract] = []
    ok_tickers = 0

    for t in tickers:
        spot = spot_prices.get(t, 0.0)
        if spot <= 0:
            continue
        try:
            tk = yf.Ticker(t)
            expiries = list(tk.options or [])
        except Exception as e:  # noqa: BLE001
            log.debug("yfinance expiries failed for %s: %s", t, e)
            continue

        # Only expiries inside the DTE window (bounds the number of API calls).
        wanted: list[tuple[str, "date"]] = []
        for exp_str in expiries:
            try:
                exp = datetime.strptime(exp_str, "%Y-%m-%d").date()
            except ValueError:
                continue
            dte = (exp - today).days
            if cfg.DTE_MIN <= dte <= cfg.DTE_MAX:
                wanted.append((exp_str, exp))
        if not wanted:
            continue

        got_any = False
        for exp_str, exp in wanted:
            try:
                chain = tk.option_chain(exp_str)
            except Exception as e:  # noqa: BLE001
                log.debug("yfinance chain failed for %s %s: %s", t, exp_str, e)
                continue
            for opt_type, df in (("call", chain.calls), ("put", chain.puts)):
                if df is None or df.empty:
                    continue
                for row in df.itertuples(index=False):
                    last = _to_float(getattr(row, "lastPrice", 0))
                    vol = _to_float(getattr(row, "volume", 0))
                    oi = _to_float(getattr(row, "openInterest", 0))
                    if last <= 0 or vol <= 0 or oi <= 0:
                        continue
                    out.append(OptionContract(
                        underlying=t,
                        symbol=str(getattr(row, "contractSymbol", "") or ""),
                        type=opt_type,
                        strike=_to_float(getattr(row, "strike", 0)),
                        expiry=exp,
                        dte=(exp - today).days,
                        spot=spot,
                        contract_price=last,
                        bid=_to_float(getattr(row, "bid", 0)),
                        ask=_to_float(getattr(row, "ask", 0)),
                        volume=vol,
                        open_interest=oi,
                        implied_volatility=_to_float(getattr(row, "impliedVolatility", 0)) or None,
                    ))
                    got_any = True
        if got_any:
            ok_tickers += 1

    log.info("yfinance options: %d contracts across %d/%d tickers",
             len(out), ok_tickers, len(tickers))
    return out


def fetch_contracts_tradier(
    tickers: list[str], spot_prices: dict[str, float], cfg: FlowConfig, token: str
) -> list[OptionContract]:
    """
    Tradier option-chain adapter (primary provider). For each ticker, pulls the
    expirations inside the DTE window, then the chain for each — with bid, ask,
    last, volume, open interest, option type, strike, and greeks (mid IV).

    Reads the bearer token from the caller (never hardcoded). Base URL defaults to
    the Tradier sandbox (free, delayed) and is configurable to production via
    cfg.tradier_base_url. Per-ticker errors are swallowed so one bad symbol never
    aborts the scan.
    """
    if not token:
        return []
    try:
        import requests
    except Exception as e:  # noqa: BLE001
        log.warning("requests unavailable for Tradier: %s", e)
        return []

    base = cfg.tradier_base_url.rstrip("/")
    timeout = cfg.tradier_timeout_s
    session = requests.Session()
    session.headers.update({"Authorization": f"Bearer {token}", "Accept": "application/json"})

    def _get(path: str, params: dict[str, Any]) -> Optional[dict]:
        r = session.get(f"{base}{path}", params=params, timeout=timeout)
        r.raise_for_status()
        return r.json()

    def _as_list(node: Any) -> list:
        # Tradier collapses single-element arrays to a bare object.
        if node is None:
            return []
        return node if isinstance(node, list) else [node]

    today = datetime.now(timezone.utc).date()
    out: list[OptionContract] = []
    ok_tickers = 0

    for t in tickers:
        spot = spot_prices.get(t, 0.0)
        if spot <= 0:
            continue
        try:
            exp_json = _get("/markets/options/expirations",
                            {"symbol": t, "includeAllRoots": "true", "strikes": "false"})
            expirations = _as_list((exp_json or {}).get("expirations", {}).get("date"))
        except Exception as e:  # noqa: BLE001
            log.debug("Tradier expirations failed for %s: %s", t, e)
            continue

        wanted: list[tuple[str, "date"]] = []
        for exp_str in expirations:
            try:
                exp = datetime.strptime(str(exp_str), "%Y-%m-%d").date()
            except ValueError:
                continue
            if cfg.DTE_MIN <= (exp - today).days <= cfg.DTE_MAX:
                wanted.append((str(exp_str), exp))
        if not wanted:
            continue

        got_any = False
        for exp_str, exp in wanted:
            try:
                chain_json = _get("/markets/options/chains",
                                  {"symbol": t, "expiration": exp_str, "greeks": "true"})
                options = _as_list((chain_json or {}).get("options", {}).get("option"))
            except Exception as e:  # noqa: BLE001
                log.debug("Tradier chain failed for %s %s: %s", t, exp_str, e)
                continue
            for o in options:
                opt_type = str(o.get("option_type", "")).lower()
                if opt_type not in ("call", "put"):
                    continue
                last = _to_float(o.get("last"))
                vol = _to_float(o.get("volume"))
                oi = _to_float(o.get("open_interest"))
                if last <= 0 or vol <= 0 or oi <= 0:
                    continue
                iv = _to_float((o.get("greeks") or {}).get("mid_iv")) or None
                out.append(OptionContract(
                    underlying=t,
                    symbol=str(o.get("symbol", "") or ""),
                    type=opt_type,
                    strike=_to_float(o.get("strike")),
                    expiry=exp,
                    dte=(exp - today).days,
                    spot=spot,
                    contract_price=last,
                    bid=_to_float(o.get("bid")),
                    ask=_to_float(o.get("ask")),
                    volume=vol,
                    open_interest=oi,
                    implied_volatility=iv,
                ))
                got_any = True
        if got_any:
            ok_tickers += 1

    log.info("Tradier options: %d contracts across %d/%d tickers",
             len(out), ok_tickers, len(tickers))
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
    Run the full scan: fetch (live source) -> filter -> score -> aggregate ->
    rank. Writes the full ranked signal list to data/flow_cache.json and returns
    the top-N FlowSignal objects.

    Provider priority: under "auto", Tradier if a token is configured, else
    yfinance. If the selected source produces nothing, the scan returns zero
    signals (no stub fallback) so no new positions open on placeholder data.
    """
    raw: list[OptionContract] = []
    source_used = "none"

    if cfg.source in ("tradier", "auto") and secrets.tradier_access_token:
        raw = fetch_contracts_tradier(tickers, spot_prices, cfg, secrets.tradier_access_token)
        if raw:
            source_used = "tradier"

    if not raw and cfg.source in ("yfinance", "auto"):
        raw = fetch_contracts_yfinance(tickers, spot_prices, cfg)
        if raw:
            source_used = "yfinance"

    log.info("Flow signal source this run: [bold]%s[/bold]", source_used)
    if source_used == "none":
        log.warning(
            "[bold yellow]No live options-flow data this run[/bold yellow] — every "
            "live source returned nothing. Yielding ZERO flow signals; no new "
            "positions will be opened from flow (existing ones are still managed)."
        )

    return rank_contracts(raw, cfg, paths)


def rank_contracts(
    raw: list[OptionContract], cfg: FlowConfig, paths: Paths,
) -> list[FlowSignal]:
    """
    Provider-agnostic pipeline: enrich -> filter -> score -> aggregate to ticker
    signals -> write the full ranked cache -> return the top-N. Takes an already
    fetched contract list so it is independent of which adapter produced it.
    """
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
