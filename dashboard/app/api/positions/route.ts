import { NextResponse } from "next/server";
import { alpacaGet } from "@/lib/alpaca";
import type { Position, ApiResponse } from "@/lib/types";

export const dynamic = "force-dynamic";
export const revalidate = 0;

export async function GET(): Promise<NextResponse<ApiResponse<Position[]>>> {
  const result = await alpacaGet<Position[]>("/v2/positions");
  if (!result.ok) {
    return NextResponse.json(
      { status: "error", error: result.error },
      { status: result.status }
    );
  }
  return NextResponse.json({ status: "ok", data: result.data });
}
