import { NextResponse } from "next/server";
import { alpacaGet } from "@/lib/alpaca";
import type { OrderLeg, ApiResponse } from "@/lib/types";

export const dynamic = "force-dynamic";
export const revalidate = 0;

export async function GET(): Promise<NextResponse<ApiResponse<OrderLeg[]>>> {
  // status=all includes closed/filled orders; nested=true returns bracket legs
  // (take-profit / stop-loss) under the parent order's `legs` field.
  const result = await alpacaGet<OrderLeg[]>(
    "/v2/orders?status=all&limit=50&nested=true&direction=desc"
  );
  if (!result.ok) {
    return NextResponse.json(
      { status: "error", error: result.error },
      { status: result.status }
    );
  }
  return NextResponse.json({ status: "ok", data: result.data });
}
