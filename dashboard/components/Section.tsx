"use client";

import { ReactNode } from "react";

interface SectionProps {
  title: string;
  loading?: boolean;
  error?: string | null;
  children: ReactNode;
  right?: ReactNode;
}

export default function Section({
  title,
  loading,
  error,
  children,
  right,
}: SectionProps) {
  return (
    <section className="rounded-2xl border border-ink-700 bg-ink-900/60 p-5 shadow-lg">
      <div className="mb-4 flex items-center justify-between">
        <h2 className="text-sm font-semibold uppercase tracking-wider text-gray-400">
          {title}
        </h2>
        {right}
      </div>
      {error ? (
        <div className="rounded-lg border border-loss/40 bg-loss/10 px-4 py-3 text-sm text-loss">
          {error}
        </div>
      ) : loading ? (
        <div className="flex items-center gap-2 py-6 text-sm text-gray-500">
          <span className="h-2 w-2 animate-pulse rounded-full bg-accent" />
          Loading…
        </div>
      ) : (
        children
      )}
    </section>
  );
}
