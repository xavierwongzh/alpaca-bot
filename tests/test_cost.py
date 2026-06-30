"""Cost accounting + decision-record tests (offline)."""
import json

from config import CostConfig
from src.cost import extract_usage, write_cost_log, Usage
from src.execution import ExecutionResult
from src.records import build_decision_records, append_decision_records
from src.risk import SizedOrder


class _Details:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class _Usage:
    def __init__(self, prompt, completion, cached=0, reasoning=0):
        self.prompt_tokens = prompt
        self.completion_tokens = completion
        self.prompt_tokens_details = _Details(cached_tokens=cached)
        self.completion_tokens_details = _Details(reasoning_tokens=reasoning)


class _Resp:
    def __init__(self, usage):
        self.usage = usage


def test_extract_and_price_decision():
    cfg = CostConfig()  # decision: $5 in / $0.50 cached / $30 out per 1M
    resp = _Resp(_Usage(prompt=10_000, completion=2_000, cached=4_000, reasoning=1_500))
    u = extract_usage(resp, "decision", "gpt-5.5", cfg)
    assert u.input_tokens == 10_000
    assert u.cached_input_tokens == 4_000
    assert u.output_tokens == 2_000
    assert u.reasoning_tokens == 1_500
    # cost = (10000-4000)/1e6*5 + 4000/1e6*0.5 + 2000/1e6*30
    #      = 0.030 + 0.002 + 0.060 = 0.092
    assert round(u.cost_usd, 6) == 0.092


def test_summary_pricing_uses_summary_rates():
    cfg = CostConfig()
    resp = _Resp(_Usage(prompt=1_000, completion=500))
    u = extract_usage(resp, "summary", "gpt-4o-mini", cfg)
    # 1000/1e6*0.15 + 500/1e6*0.60 = 0.00015 + 0.0003 = 0.00045
    assert round(u.cost_usd, 6) == 0.00045


def test_write_cost_log(tmp_path):
    path = tmp_path / "cost_log.csv"
    d = Usage("decision", "gpt-5.5", input_tokens=10_000, output_tokens=2_000, cost_usd=0.092)
    s = Usage("summary", "gpt-4o-mini", input_tokens=1_000, output_tokens=500, cost_usd=0.00045)
    row = write_cost_log(str(path), "open", d, s)
    assert round(row["total_cost_usd"], 5) == round(0.092 + 0.00045, 5)
    assert path.exists()
    content = path.read_text()
    assert "open" in content and "gpt-5.5" in content


def test_build_and_write_decision_records(tmp_path):
    so = [SizedOrder("NVDA", "buy", 4, 194.92, 179.3, 233.9, 779.7, "because flow", 0.8, "id-1")]
    er = [ExecutionResult("NVDA", "buy", 4, "placed", "alp-1", "...",
                          client_order_id="id-1", decision_id="id-1")]
    recs = build_decision_records(
        sized_orders=so, exec_results=er, mode="open", model="gpt-5.5",
        reasoning_effort="medium",
        technicals={"NVDA": {"last_price": 194.92, "rsi14": 40}},
        flow_by_ticker={"NVDA": {"composite_score": 70, "direction": "bullish"}},
        macro={"vix": 17.6, "regime": "normal"}, market_summary="ok",
    )
    assert len(recs) == 1
    r = recs[0]
    assert r["id"] == "id-1"
    assert r["client_order_id"] == "id-1"
    assert r["alpaca_order_id"] == "alp-1"
    assert r["flow_signal"]["composite_score"] == 70
    assert r["technicals"]["rsi14"] == 40

    path = tmp_path / "decisions.jsonl"
    append_decision_records(str(path), recs)
    lines = path.read_text().strip().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["ticker"] == "NVDA"
