"""Risk sizing + limit-check tests (pure functions, offline)."""
from config import RiskConfig
from src.analytics import PortfolioView, PositionView
from src.decision import Decision
from src.risk import size_and_validate, daily_loss_halt_triggered


def _empty_portfolio(equity: float = 10_000.0) -> PortfolioView:
    return PortfolioView(
        equity=equity, cash=equity, invested=0.0, cash_pct=1.0,
        invested_pct=0.0, total_unrealized_pl=0.0, largest_position_weight=0.0,
        num_positions=0, positions=[],
    )


def _buy(ticker: str, weight: float) -> Decision:
    return Decision(
        action="buy", ticker=ticker, side="long", proposed_weight=weight,
        stop_pct=-0.08, target_pct=0.20, confidence=0.8, rationale="test",
    )


def test_basic_sizing():
    risk = RiskConfig()
    pf = _empty_portfolio(10_000)
    prices = {"AAPL": 100.0}
    accepted, rejected = size_and_validate(
        [_buy("AAPL", 0.10)], pf, prices, buying_power=40_000, risk=risk, halt=False
    )
    assert len(accepted) == 1 and not rejected
    o = accepted[0]
    # 10% of 10k = $1000 / $100 = 10 shares
    assert o.qty == 10
    assert round(o.stop_price, 2) == 92.0    # -8%
    assert round(o.target_price, 2) == 120.0  # +20%


def test_position_size_cap():
    """Proposed weight above max_position_pct is capped by the code, not the model."""
    risk = RiskConfig()  # max_position_pct = 0.15
    pf = _empty_portfolio(10_000)
    accepted, _ = size_and_validate(
        [_buy("AAPL", 0.50)], pf, {"AAPL": 100.0}, 40_000, risk, halt=False
    )
    # capped at 15% -> $1500 -> 15 shares
    assert accepted[0].qty == 15


def test_halt_blocks_buys():
    risk = RiskConfig()
    pf = _empty_portfolio(10_000)
    accepted, rejected = size_and_validate(
        [_buy("AAPL", 0.10)], pf, {"AAPL": 100.0}, 40_000, risk, halt=True
    )
    assert not accepted
    assert rejected and "halt" in rejected[0].reason.lower()


def test_max_concurrent_positions():
    risk = RiskConfig(max_concurrent_positions=2)
    # portfolio already holds 2 names
    positions = [
        PositionView("AAA", 1, "long", 10, 10, 10, 10, 0, 0, 0.001, 9.2, 12, 0.08, 0.2),
        PositionView("BBB", 1, "long", 10, 10, 10, 10, 0, 0, 0.001, 9.2, 12, 0.08, 0.2),
    ]
    pf = PortfolioView(
        equity=10_000, cash=9_980, invested=20, cash_pct=0.998, invested_pct=0.002,
        total_unrealized_pl=0.0, largest_position_weight=0.001, num_positions=2,
        positions=positions,
    )
    accepted, rejected = size_and_validate(
        [_buy("CCC", 0.10)], pf, {"CCC": 100.0}, 40_000, risk, halt=False
    )
    assert not accepted
    assert rejected and "concurrent" in rejected[0].reason.lower()


def test_insufficient_buying_power():
    risk = RiskConfig()
    pf = _empty_portfolio(10_000)
    accepted, rejected = size_and_validate(
        [_buy("AAPL", 0.10)], pf, {"AAPL": 100.0}, buying_power=50.0, risk=risk, halt=False
    )
    # $50 BP < $100 minimum order notional -> rejected
    assert not accepted and rejected


def test_sell_exempt_from_halt():
    """A sell to close should still be allowed even when buys are halted."""
    risk = RiskConfig()
    positions = [
        PositionView("AAPL", 10, "long", 100, 110, 1100, 1000, 100, 0.10, 0.11,
                     92, 120, 0.16, 0.09),
    ]
    pf = PortfolioView(
        equity=10_000, cash=8_900, invested=1100, cash_pct=0.89, invested_pct=0.11,
        total_unrealized_pl=100, largest_position_weight=0.11, num_positions=1,
        positions=positions,
    )
    sell = Decision("sell", "AAPL", "long", 0.0, -0.08, 0.20, 0.9, "exit")
    accepted, _ = size_and_validate([sell], pf, {"AAPL": 110.0}, 40_000, risk, halt=True)
    assert len(accepted) == 1
    assert accepted[0].action == "sell" and accepted[0].qty == 10


def test_daily_loss_halt_trigger():
    risk = RiskConfig()  # max_daily_loss_pct = -0.05
    assert daily_loss_halt_triggered(-0.06, risk) is True
    assert daily_loss_halt_triggered(-0.04, risk) is False
