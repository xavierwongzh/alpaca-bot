"use client";

import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type {
  Evaluation,
  BreakdownRow,
  CalibrationRow,
} from "@/lib/types";
import { pct, usd, signedUsd, pnlColor } from "@/lib/format";
import StatCard from "./StatCard";
import Section from "./Section";

function fmtPct(v: number | null | undefined): string {
  return v === null || v === undefined ? "—" : pct(v);
}

function EquityOverlay({ ev }: { ev: Evaluation }) {
  const ec = ev.equity_curves;
  const dates = (ec.dates as string[]) ?? [];
  const benches = Object.keys(ec).filter((k) => k !== "dates" && k !== "strategy");
  const data = dates.map((d, i) => {
    const row: Record<string, number | string | null> = {
      date: d,
      Strategy: (ec.strategy as (number | null)[])[i] ?? null,
    };
    for (const b of benches) row[b] = (ec[b] as (number | null)[])[i] ?? null;
    return row;
  });
  const colors: Record<string, string> = { Strategy: "#6366f1", SPY: "#9ca3af", QQQ: "#22c55e" };

  if (!data.length) {
    return <div className="py-6 text-sm text-gray-500">No equity history yet.</div>;
  }
  return (
    <div className="h-72 w-full">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data} margin={{ top: 8, right: 8, left: 8, bottom: 0 }}>
          <CartesianGrid stroke="#1b1f2a" />
          <XAxis
            dataKey="date"
            stroke="#4b5563"
            tick={{ fill: "#6b7280", fontSize: 11 }}
            minTickGap={40}
            tickFormatter={(d) => String(d).slice(5)}
          />
          <YAxis
            stroke="#4b5563"
            tick={{ fill: "#6b7280", fontSize: 11 }}
            width={42}
            domain={["auto", "auto"]}
            tickFormatter={(v) => String(Math.round(v as number))}
          />
          <Tooltip
            contentStyle={{ background: "#151823", border: "1px solid #323848", borderRadius: 8, fontSize: 12 }}
            labelStyle={{ color: "#9ca3af" }}
            formatter={(v) => [(v as number)?.toFixed(1), ""]}
          />
          <Legend wrapperStyle={{ fontSize: 12 }} />
          <Line type="monotone" dataKey="Strategy" stroke={colors.Strategy} strokeWidth={2.5} dot={false} isAnimationActive={false} />
          {benches.map((b) => (
            <Line key={b} type="monotone" dataKey={b} stroke={colors[b] ?? "#888"} strokeWidth={1.5} strokeDasharray="4 3" dot={false} isAnimationActive={false} />
          ))}
        </LineChart>
      </ResponsiveContainer>
      <p className="mt-1 text-center text-[11px] text-gray-600">
        Normalized to 100 at window start. {ev.benchmark_note}
      </p>
    </div>
  );
}

