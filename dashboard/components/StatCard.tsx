interface StatCardProps {
  label: string;
  value: string;
  sub?: string;
  subColor?: string;
  loading?: boolean;
}

export default function StatCard({
  label,
  value,
  sub,
  subColor = "text-gray-400",
  loading,
}: StatCardProps) {
  return (
    <div className="rounded-2xl border border-ink-700 bg-ink-900/60 p-5 shadow-lg">
      <div className="text-xs font-medium uppercase tracking-wider text-gray-500">
        {label}
      </div>
      {loading ? (
        <div className="mt-3 h-7 w-28 animate-pulse rounded bg-ink-700" />
      ) : (
        <div className="mt-2 font-mono text-2xl font-semibold text-gray-100">
          {value}
        </div>
      )}
      {sub && !loading && (
        <div className={`mt-1 font-mono text-sm ${subColor}`}>{sub}</div>
      )}
    </div>
  );
}
