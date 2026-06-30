"use client";

import {
  Area,
  AreaChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { PortfolioHistory } from "@/lib/types";
import { usd } from "@/lib/format";

interface Props {
  history: PortfolioHistory | null;
}

export default function EquityChart({ history }: Props) {
  if (!history || !history.timestamp?.length) {
    return (
      <div className="flex h-64 items-center justify-center text-sm text-gray-500">
        No portfolio history yet.
      </div>
    );
  }

  const data = history.timestamp
    .map((ts, i) => ({
      t: ts * 1000,
      equity: history.equity[i],
    }))
    .filter((d) => d.equity !== null && Number.isFinite(d.equity as number));

  const values = data.map((d) => d.equity as number);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const pad = (max - min) * 0.1 || max * 0.02 || 1;
  const up = values.length > 1 && values[values.length - 1] >= values[0];
  const stroke = up ? "#22c55e" : "#ef4444";

  return (
    <div className="h-64 w-full">
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={data} margin={{ top: 8, right: 8, left: 8, bottom: 0 }}>
          <defs>
            <linearGradient id="equityFill" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={stroke} stopOpacity={0.25} />
              <stop offset="100%" stopColor={stroke} stopOpacity={0} />
            </linearGradient>
          </defs>
          <XAxis
            dataKey="t"
            type="number"
            domain={["dataMin", "dataMax"]}
            scale="time"
            tickFormatter={(t) =>
              new Date(t).toLocaleDateString("en-US", {
                month: "short",
                day: "numeric",
              })
            }
            stroke="#4b5563"
            tick={{ fill: "#6b7280", fontSize: 11 }}
            minTickGap={40}
          />
          <YAxis
            domain={[min - pad, max + pad]}
            tickFormatter={(v) => `$${Math.round(v / 1000)}k`}
            stroke="#4b5563"
            tick={{ fill: "#6b7280", fontSize: 11 }}
            width={48}
          />
          <Tooltip
            contentStyle={{
              background: "#151823",
              border: "1px solid #323848",
              borderRadius: 8,
              fontSize: 12,
            }}
            labelStyle={{ color: "#9ca3af" }}
            labelFormatter={(t) => new Date(t as number).toLocaleString()}
            formatter={(v) => [usd(v as number), "Equity"]}
          />
          <Area
            type="monotone"
            dataKey="equity"
            stroke={stroke}
            strokeWidth={2}
            fill="url(#equityFill)"
            dot={false}
            isAnimationActive={false}
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}
