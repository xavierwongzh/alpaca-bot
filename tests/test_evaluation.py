"""Evaluation engine + reconciliation tests (offline, deterministic)."""
import dataclasses
import datetime

from config import get_config
from src.evaluation import (
    compute_overall, compute_breakdowns, compute_calibration,
    confidence_bucket, max_drawdown,
)
from src.reconcile import reconcile_closed_trades, _exit_reason


def _t(ret, pnl, **tags):
    base = {"realized_return_pct": ret, "realized_pnl": pnl,
            "signal_type": "flow", "run_mode": "open", "confidence": 0.8, "sector": "Semiconductors"}
    base.update(tags)
    return base


def test_confidence_bucket():
    assert confidence_bucket(0.82) == "0.8-0.9"
    assert confidence_bucket(0.5) == "0.5-0.6"
    assert confidence_bucket(0.999) == "0.9-1.0"
    assert confidence_bucket(None) == "unknown"


def test_compute_overall():
    trades = [_t(0.20, 200), _t(0.10, 100), _t(-0.08, -80)]
    o = compute_overall(trades)
    assert o["trade_count"] == 3
    assert round(o["win_rate"], 3) == round(2 / 3, 3)
    assert round(o["avg_win"], 3) == 0.15
    assert round(o["avg_loss"], 3) == -0.08
    # profit factor = (200+100)/80 = 3.75
    assert round(o["profit_factor"], 2) == 3.75
    assert round(o["expectancy_per_trade"], 2) == round((200 + 100 - 80) / 3, 2)


def test_profit_factor_none_when_no_losses():
    o = compute_overall([_t(0.2, 200), _t(0.1, 100)])
    assert o["profit_factor"] is None


def test_max_drawdown():
    # 100 -> 120 -> 90 -> 110 : peak 120, trough 90 => -25%
    assert round(max_drawdown([100, 120, 90, 110]), 4) == -0.25
    assert max_drawdown([100, 101, 102]) == 0.0


def test_breakdowns_and_small_sample_flag():
    trades = [_t(0.2, 200, run_mode="open"), _t(-0.08, -80, run_mode="midday")]
    bd = compute_breakdowns(trades, min_sample=10)
    modes = {r["key"]: r for r in bd["run_mode"]}
    assert modes["open"]["count"] == 1
    assert modes["open"]["win_rate"] == 1.0
    assert modes["midday"]["win_rate"] == 0.0
    # both cells below min_sample=10 -> not meaningful
    assert all(not r["meaningful"] for r in bd["run_mode"])


def test_calibration():
    # three 0.8-bucket trades, 2 winners -> realized 0.667 vs midpoint 0.85
    trades = [_t(0.2, 200, confidence=0.82), _t(0.1, 100, confidence=0.88),
              _t(-0.08, -80, confidence=0.81)]
    cal = {c["bucket"]: c for c in compute_calibration(trades, min_sample=10)}
    b = cal["0.8-0.9"]
    assert b["count"] == 3
    assert round(b["win_rate"], 3) == round(2 / 3, 3)
    assert b["midpoint"] == 0.85
    assert not b["meaningful"]  # 3 < 10


def test_exit_reason_inference():
    assert _exit_reason("limit") == "target"
    assert _exit_reason("stop") == "stop"
    assert _exit_reason("stop_limit") == "stop"
    assert _exit_reason("market") == "manual"


# --- reconciliation FIFO ---

class _FakeBroker:
    def __init__(self, fills):
        self._fills = fills

    def get_fills(self, lookback_days=180):
        return self._fills


def test_reconcile_fifo_roundtrip(tmp_path):
    cfg = get_config()
    paths = dataclasses.replace(
        cfg.paths,
        closed_trades_jsonl=str(tmp_path / "closed.jsonl"),
        decisions_jsonl=str(tmp_path / "decisions.jsonl"),
    )
    t0 = datetime.datetime(2026, 6, 1, 14, 0, tzinfo=datetime.timezone.utc)
    t1 = datetime.datetime(2026, 6, 4, 15, 0, tzinfo=datetime.timezone.utc)
    fills = [
        {"symbol": "NVDA", "side": "buy", "qty": 10, "price": 100.0, "time": t0,
         "order_id": "o-entry", "client_order_id": "dec-1", "order_type": "market"},
        {"symbol": "NVDA", "side": "sell", "qty": 10, "price": 120.0, "time": t1,
         "order_id": "o-exit", "client_order_id": "auto", "order_type": "limit"},
    ]
    broker = _FakeBroker(fills)
    recs = reconcile_closed_trades(broker, paths)
    assert len(recs) == 1
    r = recs[0]
    assert r["ticker"] == "NVDA"
    assert round(r["realized_return_pct"], 4) == 0.20
    assert r["realized_pnl"] == 200.0
    assert r["exit_reason"] == "target"
    assert r["holding_days"] == 3  # Mon->Thu busdays

    # Idempotency: a re-run records nothing new.
    assert reconcile_closed_trades(broker, paths) == []
