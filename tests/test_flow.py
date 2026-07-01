"""
Options-flow scanner tests. The scoring/aggregation pipeline is exercised
deterministically by loading a fixture chain with the offline CSV loader and
feeding it through rank_contracts (the same pipeline live runs use after
fetching from Tradier/yfinance).
"""
import dataclasses
import datetime

from config import get_config
from src.flow import (
    parse_occ_symbol, rank_contracts, fetch_contracts_csv,
    _aggression, _composite_score, OptionContract,
)
from tests.conftest import write_contracts_csv


def test_parse_occ_symbol():
    parsed = parse_occ_symbol("NVDA260717C00175000")
    assert parsed is not None
    root, opt_type, strike, expiry = parsed
    assert root == "NVDA"
    assert opt_type == "call"
    assert strike == 175.0
    assert expiry == datetime.date(2026, 7, 17)
    # puts and bad input
    assert parse_occ_symbol("AMD260821P00145000")[1] == "put"
    assert parse_occ_symbol("not-an-option") is None


def test_aggression_proxy():
    # last at upper part of spread -> aggressive buy (>0.6)
    assert _aggression(6.55, 6.40, 6.60) == 0.75
    # mid-spread -> 0.5 (not aggressive)
    assert _aggression(6.50, 6.40, 6.60) == 0.5
    # zero/invalid spread -> None
    assert _aggression(6.50, 6.50, 6.50) is None
    assert _aggression(6.50, 0.0, 6.60) is None


def test_scan_directions(tmp_path):
    """Bullish/bearish classification + noise filtering under default thresholds."""
    cfg = get_config()
    today = datetime.date.today()
    csv_path = tmp_path / "contracts.csv"
    spots = write_contracts_csv(str(csv_path), today)

    paths = dataclasses.replace(
        cfg.paths, flow_cache_json=str(tmp_path / "flow_cache.json"),
    )

    raw = fetch_contracts_csv(str(csv_path), spots)
    signals = rank_contracts(raw, cfg.flow, paths)
    direction = {s.ticker: s.direction for s in signals}

    assert direction.get("NVDA") == "bullish"
    assert direction.get("AMD") == "bullish"
    assert direction.get("MSTR") == "bearish"
    # noise dropped by filters
    assert "GME" not in direction   # vol/OI too low
    assert "AAPL" not in direction  # contract volume too low

    # cache file written for inspection
    assert (tmp_path / "flow_cache.json").exists()


def test_scan_respects_top_n(tmp_path):
    cfg = get_config()
    today = datetime.date.today()
    csv_path = tmp_path / "contracts.csv"
    spots = write_contracts_csv(str(csv_path), today)
    flow_cfg = dataclasses.replace(cfg.flow, TOP_N_SIGNALS=1)
    paths = dataclasses.replace(cfg.paths, flow_cache_json=str(tmp_path / "fc.json"))
    raw = fetch_contracts_csv(str(csv_path), spots)
    signals = rank_contracts(raw, flow_cfg, paths)
    assert len(signals) == 1


def test_composite_score_bounds():
    cfg = get_config().flow
    c = OptionContract(
        underlying="X", symbol="X", type="call", strike=100,
        expiry=datetime.date.today() + datetime.timedelta(days=10), dte=10,
        spot=100, contract_price=5.0, bid=4.9, ask=5.1,
        volume=100000, open_interest=1000,
    )
    c.vol_oi_ratio = 100.0   # far above cap
    c.notional = 10_000_000  # far above cap
    c.aggression = 1.0
    score = _composite_score(c, cfg)
    assert 0.0 <= score <= 100.0
