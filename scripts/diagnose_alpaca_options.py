"""
Read-only diagnostic: what can THIS Alpaca account actually pull for options?

Places NO orders. Probes each options capability for two liquid underlyings
(NVDA, AAPL) and prints, per probe, PASS with a small data sample or FAIL with
the exact status code + message. Ends with a verdict on whether Alpaca is usable
as an options-flow data provider (fully / partially / no).

Method + request-class names are the ones verified present in the installed
alpaca-py (0.43.x):
  TradingClient.get_option_contracts(GetOptionContractsRequest)
  OptionHistoricalDataClient.get_option_chain(OptionChainRequest(feed=...))
  OptionHistoricalDataClient.get_option_latest_quote/_latest_trade/_bars

Usage:  python scripts/diagnose_alpaca_options.py
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

# Make the project root importable when run from anywhere.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import Secrets  # noqa: E402

from alpaca.common.exceptions import APIError
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import GetOptionContractsRequest
from alpaca.trading.enums import AssetStatus
from alpaca.data.historical.option import OptionHistoricalDataClient
from alpaca.data.historical.stock import StockHistoricalDataClient
from alpaca.data.requests import (
    OptionChainRequest,
    OptionLatestQuoteRequest,
    OptionLatestTradeRequest,
    OptionBarsRequest,
    StockLatestTradeRequest,
)
from alpaca.data.timeframe import TimeFrame
from alpaca.data.enums import OptionsFeed

UNDERLYINGS = ("NVDA", "AAPL")


# ---------------------------------------------------------------------------
# Small PASS/FAIL harness
# ---------------------------------------------------------------------------
class Probe:
    """Runs a callable, records PASS (+sample) or FAIL (+status/message)."""

    def __init__(self, name: str):
        self.name = name
        self.ok = False
        self.detail = ""

    def run(self, fn) -> "Probe":
        try:
            self.detail = fn() or ""
            self.ok = True
        except APIError as e:
            code = getattr(e, "status_code", "?")
            self.ok = False
            self.detail = f"APIError status={code}: {e}"
        except Exception as e:  # noqa: BLE001
            self.ok = False
            self.detail = f"{type(e).__name__}: {e}"
        tag = "\033[92mPASS\033[0m" if self.ok else "\033[91mFAIL\033[0m"
        print(f"  [{tag}] {self.name}")
        if self.detail:
            for line in str(self.detail).splitlines():
                print(f"         {line}")
        return self


def _g(obj, attr, default=None):
    return getattr(obj, attr, default)


# ---------------------------------------------------------------------------
# Per-underlying probes
# ---------------------------------------------------------------------------
def diagnose(
    symbol: str,
    trading: TradingClient,
    opt_data: OptionHistoricalDataClient,
    stock_data: StockHistoricalDataClient,
    caps: dict,
) -> None:
    print(f"\n{'=' * 62}\n {symbol}\n{'=' * 62}")

    # Spot (to pick a near-the-money contract below).
    spot = 0.0
    try:
        lt = stock_data.get_stock_latest_trade(
            StockLatestTradeRequest(symbol_or_symbols=symbol)
        )
        spot = float(lt[symbol].price)
        print(f"  spot (latest trade): ${spot:,.2f}")
    except Exception as e:  # noqa: BLE001
        print(f"  spot lookup failed ({e}); NTM selection will use median strike.")

    ntm_symbol: str | None = None

    # --- Probe 1: option contracts (Trading API) ---
    def p1() -> str:
        req = GetOptionContractsRequest(
            underlying_symbols=[symbol], status=AssetStatus.ACTIVE, limit=200
        )
        resp = trading.get_option_contracts(req)
        contracts = list(_g(resp, "option_contracts", []) or [])
        if not contracts:
            raise RuntimeError("no contracts returned")
        oi_pop = sum(1 for c in contracts if _g(c, "open_interest") not in (None, ""))
        cp_pop = sum(1 for c in contracts if _g(c, "close_price") not in (None, ""))
        caps["contracts"] = True
        caps["open_interest"] = caps["open_interest"] or oi_pop > 0

        # near-the-money pick for later probes
        nonlocal ntm_symbol
        def strike(c):
            return float(_g(c, "strike_price", 0) or 0)
        ref = spot if spot > 0 else strike(sorted(contracts, key=strike)[len(contracts) // 2])
        ntm = min(contracts, key=lambda c: abs(strike(c) - ref))
        ntm_symbol = _g(ntm, "symbol")

        sample = contracts[0]
        return (
            f"{len(contracts)} contracts; open_interest populated on {oi_pop}, "
            f"close_price on {cp_pop}\n"
            f"sample: {_g(sample,'symbol')} strike={_g(sample,'strike_price')} "
            f"type={_g(sample,'type')} OI={_g(sample,'open_interest')} "
            f"close={_g(sample,'close_price')}\n"
            f"near-the-money pick: {ntm_symbol} (strike {_g(ntm,'strike_price')})"
        )

    Probe("1. Option contracts (Trading API)").run(p1)

    # --- Probe 2: option chain snapshot, INDICATIVE feed ---
    def chain_probe(feed: OptionsFeed) -> str:
        resp = opt_data.get_option_chain(
            OptionChainRequest(underlying_symbol=symbol, feed=feed)
        )
        items = list(resp.items())
        if not items:
            raise RuntimeError("chain returned 0 snapshots")
        with_quote = sum(1 for _, s in items if _g(s, "latest_quote"))
        with_trade = sum(1 for _, s in items if _g(s, "latest_trade"))
        with_greeks = sum(1 for _, s in items if _g(s, "greeks"))
        with_iv = sum(1 for _, s in items if _g(s, "implied_volatility") is not None)
        # sample the first snapshot that has a quote
        sym0, snap0 = next(((k, v) for k, v in items if _g(v, "latest_quote")), items[0])
        q = _g(snap0, "latest_quote")
        gk = _g(snap0, "greeks")
        bid = _g(q, "bid_price") if q else None
        ask = _g(q, "ask_price") if q else None
        delta = _g(gk, "delta") if gk else None
        return (
            f"{len(items)} snapshots; quote on {with_quote}, trade on {with_trade}, "
            f"greeks on {with_greeks}, IV on {with_iv}\n"
            f"sample {sym0}: bid={bid} ask={ask} delta={delta} "
            f"IV={_g(snap0,'implied_volatility')}"
        )

    def p2() -> str:
        detail = chain_probe(OptionsFeed.INDICATIVE)
        caps["chain_indicative"] = True
        # If quotes+greeks+IV are all present on the indicative feed, that's the
        # combination the flow scanner needs.
        if "greeks on 0" not in detail and "IV on 0" not in detail:
            caps["chain_full_indicative"] = True
        return detail

    Probe("2. Option chain snapshot - feed=INDICATIVE (free)").run(p2)

    # --- Probe 3: same chain, OPRA feed (expected to be gated on free tier) ---
    def p3() -> str:
        detail = chain_probe(OptionsFeed.OPRA)
        caps["chain_opra"] = True
        return detail

    Probe("3. Option chain snapshot - feed=OPRA (real-time, paid)").run(p3)

    # --- Probe 4: latest quote + latest trade for the NTM contract ---
    def p4() -> str:
        if not ntm_symbol:
            raise RuntimeError("no near-the-money contract from probe 1")
        q = opt_data.get_option_latest_quote(
            OptionLatestQuoteRequest(symbol_or_symbols=ntm_symbol, feed=OptionsFeed.INDICATIVE)
        )
        t = opt_data.get_option_latest_trade(
            OptionLatestTradeRequest(symbol_or_symbols=ntm_symbol, feed=OptionsFeed.INDICATIVE)
        )
        qq, tt = q.get(ntm_symbol), t.get(ntm_symbol)
        return (
            f"{ntm_symbol}: quote bid={_g(qq,'bid_price')} ask={_g(qq,'ask_price')}; "
            f"trade price={_g(tt,'price')} size={_g(tt,'size')}"
        )

    Probe("4. Latest quote + trade (NTM, INDICATIVE)").run(p4)

    # --- Probe 5: recent historical bars for the NTM contract ---
    def p5() -> str:
        if not ntm_symbol:
            raise RuntimeError("no near-the-money contract from probe 1")
        start = datetime.now(timezone.utc) - timedelta(days=10)
        bars = opt_data.get_option_bars(
            OptionBarsRequest(
                symbol_or_symbols=ntm_symbol, timeframe=TimeFrame.Day, start=start
            )
        )
        data = _g(bars, "data", {}) or {}
        rows = data.get(ntm_symbol, [])
        if not rows:
            raise RuntimeError("no historical bars returned")
        caps["historical"] = True
        last = rows[-1]
        return (f"{len(rows)} daily bars; last close={_g(last,'close')} "
                f"volume={_g(last,'volume')}")

    Probe("5. Recent historical option bars (NTM)").run(p5)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> int:
    secrets = Secrets.from_env()
    if not secrets.alpaca_api_key or not secrets.alpaca_secret_key:
        print("Missing Alpaca credentials (ALPACA_API_KEY / ALPACA_SECRET_KEY).")
        return 1

    print("Alpaca options data diagnostic - READ ONLY, places no orders.")
    print(f"Underlyings: {', '.join(UNDERLYINGS)}")

    trading = TradingClient(secrets.alpaca_api_key, secrets.alpaca_secret_key, paper=True)
    opt_data = OptionHistoricalDataClient(secrets.alpaca_api_key, secrets.alpaca_secret_key)
    stock_data = StockHistoricalDataClient(secrets.alpaca_api_key, secrets.alpaca_secret_key)

    caps = {
        "contracts": False,
        "open_interest": False,
        "chain_indicative": False,
        "chain_full_indicative": False,   # quotes + greeks + IV on indicative
        "chain_opra": False,
        "historical": False,
    }

    for sym in UNDERLYINGS:
        try:
            diagnose(sym, trading, opt_data, stock_data, caps)
        except Exception as e:  # noqa: BLE001
            print(f"  fatal error diagnosing {sym}: {e}")

    # --- verdict ---
    print(f"\n{'=' * 62}\n VERDICT\n{'=' * 62}")

    def yn(b: bool) -> str:
        return "\033[92mYES\033[0m" if b else "\033[91mNO\033[0m"

    print(f"  Contract metadata + open interest ....... {yn(caps['contracts'] and caps['open_interest'])}")
    print(f"  Live chain (bid/ask + greeks + IV, free)  {yn(caps['chain_full_indicative'])}")
    print(f"  Real-time OPRA data ..................... {yn(caps['chain_opra'])}")
    print(f"  Historical option bars .................. {yn(caps['historical'])}")

    if caps["chain_full_indicative"] and caps["contracts"] and caps["open_interest"]:
        usable = "FULLY"
        note = ("Alpaca can serve usable chains (open interest + quotes + greeks + IV) "
                "on the free indicative feed. We can add an Alpaca adapter AHEAD of "
                "yfinance in the section A provider chain - no extra signup needed.")
    elif caps["contracts"] or caps["chain_indicative"] or caps["historical"]:
        usable = "PARTIALLY"
        note = ("Some options data is available but not the full quotes+greeks+IV chain "
                "the scanner wants on the free feed. Keep Tradier/yfinance as the flow "
                "provider; Alpaca can still supplement (e.g. contract metadata / OI).")
    else:
        usable = "NO"
        note = ("The account cannot pull usable options data. Stay on Tradier/yfinance "
                "for flow signals.")

    print(f"\n  Alpaca as an options-flow provider: {usable}")
    print(f"  {note}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
