"""AI-managed exit guardrail tests (pure resolve_exit / resolve_all, offline)."""
from config import get_config
from src.decision import ExitAction
from src.exits import PositionSnapshot, resolve_exit, resolve_all

CFG = get_config()
RISK = CFG.risk
EXITS = CFG.exits


def _pos(**kw) -> PositionSnapshot:
    base = dict(
        ticker="NVDA", qty=10, entry=100.0, current_price=110.0,
        current_stop=92.0, current_target=120.0, age_days=5,
    )
    base.update(kw)
    return PositionSnapshot(**base)


def _resolve(pos, action, new_stop=None, new_target=None, conf=0.9):
    return resolve_exit(pos, action, new_stop, new_target, conf, "r", RISK, EXITS)


def test_stop_is_one_way_ratchet():
    # proposing a LOWER (looser) stop than current is rejected -> keep current stop
    r = _resolve(_pos(current_stop=95.0), "TIGHTEN_STOP", new_stop=90.0)
    assert r.new_stop == 95.0
    assert "rejected stop loosen" in r.note
    # a higher stop (still below price) is accepted
    r2 = _resolve(_pos(current_stop=95.0), "TIGHTEN_STOP", new_stop=104.0)
    assert r2.new_stop == 104.0
    assert r2.note == ""


def test_stop_cannot_cross_current_price():
    # stop >= current price is nonsensical for a long -> rejected
    r = _resolve(_pos(current_price=110.0, current_stop=95.0), "TIGHTEN_STOP", new_stop=111.0)
    assert r.new_stop == 95.0
    assert "rejected stop >= price" in r.note


def test_move_to_breakeven_only_when_in_profit():
    # in profit: stop moves to entry
    r = _resolve(_pos(entry=100.0, current_price=110.0, current_stop=95.0), "MOVE_TO_BREAKEVEN")
    assert r.new_stop == 100.0
    # underwater: entry >= price -> cannot; downgraded to HOLD, stop unchanged
    r2 = _resolve(_pos(entry=100.0, current_price=98.0, current_stop=95.0), "MOVE_TO_BREAKEVEN")
    assert r2.action == "HOLD"
    assert r2.new_stop == 95.0


def test_raise_target_only_upward_and_clear_of_price():
    # must be higher than the existing target
    r = _resolve(_pos(current_target=120.0), "RAISE_TARGET", new_target=118.0)
    assert r.new_target == 120.0
    assert "not higher" in r.note
    # valid raise
    r2 = _resolve(_pos(current_price=110.0, current_target=120.0), "RAISE_TARGET", new_target=130.0)
    assert r2.new_target == 130.0
    assert r2.note == ""


def test_hard_max_loss_floor_when_protection_missing():
    # no live stop -> baseline is the hard max-loss floor (entry*(1+hard_max_loss_pct))
    p = _pos(entry=100.0, current_stop=None, current_target=None)
    r = _resolve(p, "HOLD")
    assert r.new_stop == round(100.0 * (1 + EXITS.hard_max_loss_pct), 2)  # 92.0
    assert r.changed is True  # must (re)establish protection


def test_take_partial_leaves_shares_and_reprotects():
    r = _resolve(_pos(qty=10), "TAKE_PARTIAL")
    assert r.sell_qty == int(10 * EXITS.partial_exit_fraction)
    assert r.remaining_qty == 10 - r.sell_qty
    assert r.remaining_qty >= 1
    # a single-share position can't be partialled
    r1 = _resolve(_pos(qty=1), "TAKE_PARTIAL")
    assert r1.action == "HOLD"
    assert r1.sell_qty == 0


def test_same_day_full_exit_needs_high_confidence():
    thresh = EXITS.same_day_full_exit_min_confidence
    # same-day (age 0), low confidence -> blocked, downgraded to HOLD, still protected
    r = _resolve(_pos(age_days=0, qty=10), "TAKE_FULL", conf=thresh - 0.1)
    assert r.action == "HOLD"
    assert r.sell_qty == 0
    # same-day, high confidence -> allowed
    r2 = _resolve(_pos(age_days=0, qty=10), "TAKE_FULL", conf=thresh + 0.01)
    assert r2.action == "TAKE_FULL"
    assert r2.sell_qty == 10
    # older position -> full exit allowed even at lower confidence
    r3 = _resolve(_pos(age_days=3, qty=10), "TAKE_FULL", conf=0.5)
    assert r3.sell_qty == 10


def test_resolve_all_defaults_missing_position_to_hold():
    positions = [_pos(ticker="NVDA"), _pos(ticker="AMD")]
    actions = [ExitAction("NVDA", "TIGHTEN_STOP", 105.0, None, 0.9, "r")]
    res = resolve_all(positions, actions, RISK, EXITS)
    by = {r.ticker: r for r in res}
    assert by["NVDA"].action == "TIGHTEN_STOP"
    assert by["AMD"].action == "HOLD"          # no action returned -> HOLD (still protected)
