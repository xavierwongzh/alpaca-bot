import { NextResponse } from "next/server";
import { alpacaGet } from "@/lib/alpaca";
import type { PortfolioHistory, ApiResponse } from "@/lib/types";

export const dynamic = "force-dynamic";
export const revalidate = 0;

export async function GET(
  request: Request
): Promise<NextResponse<ApiResponse<PortfolioHistory>>> {
  // Allow ?period= & ?timeframe= overrides; default to ~1 month of daily points.
  const { searchParams } = new URL(request.url);
  const period = searchParams.get("period") ?? "1M";
  const timeframe = searchParams.get("timeframe") ?? "1D";

  const result = await alpacaGet<PortfolioHistory>(
    `/v2/account/portfolio/history?period=${encodeURIComponent(
      period
    )}&timeframe=${encodeURIComponent(timeframe)}`
  );
  if (!result.ok) {
    return NextResponse.json(
      { status: "error", error: result.error },
      { status: result.status }
    );
  }
  return NextResponse.json({ status: "ok", data: result.data });
}