function BreakdownTable({ title, rows, minSample }: { title: string; rows: BreakdownRow[]; minSample: number }) {
  return (
    <div>
      <h4 className="mb-2 text-xs font-semibold uppercase tracking-wider text-gray-500">{title}</h4>
      <div className="scroll-thin overflow-x-auto">
        <table className="w-full min-w-[360px] text-sm">
          <thead>
            <tr className="border-b border-ink-700 text-left text-[11px] uppercase tracking-wider text-gray-500">
              <th className="py-1.5 pr-3 font-medium">Bucket</th>
              <th className="py-1.5 pr-3 text-right font-medium">N</th>
              <th className="py-1.5 pr-3 text-right font-medium">Win%</th>
              <th className="py-1.5 pr-3 text-right font-medium">Avg Ret</th>
              <th className="py-1.5 text-right font-medium">P&L</th>
            </tr>
          </thead>
          <tbody className="font-mono">
            {rows.length === 0 && (
              <tr><td colSpan={5} className="py-3 text-gray-600">No trades yet.</td></tr>
            )}
            {rows.map((r) => (
              <tr
                key={r.key}
                className={`border-b border-ink-800 last:border-0 ${r.meaningful ? "" : "opacity-40"}`}
                title={r.meaningful ? "" : `Only ${r.count} trades (< ${minSample}); not yet meaningful`}
              >
                <td className="py-1.5 pr-3 font-sans text-gray-200">
                  {r.key}
                  {!r.meaningful && (
                    <span className="ml-1.5 rounded bg-ink-700 px-1 text-[9px] uppercase text-gray-500">low n</span>
                  )}
                </td>
                <td className="py-1.5 pr-3 text-right text-gray-400">{r.count}</td>
                <td className="py-1.5 pr-3 text-right text-gray-300">{pct(r.win_rate)}</td>
                <td className={`py-1.5 pr-3 text-right ${pnlColor(r.avg_return)}`}>{pct(r.avg_return)}</td>
                <td className={`py-1.5 text-right ${pnlColor(r.total_pnl)}`}>{signedUsd(r.total_pnl)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function CalibrationTable({ rows, minSample }: { rows: CalibrationRow[]; minSample: number }) {
  return (
    <div>
      <h4 className="mb-2 text-xs font-semibold uppercase tracking-wider text-gray-500">
        Confidence calibration
      </h4>
      <div className="scroll-thin overflow-x-auto">
        <table className="w-full min-w-[420px] text-sm">
          <thead>
            <tr className="border-b border-ink-700 text-left text-[11px] uppercase tracking-wider text-gray-500">
              <th className="py-1.5 pr-3 font-medium">Confidence</th>
              <th className="py-1.5 pr-3 text-right font-medium">Expected</th>
              <th className="py-1.5 pr-3 text-right font-medium">Realized Win%</th>
              <th className="py-1.5 pr-3 text-right font-medium">Gap</th>
              <th className="py-1.5 text-right font-medium">N</th>
            </tr>
          </thead>
          <tbody className="font-mono">
            {rows.map((r) => (
              <tr
                key={r.bucket}
                className={`border-b border-ink-800 last:border-0 ${r.meaningful ? "" : "opacity-40"}`}
                title={r.meaningful ? "" : `Only ${r.count} trades (< ${minSample}); not yet meaningful`}
              >
                <td className="py-1.5 pr-3 font-sans text-gray-200">{r.bucket}</td>
                <td className="py-1.5 pr-3 text-right text-gray-400">{pct(r.midpoint, true)}</td>
                <td className="py-1.5 pr-3 text-right text-gray-300">
                  {r.win_rate === null ? "—" : pct(r.win_rate)}
                </td>
                <td className={`py-1.5 pr-3 text-right ${r.calibration_gap == null ? "text-gray-500" : pnlColor(r.calibration_gap)}`}>
                  {r.calibration_gap == null ? "—" : pct(r.calibration_gap)}
                </td>
                <td className="py-1.5 text-right text-gray-400">
                  {r.count}
                  {!r.meaningful && r.count > 0 && (
                    <span className="ml-1.5 rounded bg-ink-700 px-1 text-[9px] uppercase text-gray-500">low n</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="mt-1 text-[11px] text-gray-600">
        If 0.8-confidence trades don&apos;t win near 80% of the time, the confidence field is noise.
      </p>
    </div>
  );
}

export default function PerformanceView({ ev }: { ev: Evaluation | null }) {
  if (!ev) {
    return (
      <Section title="Performance">
        <div className="py-6 text-sm text-gray-500">
          No evaluation published yet. Metrics appear once the bot has closed trades
          and the daily run has published <code>evaluation.json</code>.
        </div>
      </Section>
    );
  }

  const o = ev.overall;
  const n = o.trade_count ?? 0;
  const lowSample = n < ev.min_sample;

  return (
    <div className="space-y-6">
      {lowSample && (
        <div className="rounded-lg border border-yellow-500/40 bg-yellow-500/10 px-4 py-3 text-sm text-yellow-300">
          Only <b>{n}</b> closed trade{n === 1 ? "" : "s"} so far — below the {ev.min_sample}-trade
          threshold. Treat every breakdown below as <b>not yet meaningful</b>.
        </div>
      )}

      {/* Metric cards */}
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-5">
        <StatCard label="Total Return" value={fmtPct(o.total_return)} subColor={pnlColor(o.total_return ?? 0)} />
        <StatCard
          label={`Excess vs ${ev.primary_benchmark}`}
          value={fmtPct(o.excess_vs_qqq)}
          subColor={pnlColor(o.excess_vs_qqq ?? 0)}
        />
        <StatCard label="Win Rate" value={n ? pct(o.win_rate) : "—"} />
        <StatCard
          label="Profit Factor"
          value={o.profit_factor == null ? (n ? "∞" : "—") : o.profit_factor.toFixed(2)}
        />
        <StatCard label="Max Drawdown" value={fmtPct(o.max_drawdown)} subColor="text-loss" />
      </div>

      {/* Equity overlay */}
      <Section title="Strategy vs Buy-and-Hold">
        <EquityOverlay ev={ev} />
      </Section>

      {/* Breakdowns */}
      <Section title="Per-signal breakdowns">
        <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
          <BreakdownTable title="Signal type" rows={ev.breakdowns.signal_type} minSample={ev.min_sample} />
          <BreakdownTable title="Run mode" rows={ev.breakdowns.run_mode} minSample={ev.min_sample} />
          <BreakdownTable title="Sector" rows={ev.breakdowns.sector} minSample={ev.min_sample} />
          <BreakdownTable title="Confidence bucket" rows={ev.breakdowns.confidence_bucket} minSample={ev.min_sample} />
          <BreakdownTable title="Exit reason" rows={ev.breakdowns.exit_reason ?? []} minSample={ev.min_sample} />
        </div>
      </Section>

      {/* Calibration */}
      <Section title="Calibration">
        <CalibrationTable rows={ev.calibration} minSample={ev.min_sample} />
      </Section>
    </div>
  );
}
