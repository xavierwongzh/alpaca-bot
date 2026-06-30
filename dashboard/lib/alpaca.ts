/**
 * Server-side Alpaca REST helper.
 *
 * SECURITY: this module must only ever run on the server (inside route
 * handlers). It reads ALPACA_API_KEY / ALPACA_SECRET_KEY from process.env and
 * must never be imported into a client component. Keys are never returned to
 * the caller.
 */
import "server-only";

const DEFAULT_BASE = "https://paper-api.alpaca.markets";

export type AlpacaResult<T> =
  | { ok: true; status: number; data: T }
  | { ok: false; status: number; error: string };

function getBaseUrl(): string {
  return (process.env.ALPACA_BASE_URL || DEFAULT_BASE).replace(/\/+$/, "");
}

function getHeaders(): Record<string, string> {
  const key = process.env.ALPACA_API_KEY;
  const secret = process.env.ALPACA_SECRET_KEY;
  if (!key || !secret) {
    throw new Error(
      "Missing Alpaca credentials. Set ALPACA_API_KEY and ALPACA_SECRET_KEY in the environment."
    );
  }
  return {
    "APCA-API-KEY-ID": key,
    "APCA-API-SECRET-KEY": secret,
    accept: "application/json",
  };
}

/**
 * GET an Alpaca endpoint and return a typed, key-free result object.
 * Network/credential/Alpaca errors are normalized into { ok: false }.
 */
export async function alpacaGet<T>(path: string): Promise<AlpacaResult<T>> {
  let res: Response;
  try {
    res = await fetch(`${getBaseUrl()}${path}`, {
      headers: getHeaders(),
      cache: "no-store",
    });
  } catch (err) {
    return {
      ok: false,
      status: 502,
      error:
        err instanceof Error ? err.message : "Failed to reach Alpaca API",
    };
  }

  const text = await res.text();
  let body: unknown = null;
  if (text) {
    try {
      body = JSON.parse(text);
    } catch {
      body = text;
    }
  }

  if (!res.ok) {
    const message =
      body && typeof body === "object" && body !== null && "message" in body
        ? String((body as { message: unknown }).message)
        : `Alpaca request failed (${res.status})`;
    return { ok: false, status: res.status, error: message };
  }

  return { ok: true, status: res.status, data: body as T };
}
