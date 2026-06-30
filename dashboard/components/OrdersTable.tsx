import type { OrderLeg } from "@/lib/types";
import { usd, timeAgo, num } from "@/lib/format";

interface Props {
  orders: OrderLeg[];
  onRowClick?: (order: OrderLeg) => void;
}

type Row = { order: OrderLeg; leg: boolean; tag: string | null };

function legTag(o: OrderLeg, isLeg: boolean): string | null {
  const t = (o.order_type || o.type || "").toLowerCase();
  if (isLeg) {
    if (t.includes("stop")) return "stop";
    if (t.includes("limit")) return "target";
    return "leg";
  }
  if (o.order_class === "bracket") return "entry";
  return null;
}

function flatten(orders: OrderLeg[]): Row[] {
  const rows: Row[] = [];
  for (const o of orders) {
    rows.push({ order: o, leg: false, tag: legTag(o, false) });
    for (const child of o.legs ?? []) {
      rows.push({ order: child, leg: true, tag: legTag(child, true) });
    }
  }
  return rows;
}

const tagStyles: Record<string, string> = {
  entry: "bg-accent/20 text-accent",
  stop: "bg-loss/20 text-loss",
  target: "bg-profit/20 text-profit",
  leg: "bg-ink-700 text-gray-400",
};

const statusStyles: Record<string, string> = {
  filled: "text-profit",
  partially_filled: "text-profit",
  new: "text-gray-300",
  accepted: "text-gray-300",
  held: "text-gray-400",
  canceled: "text-gray-500",
  expired: "text-gray-500",
  rejected: "text-loss",
};

export default function OrdersTable({ orders, onRowClick }: Props) {
  if (!orders.length) {
    return <div className="py-6 text-sm text-gray-500">No recent orders.</div>;
  }
  const rows = flatten(orders);

  return (
    <div className="scroll-thin max-h-[420px] overflow-auto">
      <table className="w-full min-w-[720px] text-sm">
        <thead className="sticky top-0 bg-ink-900">
          <tr className="border-b border-ink-700 text-left text-xs uppercase tracking-wider text-gray-500">
            <th className="py-2 pr-4 font-medium">Symbol</th>
            <th className="py-2 pr-4 font-medium">Side</th>
            <th className="py-2 pr-4 font-medium">Type</th>
            <th className="py-2 pr-4 text-right font-medium">Qty</th>
            <th className="py-2 pr-4 font-medium">Status</th>
            <th className="py-2 pr-4 text-right font-medium">Filled Avg</th>
            <th className="py-2 text-right font-medium">Submitted</th>
          </tr>
        </thead>
        <tbody className="font-mono">
          {rows.map(({ order, leg, tag }) => {
            const status = (order.status || "").toLowerCase();
            return (
              <tr
                key={order.id}
                onClick={() => onRowClick?.(order)}
                className={`border-b border-ink-800 last:border-0 hover:bg-ink-850/50 ${
                  leg ? "bg-ink-900/40" : ""
                } ${onRowClick ? "cursor-pointer" : ""}`}
              >
                <td className="py-2 pr-4 font-sans">
                  <span className={leg ? "pl-4 text-gray-400" : "font-semibold text-gray-100"}>
                    {leg ? "↳ " : ""}
                    {order.symbol}
                  </span>
                  {tag && (
                    <span
                      className={`ml-2 rounded px-1.5 py-0.5 text-[10px] uppercase ${
                        tagStyles[tag] ?? tagStyles.leg
                      }`}
                    >
                      {tag}
                    </span>
                  )}
                </td>
                <td className="py-2 pr-4 uppercase text-gray-300">{order.side}</td>
                <td className="py-2 pr-4 text-gray-400">
                  {order.order_type || order.type}
                </td>
                <td className="py-2 pr-4 text-right text-gray-300">
                  {order.qty ? num(order.qty).toLocaleString() : "—"}
                </td>
                <td
                  className={`py-2 pr-4 ${statusStyles[status] ?? "text-gray-300"}`}
                >
                  {order.status}
                </td>
                <td className="py-2 pr-4 text-right text-gray-300">
                  {order.filled_avg_price ? usd(order.filled_avg_price) : "—"}
                </td>
                <td className="py-2 text-right text-gray-400">
                  {timeAgo(order.submitted_at)}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
