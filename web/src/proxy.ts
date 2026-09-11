import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

/** 单用户架构不再做身份登录拦截。Codex OAuth 回调页可直接访问。 */
export function proxy(_request: NextRequest) {
  return NextResponse.next();
}

export const config = {
  matcher: [
    "/((?!_next/static|_next/image|api/v1/|samples/)(?!.*\\.(?:ico|png|svg|jpg|jpeg|webp)$).*)",
  ],
};
