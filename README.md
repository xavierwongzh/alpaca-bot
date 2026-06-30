# Alpaca Paper Trading Bot

An auto-executing **paper-trading** swing bot. It runs once each morning,
screens for ideas (catalyst screen + your options-flow signals), asks an
**OpenAI** model for trade decisions via structured outputs, then **sizes and
validates every trade in code** before placing **bracket orders** on a $10,000
Alpaca **paper** account.

> ⚠️ **Paper trading only.** The bot asserts the paper endpoint at startup and
> refuses to run against anything that looks like a live account. It never calls
> a live endpoint.

---

## Safety model

1. **Paper only** — `paper=True` is hard-coded in the client *and* the base URL
   is asserted to contain `paper-api.alpaca.markets`. Any live URL aborts startup.
2. **No hardcoded secrets** — all keys come from `.env` via `python-dotenv`.
3. **Kill switch** — a global `halt_trading` flag plus a `MAX_DAILY_LOSS` guard
   (`--halt`, config flag, or breach of the daily-loss limit) blocks all new buys.
4. **Code decides, model proposes** — the LLM only proposes weights. `risk.py`
   converts to share quantities and enforces position size, position count, and
   buying-power limits before anything is sent.

> A note on the spec: the prompt mentions both "OpenAI" and "Claude" as the
> decision engine. The Stack section is explicit about the **OpenAI SDK** with
> `gpt-4o`, so this build uses OpenAI. Swapping providers means editing only
> `src/decision.py` and `src/context.py`.

---

## Architecture (four layers)

| Layer | Module | Responsibility |
|------|--------|----------------|
| 1 | `src/market_data.py` | Quotes, daily bars, technicals (20/50 SMA, RSI-14, 52w distance) |
| 2 | `src/analytics.py` | Portfolio view: cash vs invested, concentration, uP&L, stop/target distance |
| 3 | `src/context.py` | VIX/macro, headlines, one batched OpenAI **summary** call |
| 4 | `src/decision.py` → `src/risk.py` → `src/execution.py` → `src/alerts.py` | Decisions, sizing, bracket execution, alerts |

`src/flow.py` is the options-flow scanner (see below); `src/dashboard.py` renders
the terminal UI; `src/broker.py` holds the paper-mode assertion; `src/logger.py`
handles the trade log + JSON snapshots.

---

## Setup

```bash
# 1. Python 3.11+
python -m venv .venv
. .venv/Scripts/activate        # Windows PowerShell: .venv\Scripts\Activate.ps1
pip install -r requirements.txt

# 2. Configure secrets
cp .env.example .env            # then edit .env with your PAPER keys
```

### Environment variables (`.env`)

| Var | Description |
|-----|-------------|
| `ALPACA_API_KEY` | Alpaca **paper** API key |
| `ALPACA_SECRET_KEY` | Alpaca **paper** secret |
| `ALPACA_BASE_URL` | Must be `https://paper-api.alpaca.markets` |
| `OPENAI_API_KEY` | OpenAI key for decisions + summary |

> **Do not put real keys in `.env.example`** — it is a template meant to be
> committed. Keep real keys in `.env`, which should be git-ignored.

---

## Run

```bash
python main.py                       # full morning run (--mode open, places orders)
python main.py --mode midday         # conservative midday pass
python main.py --dry-run             # analyze + size, place NO orders
python main.py --mode midday --dry-run
python main.py --halt                # force kill switch (no new buys)
python main.py --no-llm              # skip OpenAI (stub summary, no decisions)
```

Recommended first run: **`python main.py --dry-run`** to confirm the pipeline
end to end without sending orders.

### Two daily runs: `open` and `midday`

| | **open** (morning, default) | **midday** (conservative) |
|---|---|---|
| Purpose | Full entry scan + position management | Catch a *standout* new afternoon signal only |
| New entries | Normal risk sizing | Must clear `MIDDAY_MIN_CONFIDENCE` **and** flow `MIDDAY_MIN_COMPOSITE`; capped at `MIDDAY_MAX_NEW_POSITIONS` |
| Sells | As proposed | **No same-day reversals** — positions younger than `MIDDAY_MIN_HOLD_DAYS` can't be sold; others only above `MIDDAY_SELL_MIN_CONFIDENCE` |
| Exits | Broker brackets | Broker brackets (unchanged) |

These limits are **enforced in code** (`apply_midday_filter` in `src/risk.py`),
not just in the LLM prompt — the model is *told* its context, but the code is the
real gate. Risk rails, paper-mode guards, and bracket execution are identical in
both modes. Every order/snapshot is tagged with its `mode` in the trade log and
daily JSON.

### Scheduling (one morning run, no polling)

Use the OS scheduler. Example cron (weekdays, 9:35 AM ET, after the open):

```cron
35 9 * * 1-5  cd /path/to/alpaca-bot && /path/to/.venv/bin/python main.py >> logs/cron.log 2>&1
```

