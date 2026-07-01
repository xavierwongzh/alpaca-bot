"""
Central configuration for the Alpaca paper-trading bot.

Everything tunable lives here. Secrets are NOT stored here — they are read
from the environment (.env) by `load_env()`.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from dotenv import load_dotenv


# ---------------------------------------------------------------------------
# Environment / secrets
# ---------------------------------------------------------------------------
def load_env() -> None:
    """Load variables from a local .env file into os.environ (idempotent)."""
    load_dotenv()


@dataclass(frozen=True)
class Secrets:
    alpaca_api_key: str
    alpaca_secret_key: str
    openai_api_key: str
    alpaca_base_url: str

    @classmethod
    def from_env(cls) -> "Secrets":
        load_env()
        return cls(
            alpaca_api_key=os.getenv("ALPACA_API_KEY", "").strip(),
            alpaca_secret_key=os.getenv("ALPACA_SECRET_KEY", "").strip(),
            openai_api_key=os.getenv("OPENAI_API_KEY", "").strip(),
            alpaca_base_url=os.getenv(
                "ALPACA_BASE_URL", "https://paper-api.alpaca.markets"
            ).strip(),
        )


# ---------------------------------------------------------------------------
# Risk + sizing config (tune everything here)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class RiskConfig:
    starting_equity: float = 10_000.0       # paper account baseline
    max_position_pct: float = 0.15          # 15% of equity per name
    max_concurrent_positions: int = 8       # 6-8 names
    stop_loss_pct: float = -0.08            # -8% per trade
    profit_target_pct: float = 0.20         # +20% per trade
    max_daily_loss_pct: float = -0.05       # halt new orders past -5% on the day
    min_order_notional: float = 100.0       # don't bother with tiny orders
    # A move beyond this (abs) on an open position raises a "big move" alert.
    big_move_alert_pct: float = 0.10


# ---------------------------------------------------------------------------
# Midday run config
#
# The midday pass is a lighter, conservative second run. It must NOT relitigate
# the morning's trades — it only catches a standout new afternoon signal and lets
# the brackets manage exits. These bars are intentionally HIGHER than the morning
# run (which has no confidence/composite gate beyond risk sizing).
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class MiddayConfig:
    MIN_HOLD_DAYS: int = 1            # don't sell positions younger than this (no same-day reversals)
    MIN_CONFIDENCE: float = 0.72     # min LLM confidence to OPEN a new entry at midday
    MIN_COMPOSITE: float = 60.0      # min flow composite score (0-100) to OPEN at midday
    SELL_MIN_CONFIDENCE: float = 0.75  # min confidence to allow a sell at midday
    MAX_NEW_POSITIONS: int = 2       # cap on new positions opened on a midday run


# ---------------------------------------------------------------------------
# OpenAI / model config
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ModelConfig:
    # Decision engine: OpenAI's current flagship reasoning model. Swappable here.
    decision_model: str = "gpt-5.5"
    summary_model: str = "gpt-4o-mini"      # cheaper model for the Layer 3 summary
    # temperature is applied ONLY to non-reasoning models (e.g. the summary call).
    # GPT-5 / o-series reasoning models reject a custom temperature, so the
    # decision call drops it automatically (see decision.py).
    temperature: float = 0.2
    # Reasoning effort for GPT-5 / o-series decision calls: "low" | "medium" | "high".
    # Kept at medium deliberately (decision quality); do not lower for cost.
    reasoning_effort: str = "medium"
    max_retries: int = 1                    # retry the decision call once on failure


# ---------------------------------------------------------------------------
# Cost config — USD per 1,000,000 tokens. Tune to current OpenAI pricing.
# Turns each response's `usage` into a real dollar figure (no guessing).
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class CostConfig:
    # Decision model (gpt-5.5). reasoning_tokens are billed as output tokens.
    decision_input_per_m: float = 5.0
    decision_cached_input_per_m: float = 0.50   # cached input is heavily discounted
    decision_output_per_m: float = 30.0
    # Summary model (gpt-4o-mini).
    summary_input_per_m: float = 0.15
    summary_cached_input_per_m: float = 0.075
    summary_output_per_m: float = 0.60


# ---------------------------------------------------------------------------
# Evaluation config
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class EvalConfig:
    # Cells (per-tag / calibration buckets) with fewer trades than this are
    # flagged "not yet meaningful" so we don't read noise as signal.
    min_sample: int = 10
    # Window for the strategy equity curve + benchmark alignment.
    history_period: str = "1M"          # Alpaca portfolio-history period
    history_timeframe: str = "1D"
    benchmarks: tuple[str, ...] = ("SPY", "QQQ")
    # The honest benchmark given the tech-tilted watchlist.
    primary_benchmark: str = "QQQ"


# ---------------------------------------------------------------------------
# Universe + data config
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class UniverseConfig:
    # Liquid US equities + ETFs only for v1 (no options). Tune freely.
    candidates: tuple[str, ...] = (
        "AAPL", "MSFT", "NVDA", "AMD", "GOOGL", "AMZN", "META", "TSLA",
        "JPM", "XOM", "UNH", "PFE", "DIS", "NKE", "INTC", "BA",
        "SPY", "QQQ", "IWM", "XLE", "XLF", "XLV",
    )
    bars_lookback_days: int = 260           # ~1 trading year for 52w + SMAs
    news_lookback_days: int = 5
    max_news_per_ticker: int = 3


# ---------------------------------------------------------------------------
# Options-flow scanner config
#
# Every numeric threshold the scanner uses lives here so it can be tuned in one
# place. Names match the follow-up spec exactly.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class FlowConfig:
    # Data source for option contracts:
    #   "yfinance" -> free live-ish option chains (delayed ~15m, snapshot)
    #   "csv"      -> data/flow_contracts.csv — STUB/last-resort only
    #   "auto"     -> yfinance, fall back to csv (default)
    source: str = "auto"
    options_feed: str = "indicative"        # (legacy, unused) alpaca feed selector

    # --- Per-contract filters (drop noise before scoring) ---
    MIN_CONTRACT_VOLUME: int = 500          # day volume on the contract
    MIN_VOL_OI_RATIO: float = 2.0           # day volume / open interest (new positioning)
    MIN_NOTIONAL_USD: float = 500_000       # volume * contract price * 100
    DTE_MIN: int = 1                        # ignore same-day-expiry noise
    DTE_MAX: int = 60                        # focus on short-to-medium dated flow
    MONEYNESS_MAX: float = 0.20             # keep contracts within +/-20% of spot
    OTM_CALL_SPEC_MAX: float = 0.15         # OTM calls 0-15% above spot = speculative-bullish

    # --- Ask-side aggression proxy ---
    AGGRESSION_BUY: float = 0.6             # >= this: aggressive buying (upper spread / at ask)
    AGGRESSION_SELL: float = 0.4            # <= this: aggressive selling (bid side)

    # --- Composite score weights (0..100), must sum to 1.0 ---
    W_VOL_OI: float = 0.35
    W_NOTIONAL: float = 0.30
    W_AGGRESSION: float = 0.25
    W_DTE: float = 0.10
    # Normalization caps for the contributions above.
    VOL_OI_CAP: float = 10.0                # vol/OI contribution capped at ratio = 10
    NOTIONAL_CAP: float = 2_000_000.0       # notional contribution capped at $2M
    TOP_N_SIGNALS: int = 12                 # emit the top-N scored signals per run

    # --- Ticker-level direction ---
    BULLISH_CP_RATIO: float = 2.0           # call/put notional ratio >= -> bullish
    BEARISH_CP_RATIO: float = 0.5           # call/put notional ratio <= -> bearish

    # Default, editable starting watchlist of liquid, high-options-volume names.
    # The catalyst screen's tickers are merged in at runtime (deduped).
    WATCHLIST: tuple[str, ...] = (
        # Mega-cap tech
        "AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA", "TSLA", "NFLX",
        # Semiconductors
        "AMD", "AVGO", "MU", "INTC", "TSM", "ARM", "MRVL", "QCOM", "SMCI",
        # AI infra / software
        "PLTR", "DELL", "ANET", "CRM", "NOW", "SNOW", "ORCL", "ADBE",
        # Fintech / crypto-adjacent
        "COIN", "HOOD", "PYPL", "SOFI", "AFRM", "MSTR",
        # EV / auto
        "RIVN", "LCID", "NIO",
        # Quantum / space
        "IONQ", "RGTI", "QBTS", "RKLB", "ASTS",
        # Clean energy / materials
        "ENPH", "FSLR", "PLUG", "MP", "FCX",
        # Consumer / high-retail-interest
        "HIMS", "RDDT", "DKNG", "UBER", "ABNB", "GME",
        # Health
        "LLY", "MRNA", "PFE",
        # Liquid ETFs (regime context, not necessarily traded)
        "SPY", "QQQ", "IWM", "SMH",
    )


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Paths:
    root: str = os.path.dirname(os.path.abspath(__file__))
    data_dir: str = field(default="")
    flow_contracts_csv: str = field(default="")
    flow_cache_json: str = field(default="")
    cost_log_csv: str = field(default="")
    decisions_dir: str = field(default="")
    decisions_jsonl: str = field(default="")
    closed_trades_jsonl: str = field(default="")
    evaluation_dir: str = field(default="")
    evaluation_latest_json: str = field(default="")
    evaluation_summary_txt: str = field(default="")
    logs_dir: str = field(default="")
    trade_log_csv: str = field(default="")
    snapshots_dir: str = field(default="")

    @classmethod
    def build(cls) -> "Paths":
        root = os.path.dirname(os.path.abspath(__file__))
        data_dir = os.path.join(root, "data")
        decisions_dir = os.path.join(data_dir, "decisions")
        evaluation_dir = os.path.join(data_dir, "evaluation")
        logs_dir = os.path.join(root, "logs")
        snapshots_dir = os.path.join(logs_dir, "snapshots")
        for d in (data_dir, decisions_dir, evaluation_dir, logs_dir, snapshots_dir):
            os.makedirs(d, exist_ok=True)
        return cls(
            root=root,
            data_dir=data_dir,
            flow_contracts_csv=os.path.join(data_dir, "flow_contracts.csv"),
            flow_cache_json=os.path.join(data_dir, "flow_cache.json"),
            cost_log_csv=os.path.join(data_dir, "cost_log.csv"),
            decisions_dir=decisions_dir,
            decisions_jsonl=os.path.join(decisions_dir, "decisions.jsonl"),
            closed_trades_jsonl=os.path.join(data_dir, "closed_trades.jsonl"),
            evaluation_dir=evaluation_dir,
            evaluation_latest_json=os.path.join(evaluation_dir, "latest.json"),
            evaluation_summary_txt=os.path.join(evaluation_dir, "summary.txt"),
            logs_dir=logs_dir,
            trade_log_csv=os.path.join(logs_dir, "trade_log.csv"),
            snapshots_dir=snapshots_dir,
        )


# ---------------------------------------------------------------------------
# Aggregate config object
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Config:
    risk: RiskConfig = field(default_factory=RiskConfig)
    model: ModelConfig = field(default_factory=ModelConfig)
    cost: CostConfig = field(default_factory=CostConfig)
    midday: MiddayConfig = field(default_factory=MiddayConfig)
    evaluation: EvalConfig = field(default_factory=EvalConfig)
    universe: UniverseConfig = field(default_factory=UniverseConfig)
    flow: FlowConfig = field(default_factory=FlowConfig)
    paths: Paths = field(default_factory=Paths.build)
    # Global kill switch. If True, the bot reads/reports but places no new orders.
    halt_trading: bool = False
    # If True, run analysis + sizing but do NOT submit orders to Alpaca.
    dry_run: bool = False


def get_config() -> Config:
    return Config()
