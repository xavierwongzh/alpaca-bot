"""
Evaluation engine.

Turns closed_trades.jsonl + Alpaca portfolio history + SPY/QQQ benchmarks into
metrics that answer "is this working?": overall stats, per-tag breakdowns,
confidence calibration, and excess return vs buy-and-hold. Every cell carries a
sample size and is flagged when too small to trust.

The pure functions (compute_overall / compute_breakdowns / compute_calibration /
max_drawdown) take plain lists and are unit-tested offline. run_evaluation()
adds the Alpaca-dependent benchmark/equity work and writes the outputs.
"""
from __future__ import annotations

import bisect
import json
import os
from datetime import datetime, timezone
from typing import Any, Optional

import numpy as np

from config import Config, EvalConfig, Secrets
from src.logger import get_logger

log = get_logger()

BENCHMARK_NOTE = (
    "QQQ is the honest benchmark given the tech-tilted watchlist; SPY is shown too."
)


# ---------------------------------------------------------------------------
# Pure metric functions (offline-testable)
# ---------------------------------------------------------------------------
def load_closed_trades(path: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if not os.path.exists(path):
        return out
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def confidence_bucket(conf: Optional[float]) -> str:
    if conf is None:
        return "unknown"
    try:
        c = float(conf)
    except (TypeError, ValueError):
        return "unknown"
    c = max(0.0, min(0.999, c))
    low = int(c * 10) / 10
    return f"{low:.1f}-{low + 0.1:.1f}"


def compute_overall(trades: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(trades)
    if n == 0:
        return {"trade_count": 0}
    rets = [float(t.get("realized_return_pct", 0.0)) for t in trades]
    pnls = [float(t.get("realized_pnl", 0.0)) for t in trades]
    wins = [r for r in rets if r > 0]
    losses = [r for r in rets if r <= 0]
    gross_win = sum(p for p in pnls if p > 0)
    gross_loss = sum(p for p in pnls if p < 0)
    profit_factor = (gross_win / abs(gross_loss)) if gross_loss < 0 else None  # None == no losses yet
    return {
        "trade_count": n,
        "win_rate": len(wins) / n,
        "avg_win": (sum(wins) / len(wins)) if wins else 0.0,
        "avg_loss": (sum(losses) / len(losses)) if losses else 0.0,
        "profit_factor": profit_factor,
        "expectancy_per_trade": sum(pnls) / n,
        "total_realized_pnl": sum(pnls),
    }


def _cell(trades: list[dict[str, Any]], min_sample: int) -> dict[str, Any]:
    n = len(trades)
    rets = [float(t.get("realized_return_pct", 0.0)) for t in trades]
    pnls = [float(t.get("realized_pnl", 0.0)) for t in trades]
    wins = sum(1 for r in rets if r > 0)
    return {
        "count": n,
        "win_rate": (wins / n) if n else 0.0,
        "avg_return": (sum(rets) / n) if n else 0.0,
        "total_pnl": sum(pnls),
        "meaningful": n >= min_sample,
    }


def compute_breakdowns(trades: list[dict[str, Any]], min_sample: int) -> dict[str, list[dict]]:
    """Per-tag breakdowns for signal_type, run_mode, confidence_bucket, sector."""
    dims = {
        "signal_type": lambda t: t.get("signal_type", "unknown"),
        "run_mode": lambda t: t.get("run_mode", "unknown"),
        "confidence_bucket": lambda t: confidence_bucket(t.get("confidence")),
        "sector": lambda t: t.get("sector", "Unknown"),
    }
    out: dict[str, list[dict]] = {}
    for dim, keyfn in dims.items():
        groups: dict[str, list[dict]] = {}
        for t in trades:
            groups.setdefault(str(keyfn(t)), []).append(t)
        rows = []
        for key, grp in groups.items():
            cell = _cell(grp, min_sample)
            cell["key"] = key
            rows.append(cell)
        rows.sort(key=lambda r: r["total_pnl"], reverse=True)
        out[dim] = rows
    return out


def compute_calibration(trades: list[dict[str, Any]], min_sample: int) -> list[dict[str, Any]]:
    """Bucket by stated confidence; compare realized win rate to bucket midpoint."""
    buckets = ["0.5-0.6", "0.6-0.7", "0.7-0.8", "0.8-0.9", "0.9-1.0"]
    grouped: dict[str, list[dict]] = {b: [] for b in buckets}
    for t in trades:
        b = confidence_bucket(t.get("confidence"))
        if b in grouped:
            grouped[b].append(t)
    rows = []
    for b in buckets:
        grp = grouped[b]
        n = len(grp)
        wins = sum(1 for t in grp if float(t.get("realized_return_pct", 0.0)) > 0)
        low = float(b.split("-")[0])
        midpoint = round(low + 0.05, 2)
        win_rate = (wins / n) if n else None
        rows.append({
            "bucket": b,
            "midpoint": midpoint,
            "count": n,
            "win_rate": win_rate,
            "calibration_gap": (win_rate - midpoint) if win_rate is not None else None,
            "meaningful": n >= min_sample,
        })
    return rows


def max_drawdown(equity: list[float]) -> float:
    """Largest peak-to-trough drop as a negative fraction (0.0 if none)."""
    peak = -float("inf")
    mdd = 0.0
    for v in equity:
        if v > peak:
            peak = v
        if peak > 0:
            dd = (v - peak) / peak
            if dd < mdd:
                mdd = dd
    return mdd


# ---------------------------------------------------------------------------
# Benchmark + equity (Alpaca-dependent)
# ---------------------------------------------------------------------------
def _strategy_equity_series(broker: Any, cfg: EvalConfig) -> tuple[list[str], list[float]]:
    """(iso dates, equity) from Alpaca portfolio history; empties on failure."""
    try:
        ph = broker.get_portfolio_history(cfg.history_period, cfg.history_timeframe)
    except Exception as e:  # noqa: BLE001
        log.warning("Portfolio history fetch failed: %s", e)
        return [], []
    timestamps = list(getattr(ph, "timestamp", []) or [])
    equity = list(getattr(ph, "equity", []) or [])
    dates, eq = [], []
    for ts, e in zip(timestamps, equity):
        if e is None:
            continue
        d = datetime.fromtimestamp(int(ts), tz=timezone.utc).date().isoformat()
        dates.append(d)
        eq.append(float(e))
    return dates, eq


def _benchmark_closes(market_data: Any, symbol: str, dates: list[str]) -> list[Optional[float]]:
    """Closing price aligned (as-of) to each strategy date; None where unavailable."""
    bars = market_data.get_bars([symbol]).get(symbol)
    if bars is None or bars.empty:
        return [None] * len(dates)
    # Map bar dates -> close.
    bar_dates: list[str] = []
    bar_closes: list[float] = []
    for ts, row in bars.iterrows():
        d = ts.date().isoformat() if hasattr(ts, "date") else str(ts)[:10]
        bar_dates.append(d)
        bar_closes.append(float(row["close"]))
    out: list[Optional[float]] = []
    for d in dates:
        i = bisect.bisect_right(bar_dates, d) - 1   # latest bar on/before date
        out.append(bar_closes[i] if i >= 0 else None)
    return out


def _normalize(series: list[Optional[float]], base100: bool = True) -> list[Optional[float]]:
    start = next((v for v in series if v), None)
    if not start:
        return [None] * len(series)
    return [(100.0 * v / start) if v else None for v in series]


def run_evaluation(
    secrets: Secrets,
    cfg: Config,
    broker: Any = None,
    market_data: Any = None,
) -> dict[str, Any]:
    """Compute all metrics and write data/evaluation/latest.json + summary.txt."""
    from src.broker import Broker
    from src.market_data import MarketData

    broker = broker or Broker(secrets)
    market_data = market_data or MarketData(secrets, cfg.universe.bars_lookback_days)
    ev = cfg.evaluation

    trades = load_closed_trades(cfg.paths.closed_trades_jsonl)
    overall = compute_overall(trades)
    breakdowns = compute_breakdowns(trades, ev.min_sample)
    calibration = compute_calibration(trades, ev.min_sample)

    dates, equity = _strategy_equity_series(broker, ev)
    strat_return = (equity[-1] / equity[0] - 1.0) if len(equity) >= 2 and equity[0] else 0.0
    mdd = max_drawdown(equity) if equity else 0.0

    equity_curves: dict[str, Any] = {"dates": dates, "strategy": _normalize(equity)}
    bench_returns: dict[str, Optional[float]] = {}
    for sym in ev.benchmarks:
        closes = _benchmark_closes(market_data, sym, dates) if dates else []
        equity_curves[sym] = _normalize(closes)
        first = next((c for c in closes if c), None)
        last = next((c for c in reversed(closes) if c), None)
        bench_returns[sym] = (last / first - 1.0) if first and last else None

    overall["total_return"] = strat_return
    overall["max_drawdown"] = mdd
    for sym, r in bench_returns.items():
        overall[f"{sym.lower()}_return"] = r
        overall[f"excess_vs_{sym.lower()}"] = (strat_return - r) if r is not None else None

    result = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "window": {"start": dates[0] if dates else None, "end": dates[-1] if dates else None},
        "min_sample": ev.min_sample,
        "benchmark_note": BENCHMARK_NOTE,
        "primary_benchmark": ev.primary_benchmark,
        "overall": overall,
        "equity_curves": equity_curves,
        "breakdowns": breakdowns,
        "calibration": calibration,
    }

    os.makedirs(cfg.paths.evaluation_dir, exist_ok=True)
    with open(cfg.paths.evaluation_latest_json, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, default=str)
    summary = render_summary(result)
    with open(cfg.paths.evaluation_summary_txt, "w", encoding="utf-8") as f:
        f.write(summary)
    log.info("Evaluation written (%d closed trades). %s",
             overall.get("trade_count", 0), cfg.paths.evaluation_latest_json)
    return result


def render_summary(result: dict[str, Any]) -> str:
    o = result.get("overall", {})
    n = o.get("trade_count", 0)
    lines = [
        "=== Strategy Evaluation ===",
        f"Window: {result['window'].get('start')} -> {result['window'].get('end')}",
        f"Closed trades: {n}",
    ]
    if n:
        pf = o.get("profit_factor")
        lines += [
            f"Total return: {o.get('total_return', 0):+.2%}   Max drawdown: {o.get('max_drawdown', 0):+.2%}",
            f"Win rate: {o.get('win_rate', 0):.0%}   Avg win: {o.get('avg_win', 0):+.2%}   Avg loss: {o.get('avg_loss', 0):+.2%}",
            f"Profit factor: {pf:.2f}" if pf is not None else "Profit factor: n/a (no losing trades yet)",
            f"Expectancy/trade: ${o.get('expectancy_per_trade', 0):+.2f}",
        ]
    excess_q = o.get("excess_vs_qqq")
    excess_s = o.get("excess_vs_spy")
    if excess_q is not None:
        lines.append(f"Excess vs QQQ: {excess_q:+.2%}  (primary benchmark)")
    if excess_s is not None:
        lines.append(f"Excess vs SPY: {excess_s:+.2%}")
    lines.append(result.get("benchmark_note", ""))
    if n < result.get("min_sample", 10):
        lines.append(f"\n[!] Only {n} closed trades — below the {result.get('min_sample', 10)}-trade "
                     "threshold; treat all breakdowns as NOT YET MEANINGFUL.")
    return "\n".join(lines) + "\n"