On Windows, create a Task Scheduler task that runs
`.venv\Scripts\python.exe main.py` on the same schedule.

---

## Evaluation — "does it actually work?"

Measurement only; never touches trading. Runs at the end of every `python main.py`
and standalone via **`python evaluate.py`**.

1. **Closed-trade reconciliation** ([src/reconcile.py](src/reconcile.py)) — FIFO-matches
   filled buy/sell orders (incl. bracket legs) into round-trips, infers `exit_reason`
   (`target`/`stop`/`manual`), joins each back to its decision record by
   `client_order_id` to carry tags (`signal_type` flow/catalyst, `run_mode`,
   `confidence`, `sector`), and appends to `data/closed_trades.jsonl`. Idempotent —
   re-runs never double-record.
2. **Benchmarks** — pulls SPY/QQQ for the live window, aligns to the strategy equity
   curve (Alpaca portfolio history), and reports strategy return minus each. **QQQ is
   the honest benchmark** given the tech-tilted watchlist.
3. **Metrics** ([src/evaluation.py](src/evaluation.py)) — total return, max drawdown,
   win rate, avg win/loss, profit factor, expectancy, excess vs SPY/QQQ; per-tag
   breakdowns (signal type, run mode, confidence bucket, sector); and a **confidence
   calibration** table (realized win rate vs stated confidence). Every cell carries a
   sample size and is flagged **"not yet meaningful"** below `min_sample` (default 10),
   so three trades never masquerade as signal.

Outputs: `data/evaluation/latest.json` + `summary.txt`. The dashboard's
**Performance** tab reads `public/evaluation.json` (published by CI): equity overlay
vs SPY/QQQ, metric cards, breakdown tables, and the calibration table, with low-sample
cells visually de-emphasized.

## Tests

Offline, deterministic, no network or API keys required:

```bash
pip install -r requirements-dev.txt
pytest -q
```

Covers the paper-mode safety gate, risk sizing/limit checks, and the options-flow
scanner (direction classification + noise filtering).

## GitHub Actions

Two workflows live in [`.github/workflows/`](.github/workflows/):

- **`ci.yml`** — on every push/PR: byte-compiles the Python, runs `pytest`, and
  builds the dashboard (typecheck included). No secrets needed.
- **`daily-trade.yml`** — scheduled weekday morning run of the bot, then publishes
  the latest snapshot + flow cache into `dashboard/public/` (for the Phase 2
  frontend) and commits them.

### Setup

1. Push the repo to GitHub.
2. **Settings → Secrets and variables → Actions → New repository secret**, add:
   - `ALPACA_API_KEY` (paper)
   - `ALPACA_SECRET_KEY` (paper)
   - `OPENAI_API_KEY`

   `ALPACA_BASE_URL` is pinned to the paper endpoint inside the workflow.
3. **Manual test run:** Actions → *Daily paper trade* → *Run workflow*. Leave
   **dry_run = true** to analyze and size without placing orders. The scheduled
   run uses live paper mode (places orders).

### Schedules / DST caveat

Two crons drive the two run modes; the workflow maps the triggering schedule to
`--mode`:

- **Morning** `40 13 * * 1-5` → `--mode open` = **9:40 AM ET during EDT** (summer).
- **Midday** `0 17 * * 1-5` → `--mode midday` = ~12–1 PM ET (both DST regimes).

GitHub cron is UTC and does **not** follow DST — during EST (winter), shift the
morning cron to `40 14 * * 1-5`. Orders are market orders, so runs must land
inside regular trading hours. Manual `workflow_dispatch` runs default to dry-run
and let you pick the mode.

## Configuration

Everything tunable is in [`config.py`](config.py):

| Setting | Default |
|---------|---------|
| Starting equity | $10,000 |
| Max position size | 15% of equity / name |
| Max concurrent positions | 8 |
| Stop loss | −8% |
| Profit target | +20% |
| Max daily loss (halt) | −5% |
| Decision model | `gpt-5.5` (reasoning; `reasoning_effort=medium`) |
| Summary model | `gpt-4o-mini` |
| Midday min confidence / composite | `0.72` / `60` |
| Midday sell min confidence / min hold | `0.75` / `1 day` |
| Midday max new positions | `2` |
| Universe | liquid US equities + ETFs |

> **Model note:** the decision model is `gpt-5.5`, a reasoning model. It rejects
> a custom `temperature`, so the decision call omits it and passes
> `reasoning_effort` instead (handled in `src/decision.py`). The cheap Layer 3
> summary stays on `gpt-4o-mini`. Both are config values — swap freely.

---

## Options-flow scanner

[`src/flow.py`](src/flow.py) scans option contracts across the **`WATCHLIST`**
(in [`config.py`](config.py), merged with the catalyst-screen names), drops
noise, scores what's left, and emits the top-N signals. Every threshold lives in
`FlowConfig` — tune in one place.

