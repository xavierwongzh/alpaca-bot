"use client";

import { useMemo } from "react";
import type { FlowCache, FlowContract, FlowSignalRow } from "@/lib/types";
import { usd, compactUsd, num } from "@/lib/format";
import Section from "./Section";

function typeBadge(t: string) {
  const isCall = t.toLowerCase() === "call";
  return (
    <span
      className={`rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase ${
        isCall ? "bg-profit/20 text-profit" : "bg-loss/20 text-loss"
      }`}
    >
      {t}
    </span>
  );
}

function dirBadge(d: string) {
  const bull = d.toLowerCase() === "bullish";
  return (
    <span
      className={`rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase ${
        bull ? "bg-profit/20 text-profit" : "bg-loss/20 text-loss"
      }`}
    >
      {d}
    </span>
  );
}

function ivPct(v: number | null): string {
  return v === null || v === undefined ? "—" : `${(v * 100).toFixed(0)}%`;
}

/** Per-ticker signal summary (compact context above the contract list). */
function SignalStrip({ signals }: { signals: FlowSignalRow[] }) {
  if (!signals.length) return null;
  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
      {signals.map((s) => (
        <div
          key={s.ticker}
          className="rounded-xl border border-ink-700 bg-ink-850/60 p-3"
        >
          <div className="flex items-center justify-between">
            <span className="font-semibold text-gray-100">{s.ticker}</span>
            {dirBadge(s.direction)}
          </div>
          <div className="mt-1 flex items-baseline justify-between text-xs text-gray-400">
            <span>
              score <span className="font-mono text-gray-200">{s.composite_score.toFixed(1)}</span>
            </span>
            <span>
              notional{" "}
              <span className="font-mono text-accent">{compactUsd(s.notional)}</span>
            </span>
          </div>
          <div className="mt-1 text-[11px] text-gray-500">
            C/P {s.call_put_notional_ratio.toFixed(1)}x · vol/OI {s.vol_oi_ratio.toFixed(1)} ·
            IV {ivPct(s.iv)}
          </div>
        </div>
      ))}
    </div>
  );
}

export default function FlowView({ flow }: { flow: FlowCache | null }) {
  // Sort qualifying contracts by notional, highest to lowest (defensive — the
  // published file is score-ranked, this view is explicitly notional-ranked).
  const contracts = useMemo<FlowContract[]>(() => {
    const rows = flow?.qualifying_contracts_ranked ?? [];
    return [...rows].sort((a, b) => num(b.notional) - num(a.notional));
  }, [flow]);

  if (!flow) {
    return (
      <Section title="Unusual Options Activity">
        <div className="py-6 text-sm text-gray-500">
          No flow scan published yet. This populates once the bot runs and publishes{" "}
          <code>flow-cache.json</code>.
        </div>
      </Section>
    );
  }

  const th = flow.thresholds;
  const generated = new Date(flow.generated_at);

  return (
    <div className="space-y-6">
      <Section
        title="Scan filters"
        right={
          <span className="text-xs text-gray-500">
            {Number.isNaN(generated.getTime())
              ? ""
              : `scanned ${generated.toLocaleString()}`}
          </span>
        }
      >
        <div className="flex flex-wrap gap-x-6 gap-y-1 text-xs text-gray-400">
          <span>min volume <span className="font-mono text-gray-200">{th.MIN_CONTRACT_VOLUME}</span></span>
          <span>min vol/OI <span className="font-mono text-gray-200">{th.MIN_VOL_OI_RATIO}</span></span>
          <span>min notional <span className="font-mono text-gray-200">{compactUsd(th.MIN_NOTIONAL_USD)}</span></span>
          <span>DTE <span className="font-mono text-gray-200">{th.DTE_MIN}–{th.DTE_MAX}</span></span>
          <span>±moneyness <span className="font-mono text-gray-200">{(th.MONEYNESS_MAX * 100).toFixed(0)}%</span></span>
        </div>
      </Section>

      {flow.signals_ranked?.length > 0 && (
        <Section title="Ticker signals">
          <SignalStrip signals={flow.signals_ranked} />
        </Section>
      )}

      <Section
        title="Unusual Options Activity"
        right={
          <span className="text-xs text-gray-500">
            {contracts.length} contracts · sorted by notional
          </span>
        }
      >
        {contracts.length === 0 ? (
          <div className="py-6 text-sm text-gray-500">
            No contracts qualified in the last scan.
          </div>
        ) : (
          <div className="scroll-thin max-h-[560px] overflow-auto">
            <table className="w-full min-w-[860px] text-sm">
              <thead className="sticky top-0 bg-ink-900">
                <tr className="border-b border-ink-700 text-left text-xs uppercase tracking-wider text-gray-500">
                  <th className="py-2 pr-4 font-medium">Underlying</th>
                  <th className="py-2 pr-4 font-medium">Type</th>
                  <th className="py-2 pr-4 text-right font-medium">Strike</th>
                  <th className="py-2 pr-4 font-medium">Expiry</th>
                  <th className="py-2 pr-4 text-right font-medium">Volume</th>
                  <th className="py-2 pr-4 text-right font-medium">OI</th>
                  <th className="py-2 pr-4 text-right font-medium">Vol/OI</th>
                  <th className="py-2 pr-4 text-right font-medium">Notional</th>
                  <th className="py-2 pr-4 text-right font-medium">Aggr</th>
                  <th className="py-2 pr-4 text-right font-medium">IV</th>
                  <th className="py-2 text-right font-medium">Score</th>
                </tr>
              </thead>
              <tbody className="font-mono">
                {contracts.map((c) => (
                  <tr
                    key={c.symbol}
                    className="border-b border-ink-800 last:border-0 hover:bg-ink-850/50"
                  >
                    <td className="py-2 pr-4 font-sans font-semibold text-gray-100">
                      {c.underlying}
                    </td>
                    <td className="py-2 pr-4">{typeBadge(c.type)}</td>
                    <td className="py-2 pr-4 text-right text-gray-300">{usd(c.strike)}</td>
                    <td className="py-2 pr-4 text-gray-400">
                      {c.expiry}
                      <span className="ml-1 text-[10px] text-gray-600">{c.dte}d</span>
                    </td>
                    <td className="py-2 pr-4 text-right text-gray-300">
                      {num(c.volume).toLocaleString()}
                    </td>
                    <td className="py-2 pr-4 text-right text-gray-400">
                      {num(c.open_interest).toLocaleString()}
                    </td>
                    <td className="py-2 pr-4 text-right text-gray-300">
                      {num(c.vol_oi_ratio).toFixed(1)}
                    </td>
                    <td className="py-2 pr-4 text-right font-semibold text-accent">
                      {compactUsd(c.notional)}
                    </td>
                    <td className="py-2 pr-4 text-right text-gray-300">
                      {c.aggression === null ? "—" : num(c.aggression).toFixed(2)}
                    </td>
                    <td className="py-2 pr-4 text-right text-gray-400">
                      {ivPct(c.implied_volatility)}
                    </td>
                    <td className="py-2 text-right text-gray-300">
                      {num(c.composite_score).toFixed(1)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Section>
    </div>
  );
}
