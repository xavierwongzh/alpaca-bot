"""Midday conservative-policy tests (code-enforced, offline)."""
from config import MiddayConfig
from src.decision import Decision, _is_reasoning_model, _build_request_params
from config import ModelConfig
from src.risk import apply_midday_filter


MID = MiddayConfig()  # defaults: hold>=1d, buy conf>=0.72, composite>=60, sell conf>=0.75, max 2 new


def _buy(ticker, conf):
    return Decision("buy", ticker, "long", 0.10, -0.08, 0.20, conf, "r")


def _sell(ticker, conf):
    return Decision("sell", ticker, "long", 0.0, -0.08, 0.20, conf, "r")


def _flow(ticker, composite):
    return {ticker: {"ticker": ticker, "composite_score": composite, "direction": "bullish"}}


def test_no_same_day_reversal():
    """A sell of a position opened today (age 0 < MIN_HOLD_DAYS) is dropped."""
    kept, dropped = apply_midday_filter(
        [_sell("AAPL", 0.99)],
        position_age_days={"AAPL": 0},
        flow_by_ticker={},
        held_tickers={"AAPL"},
        midday=MID,
    )
    assert not kept
    assert dropped and "too young" in dropped[0].reason


def test_old_position_sell_allowed_if_confident():
    kept, dropped = apply_midday_filter(
        [_sell("AAPL", 0.80)],
        position_age_days={"AAPL": 5},
        flow_by_ticker={},
        held_tickers={"AAPL"},
        midday=MID,
    )
    assert len(kept) == 1 and kept[0].action == "sell"
    assert not dropped


def test_low_confidence_sell_dropped():
    kept, dropped = apply_midday_filter(
        [_sell("AAPL", 0.50)],
        position_age_days={"AAPL": 5},
        flow_by_ticker={},
        held_tickers={"AAPL"},
        midday=MID,
    )
    assert not kept and dropped


def test_buy_below_confidence_dropped():
    kept, dropped = apply_midday_filter(
        [_buy("NVDA", 0.60)],
        position_age_days={},
        flow_by_ticker=_flow("NVDA", 90),
        held_tickers=set(),
        midday=MID,
    )
    assert not kept
    assert dropped and "confidence" in dropped[0].reason


def test_buy_below_composite_dropped():
    kept, dropped = apply_midday_filter(
        [_buy("NVDA", 0.90)],
        position_age_days={},
        flow_by_ticker=_flow("NVDA", 40),  # below MIN_COMPOSITE 60
        held_tickers=set(),
        midday=MID,
    )
    assert not kept
    assert dropped and "composite" in dropped[0].reason


def test_buy_without_flow_dropped():
    """Midday entries require a flow signal; a buy with no flow composite is dropped."""
    kept, dropped = apply_midday_filter(
        [_buy("NVDA", 0.95)],
        position_age_days={},
        flow_by_ticker={},  # no flow
        held_tickers=set(),
        midday=MID,
    )
    assert not kept and dropped


def test_strong_buy_accepted():
    kept, dropped = apply_midday_filter(
        [_buy("NVDA", 0.85)],
        position_age_days={},
        flow_by_ticker=_flow("NVDA", 80),
        held_tickers=set(),
        midday=MID,
    )
    assert len(kept) == 1 and kept[0].ticker == "NVDA"
    assert not dropped


def test_cap_new_positions():
    """At most MAX_NEW_POSITIONS new names; highest-confidence kept."""
    flow = {}
    for t in ("AAA", "BBB", "CCC"):
        flow.update(_flow(t, 90))
    decisions = [_buy("AAA", 0.80), _buy("BBB", 0.95), _buy("CCC", 0.90)]
    kept, dropped = apply_midday_filter(
        decisions, position_age_days={}, flow_by_ticker=flow,
        held_tickers=set(), midday=MID,
    )
    kept_tickers = {d.ticker for d in kept}
    assert len(kept) == MID.MAX_NEW_POSITIONS  # 2
    assert kept_tickers == {"BBB", "CCC"}      # AAA (lowest conf) dropped
    assert any(d.ticker == "AAA" for d in dropped)


def test_hold_passes_through():
    hold = Decision("hold", "AAPL", "long", 0.0, -0.08, 0.20, 0.5, "keep")
    kept, dropped = apply_midday_filter(
        [hold], position_age_days={"AAPL": 0}, flow_by_ticker={},
        held_tickers={"AAPL"}, midday=MID,
    )
    assert len(kept) == 1 and kept[0].action == "hold"


# --- reasoning-model param handling ---

def test_is_reasoning_model():
    assert _is_reasoning_model("gpt-5.5")
    assert _is_reasoning_model("gpt-5")
    assert _is_reasoning_model("o3-mini")
    assert not _is_reasoning_model("gpt-4o")
    assert not _is_reasoning_model("gpt-4o-mini")


def test_request_params_drop_temperature_for_reasoning():
    cfg = ModelConfig(decision_model="gpt-5.5", reasoning_effort="low")
    params = _build_request_params(cfg, messages=[{"role": "user", "content": "x"}])
    assert "temperature" not in params
    assert params["reasoning_effort"] == "low"
    assert params["response_format"]["type"] == "json_schema"


def test_request_params_keep_temperature_for_gpt4o():
    cfg = ModelConfig(decision_model="gpt-4o", temperature=0.2)
    params = _build_request_params(cfg, messages=[{"role": "user", "content": "x"}])
    assert params["temperature"] == 0.2
    assert "reasoning_effort" not in params
