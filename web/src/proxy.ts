import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

/**
 * Next.js 服务端中间件 — 路由保护第一层防线。
 *
 * 在服务端拦截请求，阻止未认证用户获取受保护页面的 HTML。
 * 配合客户端 AuthProvider 形成双层保护：
 *   1. middleware（本文件）：基于 cookie 快速拦截，阻止服务端渲染受保护页面
 *   2. AuthProvider：基于 JWT 精确校验，处理 token 刷新和过期
 *
 * 启用条件：需设置环境变量 NEXT_PUBLIC_AUTH_ENABLED=true
 * （与后端 auth_enabled 保持一致的部署时配置）
 */

const PUBLIC_PATHS = [
  "/login",
  "/register",
  "/auth/callback",
  "/auth/codex/callback",
  "/forgot-password",
  "/terms",
  "/privacy",
];

const SESSION_COOKIE = "em-session";

export function proxy(request: NextRequest) {
  // 认证未显式启用 → 中间件不拦截
  if (process.env.NEXT_PUBLIC_AUTH_ENABLED !== "true") {
    return NextResponse.next();
  }

  const { pathname } = request.nextUrl;

  // 公开路径 → 放行
  if (PUBLIC_PATHS.some((p) => pathname.startsWith(p))) {
    return NextResponse.next();
  }

  // 检查会话 cookie（登录时设置，登出时清除）
  const hasSession = request.cookies.get(SESSION_COOKIE);
  if (!hasSession) {
    const loginUrl = new URL("/login", request.url);
    return NextResponse.redirect(loginUrl);
  }

  return NextResponse.next();
}

export const config = {
  matcher: [
    /*
     * 匹配所有路径，排除：
     * - _next/static, _next/image （Next.js 内部静态资源）
     * - api/v1 （后端 API rewrite，由后端自身保护）
     * - 以 .ico/.png/.svg/.jpg/.webp 结尾的静态文件
     * - samples/ 目录
     */
    "/((?!_next/static|_next/image|api/v1/|samples/)(?!.*\\.(?:ico|png|svg|jpg|jpeg|webp)$).*)",
  ],
};
