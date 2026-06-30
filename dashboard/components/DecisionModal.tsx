"use client";

import { useEffect } from "react";
import type { DecisionRecord } from "@/lib/types";
import { usd, pct, num } from "@/lib/format";

interface Props {
  open: boolean;
  title: string;
  record: DecisionRecord | null;
  onClose: () => void;
}

function Row({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex justify-between gap-4 py-1 text-sm">
      <span className="text-gray-500">{label}</span>
      <span className="text-right font-mono text-gray-200">{value}</span>
    </div>
  );
}

function fmtVal(v: unknown): string {
  if (v === null || v === undefined) return "—";
  if (typeof v === "number") return Number.isInteger(v) ? String(v) : v.toFixed(2);
  return String(v);
}

export default function DecisionModal({ open, title, record, onClose }: Props) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    if (open) document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;

  const flow = (record?.flow_signal ?? {}) as Record<string, unknown>;
  const tech = (record?.technicals ?? {}) as Record<string, unknown>;
  const macro = (record?.market_context?.macro ?? {}) as Record<string, unknown>;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
      onClick={onClose}
    >
      <div
        className="scroll-thin max-h-[85vh] w-full max-w-2xl overflow-auto rounded-2xl border border-ink-700 bg-ink-900 p-6 shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-4 flex items-start justify-between">
          <div>
            <h2 className="text-xl font-bold text-gray-100">{title}</h2>
            <p className="text-xs text-gray-500">Trade decision record</p>
          </div>
          <button
            onClick={onClose}
            className="rounded-lg px-2 py-1 text-gray-400 hover:bg-ink-700 hover:text-gray-200"
            aria-label="Close"
          >
            ✕
          </button>
        </div>

        {!record ? (
          <div className="rounded-lg border border-ink-700 bg-ink-850 px-4 py-6 text-center text-sm text-gray-400">
            No decision record for this trade.
            <div className="mt-1 text-xs text-gray-600">
              (Manual trade, or placed before reasoning capture was enabled.)
            </div>
          </div>
        ) : (
          <div className="space-y-5">
            {/* Tags */}
            <div className="flex flex-wrap gap-2">
              <span className="rounded bg-accent/20 px-2 py-0.5 text-xs uppercase text-accent">
                {record.action}
              </span>
              <span className="rounded bg-ink-700 px-2 py-0.5 text-xs uppercase text-gray-300">
                {record.mode}
              </span>
              <span className="rounded bg-ink-700 px-2 py-0.5 text-xs text-gray-300">
                {record.model}
              </span>
              <span className="rounded bg-ink-700 px-2 py-0.5 text-xs text-gray-300">
                effort: {record.reasoning_effort}
              </span>
              <span
                className={`rounded px-2 py-0.5 text-xs ${
                  record.direction === "bullish"
                    ? "bg-profit/20 text-profit"
                    : record.direction === "bearish"
                    ? "bg-loss/20 text-loss"
                    : "bg-ink-700 text-gray-300"
                }`}
              >
                {record.direction}
              </span>
            </div>

            {/* Rationale */}
            <div>
              <h3 className="mb-1 text-xs font-semibold uppercase tracking-wider text-gray-500">
                Rationale
              </h3>
              <p className="rounded-lg bg-ink-850 p-3 text-sm leading-relaxed text-gray-200">
                {record.rationale || "—"}
              </p>
              <p className="mt-1 text-[11px] text-gray-600">
                Note: a reasoning model&apos;s raw chain-of-thought isn&apos;t returned by the
                API; this written rationale plus the signal context below is the explanation.
              </p>
            </div>

            <div className="grid grid-cols-1 gap-5 sm:grid-cols-2">
              {/* Trade params */}
              <div>
                <h3 className="mb-1 text-xs font-semibold uppercase tracking-wider text-gray-500">
                  Trade
                </h3>
                <Row label="Confidence" value={pct(record.confidence)} />
                <Row label="Entry" value={usd(record.entry_price)} />
                <Row label="Qty" value={record.qty} />
                <Row label="Stop" value={usd(record.stop_price)} />
                <Row label="Target" value={usd(record.target_price)} />
                <Row label="Status" value={record.order_status} />
              </div>

              {/* Technicals at decision time */}
              <div>
                <h3 className="mb-1 text-xs font-semibold uppercase tracking-wider text-gray-500">
                  Technicals @ decision
                </h3>
                <Row label="Price" value={fmtVal(tech.last_price)} />
                <Row label="SMA20 / 50" value={`${fmtVal(tech.sma20)} / ${fmtVal(tech.sma50)}`} />
                <Row label="RSI-14" value={fmtVal(tech.rsi14)} />
                <Row label="vs 52w low" value={fmtVal(tech.pct_from_52w_low)} />
                <Row label="vs 52w high" value={fmtVal(tech.pct_from_52w_high)} />
                <Row label="Trend" value={fmtVal(tech.trend)} />
              </div>
            </div>

            {/* Flow signal */}
            {record.flow_signal && (
              <div>
                <h3 className="mb-1 text-xs font-semibold uppercase tracking-wider text-gray-500">
                  Options-flow signal
                </h3>
                <Row label="Composite score" value={fmtVal(flow.composite_score)} />
                <Row label="Vol/OI" value={fmtVal(flow.vol_oi_ratio)} />
                <Row label="Aggression" value={fmtVal(flow.aggression)} />
                <Row label="Call/Put notional" value={fmtVal(flow.call_put_notional_ratio)} />
                <Row label="IV" value={fmtVal(flow.iv)} />
                {flow.top_contract ? (
                  <Row
                    label="Top contract"
                    value={fmtVal((flow.top_contract as Record<string, unknown>).symbol)}
                  />
                ) : null}
              </div>
            )}

            {/* Market context */}
            <div>
              <h3 className="mb-1 text-xs font-semibold uppercase tracking-wider text-gray-500">
                Market context
              </h3>
              <Row label="VIX / regime" value={`${fmtVal(macro.vix)} (${fmtVal(macro.regime)})`} />
              <p className="mt-2 rounded-lg bg-ink-850 p-3 text-xs leading-relaxed text-gray-400">
                {record.market_context?.summary || "—"}
              </p>
            </div>

            <div className="border-t border-ink-800 pt-3 text-[11px] text-gray-600">
              {new Date(record.timestamp).toLocaleString()} · order{" "}
              {record.alpaca_order_id ?? "—"} · coid {record.client_order_id}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
