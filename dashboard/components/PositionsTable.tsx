import type { Position } from "@/lib/types";
import { usd, signedUsd, pct, pnlColor, num } from "@/lib/format";

interface Props {
  positions: Position[];
  onRowClick?: (symbol: string) => void;
}

export default function PositionsTable({ positions, onRowClick }: Props) {
  if (!positions.length) {
    return <div className="py-6 text-sm text-gray-500">No open positions.</div>;
  }

  return (
    <div className="scroll-thin overflow-x-auto">
      <table className="w-full min-w-[640px] text-sm">
        <thead>
          <tr className="border-b border-ink-700 text-left text-xs uppercase tracking-wider text-gray-500">
            <th className="py-2 pr-4 font-medium">Symbol</th>
            <th className="py-2 pr-4 text-right font-medium">Qty</th>
            <th className="py-2 pr-4 text-right font-medium">Avg Entry</th>
            <th className="py-2 pr-4 text-right font-medium">Price</th>
            <th className="py-2 pr-4 text-right font-medium">Mkt Value</th>
            <th className="py-2 pr-4 text-right font-medium">Unrealized P&L</th>
            <th className="py-2 text-right font-medium">P&L %</th>
          </tr>
        </thead>
        <tbody className="font-mono">
          {positions.map((p) => {
            const color = pnlColor(p.unrealized_pl);
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
