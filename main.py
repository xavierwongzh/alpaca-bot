"""
Morning-run orchestrator for the Alpaca paper-trading bot.

Single command:  python main.py
Flags:
  --dry-run   run full analysis + sizing but place NO orders
  --halt      force the kill switch on (no new buys)
  --no-llm    skip OpenAI calls (uses a stub summary + no decisions)

Pipeline:
  0. Load config + secrets, assert PAPER mode.
  1. Account read + positions (Layer 1/2 inputs).
  2. Market data + technicals (Layer 1).
  3. Portfolio analytics (Layer 2).
  4. Context: VIX, headlines, LLM summary (Layer 3).
  5. Decisions (Layer 4) -> size & validate (risk) -> bracket execution.
  6. Alerts, dashboard, trade log, daily snapshot.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone

from config import Secrets, get_config
from src.alerts import build_alerts
from src.analytics import build_portfolio_view
from src.broker import Broker, LiveModeError
from src.context import get_headlines, get_macro_context, summarize_market
from src.cost import write_cost_log
from src.dashboard import console, render_dashboard
from src.decision import get_decisions
from src.evaluation import run_evaluation
from src.execution import place_orders, reconcile_protection, live_protection_by_symbol
from src.exits import PositionSnapshot, resolve_all, apply_exits, append_exit_records
from src.flow import bullish_tickers, scan_flow
from src.logger import get_logger, init_trade_log, log_trade_event, write_snapshot
from src.market_data import MarketData
from src.records import append_decision_records, build_decision_records
from src.reconcile import latest_entry_by_symbol, reconcile_closed_trades
from src.risk import apply_midday_filter, daily_loss_halt_triggered, size_and_validate
from src.runstate import already_ran_today, mark_ran_today

log = get_logger()


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Alpaca paper trading bot — morning run")
    p.add_argument("--dry-run", action="store_true", help="analyze + size but do not place orders")
    p.add_argument("--halt", action="store_true", help="force kill switch (no new buys)")
    p.add_argument("--no-llm", action="store_true", help="skip OpenAI calls")
    p.add_argument(
        "--mode", choices=["open", "midday"], default="open",
        help="run mode: 'open' = full morning entry scan + management; "
             "'midday' = conservative second pass (new standout signals only)",
    )
    return p.parse_args()


def main() -> int:
    args = parse_args()
    cfg = get_config()
    dry_run = cfg.dry_run or args.dry_run
    mode = args.mode

    # --- 0. secrets + paper-mode gate ---
    secrets = Secrets.from_env()
    try:
        broker = Broker(secrets)
    except LiveModeError as e:
        console.print(f"[bold red]SAFETY ABORT:[/bold red] {e}")
        return 2
    except Exception as e:  # noqa: BLE001
        console.print(f"[bold red]Startup failed:[/bold red] {e}")
        return 1

    init_trade_log(cfg.paths.trade_log_csv)
    log.info("Run mode: [bold]%s[/bold]%s", mode, " (dry-run)" if dry_run else "")

    # --- idempotency guard: one successful run per mode per US market day ---
    # The workflow fires each mode from several redundant slots so a dropped
    # GitHub trigger can't cost the run. This gate runs BEFORE the flow scan and
    # any OpenAI call, so a duplicate slot exits here having spent ZERO tokens.
    # The completion marker is written only at the END of a successful run, so a
    # slot that fails early doesn't block a later slot from retrying. Skipped in
    # dry-run so manual testing always proceeds.
    market_date = broker.get_market_date()
    if not dry_run and already_ran_today(cfg.paths, mode, market_date):
        log.info("[yellow]%s run already completed today (%s) — skipping.[/yellow]",
                 mode, market_date)
        return 0

    # --- market-open safeguard ---
    # GitHub's scheduler can fire on holidays/early closes. When the market is
    # closed we still run the full read-only analysis (and evaluation), but submit
    # NO orders — so we never fire orders into a closed market. Order placement is
    # skipped cleanly with reason "market_closed".
    try:
        market_open = broker.is_market_open()
    except Exception as e:  # noqa: BLE001
        log.warning("Could not read market clock (%s); assuming CLOSED for safety.", e)
        market_open = False
    if not market_open:
        log.warning("[bold yellow]Market is CLOSED[/bold yellow] — analysis only, no orders will be submitted.")

    # --- 1. account + positions ---
    account = broker.get_account()
    raw_positions = broker.get_positions()
    held_tickers = [p.symbol for p in raw_positions]

    # --- protection reconciliation (invariant: every position has GTC TP+SL) ---
    # Runs BEFORE new decisions. Re-attaches missing brackets so a position that
    # lost its protection (e.g. old DAY legs that expired) is protected again.
    # Skipped in dry-run (it places real GTC orders).
    if not dry_run:
        try:
            entries_by_sym = latest_entry_by_symbol(cfg.paths.decisions_jsonl)
            repaired = reconcile_protection(broker, cfg, raw_positions, entries_by_sym)
            if repaired:
                log.info("Protection reconciliation: repaired %d position(s): %s",
                         len(repaired), ", ".join(r["ticker"] for r in repaired))
        except Exception as e:  # noqa: BLE001
            log.warning("Protection reconciliation failed (non-fatal): %s", e)

    # --- kill switch / daily loss halt ---
    halt = (
        cfg.halt_trading
        or args.halt
        or daily_loss_halt_triggered(account.day_pnl_pct, cfg.risk)
    )
    halt_from_loss = daily_loss_halt_triggered(account.day_pnl_pct, cfg.risk)

    # --- universe = flow WATCHLIST + catalyst-screen names + held ---
    scan_universe = sorted(set(cfg.flow.WATCHLIST) | set(held_tickers))
    base_universe = sorted(set(cfg.flow.WATCHLIST) | set(cfg.universe.candidates) | set(held_tickers))

    # --- 2. market data + technicals (Layer 1) ---
    md = MarketData(secrets, lookback_days=cfg.universe.bars_lookback_days)
    tech = md.technicals(base_universe)
    last_prices = md.get_last_prices(base_universe)

    # --- options-flow scan (needs spot prices) ---
    flow = scan_flow(secrets, cfg.flow, cfg.paths, scan_universe, last_prices)
    flow_triggers = bullish_tickers(flow)
    flow_by_ticker: dict[str, dict] = {s.ticker: s.as_dict() for s in flow}

    # Candidates = base universe + any bullish flow underlyings (deduped).
    candidates = sorted(set(base_universe) | set(flow_triggers))

    # --- 3. portfolio analytics (Layer 2) ---
    portfolio = build_portfolio_view(account, raw_positions, cfg.risk)

    # Live protective levels + position ages feed BOTH the exit management context
    # (below) and the midday same-day guard. Read once here.
    live_levels = live_protection_by_symbol(broker)
    position_age_days = broker.get_position_age_days()

    # --- 4. context (Layer 3) ---
    macro = get_macro_context()
    summary_usage = None
    if args.no_llm:
        market_summary = f"[LLM skipped] Macro regime {macro.regime} (VIX {macro.vix})."
        headlines = {}
    else:
        news_tickers = sorted(
            set(held_tickers) | set(flow_by_ticker.keys()) | set(cfg.universe.candidates)
        )[:12]
        headlines = get_headlines(news_tickers, cfg.universe.max_news_per_ticker)
        market_summary, summary_usage = summarize_market(
            secrets, cfg.model, macro, headlines,
            {t: tech[t].as_dict() for t in news_tickers if t in tech},
            cost_cfg=cfg.cost,
        )

    # --- 5. decisions -> sizing -> execution (Layer 4) ---
    # COST: keep the decision payload lean — only open positions + candidate
    # tickers (flow top-signals + catalyst screen). The ~60-name WATCHLIST is for
    # SCANNING only and is deliberately NOT sent to the model (the main token saving).
    decision_tickers = sorted(
        set(held_tickers) | set(flow_by_ticker.keys()) | set(cfg.universe.candidates)
    )
    candidate_payload = []
    for t in decision_tickers:
        item = {"ticker": t, "technicals": tech[t].as_dict() if t in tech else {}}
        if t in flow_by_ticker:
            item["flow_signal"] = flow_by_ticker[t]
        candidate_payload.append(item)

    # Regime read (VIX + SPY trend) and per-open-position management context, so
    # the AI can re-evaluate each position's stop/target in the SAME batched call.
    regime = {
        "vix": macro.vix, "vix_change_5d": macro.vix_change_5d, "regime": macro.regime,
        "spy": tech["SPY"].as_dict() if "SPY" in tech else None,
    }
    positions_management = []
    for p in portfolio.positions:
        lv = live_levels.get(p.ticker, {})
        positions_management.append({
            "ticker": p.ticker,
            "entry_price": p.avg_entry,
            "current_price": p.last_price,
            "unrealized_pl": round(p.unrealized_pl, 2),
            "unrealized_pl_pct": round(p.unrealized_pl_pct, 4),
            "holding_days": position_age_days.get(p.ticker),
            "current_stop": lv.get("stop"),
            "current_target": lv.get("target"),
            "weight": p.weight,
            "flow_signal": flow_by_ticker.get(p.ticker),
            "technicals": tech[p.ticker].as_dict() if p.ticker in tech else {},
        })

    decisions = []
    exit_actions = []
    decision_usage = None
    if not args.no_llm:
        decisions, exit_actions, decision_usage = get_decisions(
            secrets, cfg.model, cfg.risk,
            portfolio=portfolio.as_dict(),
            candidates=candidate_payload,
            flow_signals=[s.as_dict() for s in flow],
            market_summary=market_summary,
            mode=mode,
            cost_cfg=cfg.cost,
            positions_management=positions_management,
            regime=regime,
        )

    # Midday: enforce the conservative policy IN CODE before sizing/execution.
    midday_dropped = []
    if mode == "midday":
        ages = {t: position_age_days.get(t) for t in held_tickers}
        decisions, midday_dropped = apply_midday_filter(
            decisions,
            position_age_days=ages,
            flow_by_ticker=flow_by_ticker,
            held_tickers=set(held_tickers),
            midday=cfg.midday,
        )
        log.info("Midday filter: %d decisions kept, %d dropped", len(decisions), len(midday_dropped))

    # Submit only when not a dry-run AND the market is open. Otherwise skip
    # cleanly, labelling why ("dry_run" vs "market_closed").
    place_live = (not dry_run) and market_open
    skip_detail = "dry_run" if dry_run else ("market_closed" if not market_open else "dry_run")

    # --- exit management: the AI adjusts each position's stop/target; the broker
    # enforces the result. Code guardrails (ratchet, hard max-loss, sanity,
    # same-day churn) are applied in resolve_all before anything touches the
    # broker. Position EXITS go through here, not the entry path. ---
    exit_snaps = [
        PositionSnapshot(
            ticker=p.ticker, qty=int(p.qty), entry=p.avg_entry, current_price=p.last_price,
            current_stop=live_levels.get(p.ticker, {}).get("stop"),
            current_target=live_levels.get(p.ticker, {}).get("target"),
            age_days=position_age_days.get(p.ticker),
        )
        for p in portfolio.positions
    ]
    exit_resolutions = resolve_all(exit_snaps, exit_actions, cfg.risk, cfg.exits)
    exit_records = apply_exits(broker, exit_resolutions, mode=mode, regime=regime,
                               dry_run=not place_live)
    if exit_records:
        append_exit_records(cfg.paths.exits_jsonl, exit_records)

    # Entries (buy/hold only): position exits are handled by exit_actions above,
    # so any 'sell' the model still emitted is ignored in the entry path.
    entry_decisions = [d for d in decisions if d.action != "sell"]
    sells_ignored = len(decisions) - len(entry_decisions)
    if sells_ignored:
        log.info("Ignoring %d 'sell' decision(s) in entries (exits handled by exit_actions).",
                 sells_ignored)

    sized, rejected = size_and_validate(
        entry_decisions, portfolio, last_prices, account.buying_power, cfg.risk, halt
    )
    rejected = list(rejected) + midday_dropped

    exec_results = place_orders(broker, sized, cfg.paths, dry_run=not place_live,
                                mode=mode, skip_detail=skip_detail)

    # --- persist decision records (the dashboard drill-down) ---
    # Only persist when orders were actually placed live, so the records log
    # reflects real trades joinable by client_order_id.
    decision_records = []
    if place_live and sized:
        decision_records = build_decision_records(
            sized_orders=sized,
            exec_results=exec_results,
            mode=mode,
            model=cfg.model.decision_model,
            reasoning_effort=cfg.model.reasoning_effort,
            technicals={t: tech[t].as_dict() for t in tech},
            flow_by_ticker=flow_by_ticker,
            macro=macro.as_dict(),
            market_summary=market_summary,
        )
        append_decision_records(cfg.paths.decisions_jsonl, decision_records)

    # --- cost accounting (real numbers from usage) ---
    cost_row = None
    if not args.no_llm and (decision_usage or summary_usage):
        cost_row = write_cost_log(cfg.paths.cost_log_csv, mode, decision_usage, summary_usage)

    # --- 6. alerts, dashboard, snapshot ---
    try:
        recently_filled = broker.client.get_orders()  # default recent orders
    except Exception:  # noqa: BLE001
        recently_filled = []
    alerts = build_alerts(portfolio, account.day_pnl_pct, cfg.risk, halt_from_loss, recently_filled)

    render_dashboard(account, portfolio, macro, market_summary,
                     exec_results, rejected, alerts, halt_from_loss, mode=mode)

    snapshot = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "mode": mode,
        "account": {
            "equity": account.equity, "cash": account.cash,
            "buying_power": account.buying_power, "day_pnl": account.day_pnl,
            "day_pnl_pct": account.day_pnl_pct,
        },
        "portfolio": portfolio.as_dict(),
        "macro": macro.as_dict(),
        "market_summary": market_summary,
        "flow_signals": [s.as_dict() for s in flow],
        "decisions": [d.as_dict() for d in decisions],
        "decision_records": decision_records,
        "exit_actions": [e.as_dict() for e in exit_actions],
        "exit_records": exit_records,
        "cost": cost_row,
        "usage": {
            "decision": decision_usage.as_dict() if decision_usage else None,
            "summary": summary_usage.as_dict() if summary_usage else None,
        },
        "executed": [r.__dict__ for r in exec_results],
        "rejected": [r.__dict__ for r in rejected],
        "alerts": [a.as_dict() for a in alerts],
        "halt": halt,
        "market_open": market_open,
        "dry_run": dry_run,
    }
    snap_path = write_snapshot(cfg.paths.snapshots_dir, snapshot)
    log.info("Snapshot written to %s", snap_path)

    # --- 7. evaluation: reconcile closed trades + recompute metrics ---
    # Read-only / measurement only; never affects trading. Failures here must not
    # break the run, so they're caught and logged.
    try:
        reconcile_closed_trades(broker, cfg.paths)
        run_evaluation(secrets, cfg, broker=broker, market_data=md)
    except Exception as e:  # noqa: BLE001
        log.warning("Evaluation step failed (non-fatal): %s", e)

    # --- mark this run complete (idempotency marker for redundant slots) ---
    # Written only here, after a full successful run, so a later slot sees it and
    # skips. Not written in dry-run. CI commits data/state/ so the next slot's
    # fresh runner can read it.
    if not dry_run:
        mark_ran_today(cfg.paths, mode, market_date)

    return 0


if __name__ == "__main__":
    sys.exit(main())
