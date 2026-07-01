"use client";

import { useMemo, useState } from "react";
import Section from "@/components/Section";
import StatCard from "@/components/StatCard";
import EquityChart from "@/components/EquityChart";
import PositionsTable from "@/components/PositionsTable";
import OrdersTable from "@/components/OrdersTable";
import DecisionModal from "@/components/DecisionModal";
import PerformanceView from "@/components/PerformanceView";
import { usePolling, usePublicJson } from "@/lib/usePolling";
import type {
  Account,
  Position,
  OrderLeg,
  PortfolioHistory,
  DecisionRecord,
  Evaluation,
} from "@/lib/types";
import { usd, signedUsd, pct, pnlColor, num } from "@/lib/format";

const REFRESH_MS = 60_000; // 60s — modest, to stay within Alpaca rate limits

export default function DashboardPage() {
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  const markUpdated = () => setLastUpdated(new Date());

  const account = usePolling<Account>("/api/account", REFRESH_MS, markUpdated);
  const positions = usePolling<Position[]>("/api/positions", REFRESH_MS);
  const orders = usePolling<OrderLeg[]>("/api/orders", REFRESH_MS);
  const history = usePolling<PortfolioHistory>(
    "/api/portfolio-history",
    REFRESH_MS
  );
  const decisions = usePublicJson<DecisionRecord[]>("/decisions.json", REFRESH_MS);
  const evaluation = usePublicJson<Evaluation>("/evaluation.json", REFRESH_MS);
  const [tab, setTab] = useState<"account" | "performance">("account");

  // Join maps: orders -> by client_order_id (exact); positions -> most recent
  // open-mode entry per symbol.
  const byClientOrderId = useMemo(() => {
    const m = new Map<string, DecisionRecord>();
    for (const r of decisions ?? []) {
      if (r.client_order_id) m.set(r.client_order_id, r);
    }
    return m;
  }, [decisions]);

  // Most recent ENTRY (buy) per symbol, ANY run mode (open OR midday) — a
  // position can be opened by either pass, so we must not filter on mode.
  const latestEntryBySymbol = useMemo(() => {
    const m = new Map<string, DecisionRecord>();
    for (const r of decisions ?? []) {
      if (r.action !== "buy") continue;
      const prev = m.get(r.ticker);
      if (!prev || r.timestamp > prev.timestamp) m.set(r.ticker, r);
    }
    return m;
  }, [decisions]);

  const [modal, setModal] = useState<{
    open: boolean;
    title: string;
    record: DecisionRecord | null;
  }>({ open: false, title: "", record: null });

  const openForOrder = (o: OrderLeg) => {
    const rec = (o.client_order_id && byClientOrderId.get(o.client_order_id)) || null;
    setModal({ open: true, title: `${o.symbol} · order`, record: rec });
  };
  const openForPosition = (symbol: string) => {
    const rec = latestEntryBySymbol.get(symbol) ?? null;
    setModal({ open: true, title: `${symbol} · position`, record: rec });
  };
  const closeModal = () => setModal((s) => ({ ...s, open: false }));

  const a = account.data;
  const equity = num(a?.equity);
  const lastEquity = num(a?.last_equity);
  const dayPl =
    Number.isFinite(equity) && Number.isFinite(lastEquity)
      ? equity - lastEquity
      : NaN;
  const dayPlPct =
    Number.isFinite(dayPl) && lastEquity ? dayPl / lastEquity : NaN;

  return (
    <main className="mx-auto max-w-6xl px-4 py-8 sm:px-6 lg:px-8">
      {/* Header */}
      <div className="mb-8 flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold text-gray-100">
            Alpaca Paper Dashboard
          </h1>
          <p className="mt-1 text-sm text-gray-500">
            Read-only · {a?.account_number ? `account ${a.account_number}` : "paper account"}
            <span className="ml-2 rounded bg-accent/20 px-1.5 py-0.5 text-[10px] uppercase text-accent">
              paper
            </span>
          </p>
        </div>
        <div className="text-xs text-gray-500">
          {lastUpdated
            ? `Last updated ${lastUpdated.toLocaleTimeString()}`
            : "Loading…"}
          <span className="ml-2 text-gray-600">· refreshes every 60s</span>
        </div>
      </div>

      {/* Tabs */}
      <div className="mb-6 flex gap-1 border-b border-ink-700">
        {(["account", "performance"] as const).map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={`-mb-px border-b-2 px-4 py-2 text-sm font-medium capitalize transition ${
              tab === t
                ? "border-accent text-gray-100"
                : "border-transparent text-gray-500 hover:text-gray-300"
            }`}
          >
            {t}
          </button>
        ))}
      </div>

      {tab === "performance" ? (
        <PerformanceView ev={evaluation} />
      ) : (
        <>
      {/* Stat cards */}
      <div className="mb-6 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard
          label="Total Equity"
          value={usd(a?.equity)}
          loading={account.loading}
        />
        <StatCard
          label="Day P&L"
          value={Number.isFinite(dayPl) ? signedUsd(dayPl) : "—"}
          sub={Number.isFinite(dayPlPct) ? pct(dayPlPct) : undefined}
          subColor={pnlColor(dayPl)}
          loading={account.loading}
        />
        <StatCard
          label="Buying Power"
          value={usd(a?.buying_power)}
          loading={account.loading}
        />
        <StatCard
          label="Cash"
          value={usd(a?.cash)}
          loading={account.loading}
        />
      </div>

      {account.error && (
        <div className="mb-6 rounded-lg border border-loss/40 bg-loss/10 px-4 py-3 text-sm text-loss">
          Account error: {account.error}
        </div>
      )}

      {/* Equity curve */}
      <div className="mb-6">
        <Section
          title="Equity Curve"
          loading={history.loading && !history.data}
          error={history.error}
        >
          <EquityChart history={history.data} />
        </Section>
      </div>

      {/* Positions */}
      <div className="mb-6">
        <Section
          title="Open Positions"
          loading={positions.loading && !positions.data}
          error={positions.error}
          right={
            positions.data ? (
              <span className="text-xs text-gray-500">
                {positions.data.length} open
              </span>
            ) : null
          }
        >
          <PositionsTable
            positions={positions.data ?? []}
            onRowClick={openForPosition}
          />
        </Section>
      </div>

      {/* Orders */}
      <div className="mb-6">
        <Section
          title="Recent Orders"
          loading={orders.loading && !orders.data}
          error={orders.error}
        >
          <OrdersTable orders={orders.data ?? []} onRowClick={openForOrder} />
        </Section>
      </div>

      <p className="mb-6 text-center text-xs text-gray-600">
        Tip: click any position or order row to see the AI&apos;s reasoning for that trade.
      </p>
        </>
      )}

      <footer className="mt-8 text-center text-xs text-gray-600">
        Paper trading view · keys stay server-side · data via Alpaca
      </footer>

      <DecisionModal
        open={modal.open}
        title={modal.title}
        record={modal.record}
        onClose={closeModal}
      />
    </main>
  );
}