**Pipeline:**

1. **Fetch** contracts for each watchlist name. Source is `config.flow.source`:
   - `alpaca` — live option-chain snapshots
   - `csv` — [`data/flow_contracts.csv`](data/flow_contracts.csv) (offline/testing)
   - `auto` — try Alpaca, fall back to CSV (default)
2. **Per-contract filters** (drop noise): `MIN_CONTRACT_VOLUME` (500),
   `MIN_VOL_OI_RATIO` (2.0 — new positioning, not existing), `MIN_NOTIONAL_USD`
   ($250k), `DTE_MIN/DTE_MAX` (1–60), and moneyness within ±20% of spot. OTM
   calls 0–15% above spot are flagged as the speculative-bullish bucket.
3. **Aggression proxy**: `score = (last − bid) / (ask − bid)`, clamped [0,1].
   `≥ AGGRESSION_BUY` (0.6) = aggressive buying; `≤ 0.4` = aggressive selling.
   Skipped if the spread is zero/missing.
4. **Composite score (0–100)**: weighted blend of vol/OI (35%, capped at 10),
   notional (30%, capped at $2M), aggression (25%), and DTE urgency (10%).
5. **Ticker direction**: `call_put_notional_ratio = call_notional / max(put_notional, 1)`.
   `≥ 2.0` with aggressive call buying ⇒ **bullish**; `≤ 0.5` with aggressive put
   buying ⇒ **bearish**.

**Long-only mapping:** only **bullish** ticker signals become candidate long
entries on the underlying. **Bearish** signals are passed to the OpenAI decision
engine as *caution* context (avoid/trim) — never as short orders, and the
rationale notes that heavy put buying may be hedging.

**Outputs:** the full ranked list is written to
[`data/flow_cache.json`](data/flow_cache.json) for inspection; the top
`TOP_N_SIGNALS` (12) are fed into the decision call.

### Editing the watchlist / thresholds

- **Watchlist:** edit `FlowConfig.WATCHLIST` in `config.py`.
- **Thresholds:** every constant (`MIN_CONTRACT_VOLUME`, `MIN_VOL_OI_RATIO`,
  `MIN_NOTIONAL_USD`, `DTE_MIN/MAX`, `MONEYNESS_MAX`, `OTM_CALL_SPEC_MAX`,
  `AGGRESSION_BUY/SELL`, the `W_*` weights and `*_CAP` normalizers,
  `TOP_N_SIGNALS`, `BULLISH_CP_RATIO`, `BEARISH_CP_RATIO`) is in `FlowConfig`.
- **Offline testing:** set `source="csv"` and edit
  [`data/flow_contracts.csv`](data/flow_contracts.csv)
  (`underlying,option_symbol,type,strike,expiry,spot,contract_price,bid,ask,volume,open_interest,implied_volatility`).
- **Live UOA feed (Phase 2):** replace `fetch_contracts_alpaca()` /
  `fetch_contracts_csv()` in `src/flow.py` with your source — the scoring,
  aggregation, and decision pipeline are unchanged.

---

## Outputs

- **Terminal dashboard** — equity, day P&L, positions w/ stop & target distance,
  allocation, today's actions, alerts.
- **Trade log** — append-only `logs/trade_log.csv` (every placed/filled/rejected order, tagged with `mode`).
- **Daily snapshot** — `logs/snapshots/portfolio_<timestamp>.json` (includes cost + decision records).
- **Cost log** — `data/cost_log.csv`: real per-run token counts and dollar cost
  (input / cached / output / reasoning), tagged with mode, priced from `CostConfig`.
- **Decision records** — `data/decisions/decisions.jsonl`: one record per placed
  trade with the model's full rationale + signal context, joined to Alpaca orders
  by `client_order_id`. Powers the dashboard drill-down.

## Cost & decision records

- **Lean payload:** only open positions + candidate tickers (flow top-signals +
  catalyst names) go to the decision model — never the ~60-name watchlist.
- **Prompt caching:** the static system prompt + JSON schema are sent first and
  are stable per mode, so OpenAI prompt caching can serve that prefix at the
  discounted rate; only the dynamic payload changes per run.
- **Real cost:** each OpenAI response's `usage` is priced via `CostConfig` (USD
  per 1M tokens) and written to `data/cost_log.csv` + the snapshot. A typical run
  is ~$0.05–0.06; two runs/day ≈ $0.11/day.
- **Why-this-trade:** every placed order stores a `DecisionRecord` (rationale,
  confidence, flow signal, technicals at decision time, VIX/market context, stop/
  target). Note: a reasoning model's raw chain-of-thought is **not** returned by
  the API — the stored rationale + signal context is the explanation.

---

## Phase 2 (not built yet)

- Options support (LEAPS on larger names, shares on small caps).
- Replace the CSV flow feed with a live unusual-options-activity source.
```
