import { NextResponse } from "next/server";
import { alpacaGet } from "@/lib/alpaca";
import type { Account, ApiResponse } from "@/lib/types";

export const dynamic = "force-dynamic";
export const revalidate = 0;

export async function GET(): Promise<NextResponse<ApiResponse<Account>>> {
  const result = await alpacaGet<Account>("/v2/account");
  if (!result.ok) {
    return NextResponse.json(
      { status: "error", error: result.error },
      { status: result.status }
    );
  }
  // Return only the fields the UI needs; never echo credentials.
  const a = result.data;
  return NextResponse.json({
    status: "ok",
    data: {
      account_number: a.account_number,
      status: a.status,
      equity: a.equity,
      last_equity: a.last_equity,
      buying_power: a.buying_power,
      cash: a.cash,
      portfolio_value: a.portfolio_value,
      currency: a.currency,
    },
  });
}
