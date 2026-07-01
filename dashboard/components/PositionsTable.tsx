import type { Position } from "@/lib/types";
import { usd, signedUsd, pct, pnlColor, num } from "@/lib/format";

/** Live protective levels for a symbol, derived from open OCO sell legs. */
export interface Protection {
  stop?: number;
  target?: number;
}

interface Props {
  positions: Position[];
  protection?: Map<string, Protection>;
  onRowClick?: (symbol: string) => void;
}

/** A protective level: price on top, % distance from current below. */
function ProtectionCell({
  level,
  current,
  tone,
}: {
  level: number | undefined;
  current: number;
  tone: string;
}) {
  if (level === undefined || !Number.isFinite(level)) {
    return <span className="text-gray-600">—</span>;
  }
  const dist =
    Number.isFinite(current) && current > 0 ? (level - current) / current : NaN;
  return (
    <div className="leading-tight">
      <div className={tone}>{usd(level)}</div>
      {Number.isFinite(dist) && (
        <div className="text-[10px] text-gray-500">{pct(dist)}</div>
      )}
    </div>
  );
}

export default function PositionsTable({ positions, protection, onRowClick }: Props) {
  if (!positions.length) {
    return <div className="py-6 text-sm text-gray-500">No open positions.</div>;
  }

  return (
    <div className="scroll-thin overflow-x-auto">
      <table className="w-full min-w-[820px] text-sm">
        <thead>
          <tr className="border-b border-ink-700 text-left text-xs uppercase tracking-wider text-gray-500">
            <th className="py-2 pr-4 font-medium">Symbol</th>
            <th className="py-2 pr-4 text-right font-medium">Qty</th>
            <th className="py-2 pr-4 text-right font-medium">Avg Entry</th>
            <th className="py-2 pr-4 text-right font-medium">Price</th>
            <th className="py-2 pr-4 text-right font-medium">Stop →</th>
            <th className="py-2 pr-4 text-right font-medium">Target →</th>
            <th className="py-2 pr-4 text-right font-medium">Mkt Value</th>
            <th className="py-2 pr-4 text-right font-medium">Unrealized P&L</th>
            <th className="py-2 text-right font-medium">P&L %</th>
          </tr>
        </thead>
        <tbody className="font-mono">
          {positions.map((p) => {
            const color = pnlColor(p.unrealized_pl);
            const prot = protection?.get(p.symbol);
            const current = num(p.current_price);
            return (
              <tr
                key={p.symbol}
                onClick={() => onRowClick?.(p.symbol)}
                className={`border-b border-ink-800 last:border-0 hover:bg-ink-850/50 ${
                  onRowClick ? "cursor-pointer" : ""
                }`}
              >
                <td className="py-2.5 pr-4 font-sans font-semibold text-gray-100">
                  {p.symbol}
                  <span className="ml-2 rounded bg-ink-700 px-1.5 py-0.5 text-[10px] uppercase text-gray-400">
                    {p.side}
                  </span>
                </td>
                <td className="py-2.5 pr-4 text-right text-gray-300">
                  {num(p.qty).toLocaleString()}
                </td>
                <td className="py-2.5 pr-4 text-right text-gray-300">
                  {usd(p.avg_entry_price)}
                </td>
                <td className="py-2.5 pr-4 text-right text-gray-300">
                  {usd(p.current_price)}
                </td>
                <td className="py-2.5 pr-4 text-right">
                  <ProtectionCell level={prot?.stop} current={current} tone="text-loss" />
                </td>
                <td className="py-2.5 pr-4 text-right">
                  <ProtectionCell level={prot?.target} current={current} tone="text-profit" />
                </td>
                <td className="py-2.5 pr-4 text-right text-gray-300">
                  {usd(p.market_value)}
                </td>
                <td className={`py-2.5 pr-4 text-right ${color}`}>
                  {signedUsd(p.unrealized_pl)}
                </td>
                <td className={`py-2.5 text-right ${color}`}>
                  {pct(p.unrealized_plpc)}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
