/** Shared types for the dashboard (client + server). No secrets here. */

export interface Account {
  account_number: string;
  status: string;
  equity: string;
  last_equity: string;
  buying_power: string;
  cash: string;
  portfolio_value: string;
  currency: string;
}

export interface Position {
  symbol: string;
  qty: string;
  side: string;
  avg_entry_price: string;
  current_price: string | null;
  market_value: string | null;
  cost_basis: string;
  unrealized_pl: string | null;
  unrealized_plpc: string | null;
}

export interface OrderLeg {
  id: string;
  client_order_id?: string | null;
  symbol: string;
  side: string;
  type: string;
  order_type?: string;
  qty: string | null;
  status: string;
  filled_avg_price: string | null;
  submitted_at: string;
  limit_price: string | null;
  stop_price: string | null;
  order_class: string;
  legs?: OrderLeg[] | null;
}

/** A stored decision record (written by the bot, published to public/decisions.json). */
export interface DecisionRecord {
  id: string;                 // == client_order_id
  timestamp: string;
  mode: string;               // open | midday
  model: string;
  reasoning_effort: string;
  ticker: string;
  action: string;
  direction: string;
  confidence: number;
  rationale: string;
  entry_price: number;
  qty: number;
  stop_price: number;
  target_price: number;
  flow_signal: Record<string, unknown> | null;
  technicals: Record<string, unknown> | null;
  market_context: { macro: Record<string, unknown> | null; summary: string } | null;
  client_order_id: string;
  order_status: string;
  alpaca_order_id: string | null;
}

/** Options-flow scan output (published to public/flow-cache.json by CI). */
export interface FlowContract {
  underlying: string;
  symbol: string;
  type: string;              // call | put
  strike: number;
  expiry: string;
  dte: number;
  contract_price: number;
  volume: number;
  open_interest: number;
  vol_oi_ratio: number;
  notional: number;
  moneyness: number;
  aggression: number | null;
  is_spec_otm_call: boolean;
  implied_volatility: number | null;
  composite_score: number;
}

export interface FlowSignalRow {
  ticker: string;
  direction: string;         // bullish | bearish
  composite_score: number;
  top_contract: { symbol: string; type: string; strike: number; expiry: string };
  vol_oi_ratio: number;
  notional: number;
  aggression: number | null;
  call_put_notional_ratio: number;
  iv: number | null;
  rationale: string;
}

export interface FlowCache {
  generated_at: string;
  thresholds: {
    MIN_CONTRACT_VOLUME: number;
    MIN_VOL_OI_RATIO: number;
    MIN_NOTIONAL_USD: number;
    DTE_MIN: number;
    DTE_MAX: number;
    MONEYNESS_MAX: number;
    AGGRESSION_BUY: number;
    weights: Record<string, number>;
  };
  signals_ranked: FlowSignalRow[];
  qualifying_contracts_ranked: FlowContract[];
}

export interface PortfolioHistory {
  timestamp: number[];
  equity: (number | null)[];
  profit_loss: (number | null)[];
  profit_loss_pct: (number | null)[];
  base_value: number;
  timeframe: string;
}

/** Standard shape returned by every /api route. */
export type ApiResponse<T> =
  | { status: "ok"; data: T }
  | { status: "error"; error: string };

/** Evaluation output (published to public/evaluation.json by CI). */
export interface BreakdownRow {
  key: string;
  count: number;
  win_rate: number;
  avg_return: number;
  total_pnl: number;
  meaningful: boolean;
}

export interface CalibrationRow {
  bucket: string;
  midpoint: number;
  count: number;
  win_rate: number | null;
  calibration_gap: number | null;
  meaningful: boolean;
}

export interface Evaluation {
  generated_at: string;
  window: { start: string | null; end: string | null };
  min_sample: number;
  benchmark_note: string;
  primary_benchmark: string;
  overall: {
    trade_count: number;
    total_return?: number;
    max_drawdown?: number;
    win_rate?: number;
    avg_win?: number;
    avg_loss?: number;
    profit_factor?: number | null;
    expectancy_per_trade?: number;
    total_realized_pnl?: number;
    spy_return?: number | null;
    qqq_return?: number | null;
    excess_vs_spy?: number | null;
    excess_vs_qqq?: number | null;
  };
  equity_curves: {
    dates: string[];
    strategy: (number | null)[];
    [benchmark: string]: (number | null)[] | string[];
  };
  breakdowns: {
    signal_type: BreakdownRow[];
    run_mode: BreakdownRow[];
    confidence_bucket: BreakdownRow[];
    sector: BreakdownRow[];
  };
  calibration: CalibrationRow[];
}
