"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { ApiResponse } from "./types";

export interface PollState<T> {
  data: T | null;
  error: string | null;
  loading: boolean;
}

/**
 * Fetch one of our own /api routes and re-poll on an interval.
 * Errors are surfaced per-section; a failed refresh keeps the last good data.
 */
export function usePolling<T>(
  path: string,
  intervalMs: number,
  onUpdated?: () => void
): PollState<T> {
  const [state, setState] = useState<PollState<T>>({
    data: null,
    error: null,
    loading: true,
  });
  const onUpdatedRef = useRef(onUpdated);
  onUpdatedRef.current = onUpdated;

  const fetchOnce = useCallback(async () => {
    try {
      const res = await fetch(path, { cache: "no-store" });
      const body = (await res.json()) as ApiResponse<T>;
      if (body.status === "ok") {
        setState({ data: body.data, error: null, loading: false });
      } else {
        setState((s) => ({ ...s, error: body.error, loading: false }));
      }
    } catch (err) {
      setState((s) => ({
        ...s,
        error: err instanceof Error ? err.message : "Request failed",
        loading: false,
      }));
    } finally {
      onUpdatedRef.current?.();
    }
  }, [path]);

  useEffect(() => {
    fetchOnce();
    const id = setInterval(fetchOnce, intervalMs);
    return () => clearInterval(id);
  }, [fetchOnce, intervalMs]);

  return state;
}

/**
 * Fetch a static JSON file from /public (e.g. decisions.json published by CI).
 * Returns the parsed value or null. Tolerates a missing file (404) by returning
 * null without surfacing an error — the feature is optional.
 */
export function usePublicJson<T>(path: string, intervalMs: number): T | null {
  const [data, setData] = useState<T | null>(null);

  const fetchOnce = useCallback(async () => {
    try {
      const res = await fetch(path, { cache: "no-store" });
      if (!res.ok) {
        setData(null);
        return;
      }
      setData((await res.json()) as T);
    } catch {
      setData(null);
    }
  }, [path]);

  useEffect(() => {
    fetchOnce();
    const id = setInterval(fetchOnce, intervalMs);
    return () => clearInterval(id);
  }, [fetchOnce, intervalMs]);

  return data;
}
