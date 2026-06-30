"""Shared pytest fixtures/helpers. All tests run offline (no network, no keys)."""
import csv
import datetime
import os
import sys

import pytest

# Make the project root importable when pytest runs from anywhere.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import Secrets  # noqa: E402

# A throwaway paper-mode Secrets object; no real keys, never hits the network in
# the code paths exercised by these tests.
DUMMY_SECRETS = Secrets(
    alpaca_api_key="TESTKEY",
    alpaca_secret_key="TESTSECRET",
    openai_api_key="TESTOPENAI",
    alpaca_base_url="https://paper-api.alpaca.markets",
)


@pytest.fixture
def dummy_secrets() -> Secrets:
    return DUMMY_SECRETS


def write_contracts_csv(path: str, today: datetime.date) -> dict[str, float]:
    """
    Write a deterministic per-contract CSV with expiries anchored to `today`
    (so DTE filters stay valid no matter when CI runs). Returns the spot map.

    Designed so that, under default thresholds:
      - NVDA: aggressive call buying -> bullish
      - AMD:  aggressive call buying -> bullish
      - MSTR: aggressive put buying  -> bearish
      - GME:  vol/OI too low         -> filtered out
      - AAPL: contract volume too low -> filtered out
    """
    exp_near = (today + datetime.timedelta(days=20)).isoformat()
    spots = {"NVDA": 170.0, "AMD": 140.0, "MSTR": 380.0, "GME": 28.0, "AAPL": 205.0}
    rows = [
        # underlying, option_symbol, type, strike, expiry, spot, price, bid, ask, vol, oi, iv
        ["NVDA", "NVDA_C", "call", 175, exp_near, 170.0, 6.55, 6.40, 6.60, 8000, 2000, 0.52],
        ["AMD", "AMD_C", "call", 145, exp_near, 140.0, 3.20, 3.10, 3.25, 6000, 1500, 0.48],
        ["MSTR", "MSTR_P", "put", 360, exp_near, 380.0, 12.10, 11.80, 12.20, 5000, 1000, 0.70],
        # GME: volume 900 >= 500 but vol/OI = 0.225 < 2.0 -> filtered
        ["GME", "GME_C", "call", 30, exp_near, 28.0, 0.90, 0.85, 0.95, 900, 4000, 0.85],
        # AAPL: volume 300 < 500 -> filtered
        ["AAPL", "AAPL_C", "call", 210, exp_near, 205.0, 4.20, 4.10, 4.30, 300, 5000, 0.30],
    ]
    header = [
        "underlying", "option_symbol", "type", "strike", "expiry", "spot",
        "contract_price", "bid", "ask", "volume", "open_interest", "implied_volatility",
    ]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)
    return spots
