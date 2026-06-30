/** Display formatting helpers (client-safe, no secrets). */

export function num(v: string | number | null | undefined): number {
  if (v === null || v === undefined || v === "") return NaN;
  const n = typeof v === "number" ? v : parseFloat(v);
  return Number.isFinite(n) ? n : NaN;
}

export function usd(v: string | number | null | undefined, dp = 2): string {
  const n = num(v);
  if (!Number.isFinite(n)) return "—";
  return n.toLocaleString("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: dp,
    maximumFractionDigits: dp,
  });
}

export function signedUsd(v: string | number | null | undefined): string {
  const n = num(v);
  if (!Number.isFinite(n)) return "—";
  const s = usd(Math.abs(n));
  return n >= 0 ? `+${s}` : `-${s}`;
}

export function pct(v: string | number | null | undefined, alreadyPct = false): string {
  const n = num(v);
  if (!Number.isFinite(n)) return "—";
  const value = alreadyPct ? n : n * 100;
  const sign = value >= 0 ? "+" : "";
  return `${sign}${value.toFixed(2)}%`;
}

/** green for positive, red for negative, muted for zero/NaN. */
export function pnlColor(v: string | number | null | undefined): string {
  const n = num(v);
  if (!Number.isFinite(n) || n === 0) return "text-gray-400";
  return n > 0 ? "text-profit" : "text-loss";
}

export function timeAgo(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleString("en-US", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}
