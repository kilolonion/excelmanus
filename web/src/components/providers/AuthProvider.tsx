"use client";

import { useEffect, useState, useCallback, useRef } from "react";
import { usePathname, useRouter } from "next/navigation";
import { useAuthStore } from "@/stores/auth-store";
import {
  fetchCurrentUser,
  refreshAccessToken,
  isTokenExpired,
  clearSessionCookie,
} from "@/lib/auth-api";
import { LoadingScreen } from "@/components/ui/LoadingScreen";
import { pathnameStartsWith } from "@/lib/pathname";

const PUBLIC_PATHS = ["/login", "/register", "/auth/callback", "/forgot-password", "/terms", "/privacy"];

interface AuthProviderProps {
  children: React.ReactNode;
  authEnabled?: boolean;
}

/**
 * 安全优先认证提供者。
 *
 * 渲染决策完全在渲染路径中同步计算（基于响应式 zustand hook），
 * 不依赖任何 useEffect 异步置状态来控制 gate，
 * 从而杜绝 "水合完成 → useEffect 运行前" 窗口期的内容泄漏。
 *
 * 规则：
 * 1. zustand 未水合 → null
 * 2. 公开路径 → 放行
 * 3. 有效本地 token（未过期 JWT） → 乐观放行，后台静默校验
 * 4. 其余情况 → LoadingScreen 阻塞，等待后台刷新/重定向
 */
export function AuthProvider({ children, authEnabled = false }: AuthProviderProps) {
  const pathname = usePathname();
  const router = useRouter();
  const [hydrated, setHydrated] = useState(false);
  const validatedRef = useRef(false);

  // ── 响应式订阅认证状态（logout/setTokens 后自动触发重渲染）──
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);
  const accessToken = useAuthStore((s) => s.accessToken);

  const isPublic = pathnameStartsWith(pathname, PUBLIC_PATHS);

  // ── 水合监听 ───────────────────────────────────
  useEffect(() => {
    const unsub = useAuthStore.persist.onFinishHydration(() => setHydrated(true));
    if (useAuthStore.persist.hasHydrated()) setHydrated(true);
    return unsub;
  }, []);

  // ── 后台校验（水合后执行一次）──────────
  const validateSession = useCallback(async () => {
    if (!authEnabled) return;

    const state = useAuthStore.getState();

    // 情况 1：完全没有 token
    if (!state.isAuthenticated || !state.accessToken) {
      if (state.refreshToken) {
        const refreshed = await refreshAccessToken();
        if (refreshed) return; // store 更新 → 响应式 hook 触发重渲染 → 放行
      }
      if (!isPublic) router.replace("/login");
      return;
    }

    // 情况 2：已认证用户访问登录/注册页 → 重定向到首页
    const REDIRECT_WHEN_AUTHED = ["/login", "/register"];
    if (pathnameStartsWith(pathname, REDIRECT_WHEN_AUTHED)) {
      router.replace("/");
      return;
    }

    // 情况 3：本地 token 已过期 → 先尝试刷新
    if (isTokenExpired(state.accessToken)) {
      if (state.refreshToken) {
        const refreshed = await refreshAccessToken();
        if (refreshed) return;
      }
      useAuthStore.getState().logout();
      clearSessionCookie();
      router.replace("/login");
      return;
    }

    // 情况 4：本地 token 看似有效 → 静默后台校验
    try {
      const user = await fetchCurrentUser();
      if (!user) {
        // fetchCurrentUser 返回 null 说明后端明确拒绝（401 等），尝试刷新
        const refreshed = await refreshAccessToken();
        if (!refreshed) {
          useAuthStore.getState().logout();
          clearSessionCookie();
          router.replace("/login");
        }
      }
    } catch {
      // 网络错误（Failed to fetch / DNS / timeout）→ 不登出，保留本地 session，
      // 避免瞬时网络波动把用户踢出登录并导致与自动登录形成重定向循环。
      // 下次用户主动操作时会自然重试。
      console.warn("[auth] validation network error, keeping session");
    }
  }, [authEnabled, pathname, router, isPublic]);

  useEffect(() => {
    if (!hydrated || validatedRef.current) return;
    validatedRef.current = true;
    validateSession();
  }, [hydrated, validateSession]);

  // ── 渲染逻辑（纯同步，无异步状态依赖）──────────────

  // 认证未启用 → 始终放行
  if (!authEnabled) return <>{children}</>;

  // zustand 尚未从 localStorage 水合 → 不渲染任何内容
  if (!hydrated) return null;

  // 公开路径 → 始终放行
  if (isPublic) return <>{children}</>;

  // 同步判定：有效本地 token → 乐观放行（后台 validateSession 静默校验）
  if (isAuthenticated && accessToken && !isTokenExpired(accessToken)) {
    return <>{children}</>;
  }

  // 无有效 token → 阻塞渲染，显示加载（validateSession 正在后台刷新或重定向）
  return <LoadingScreen message="验证身份中..." />;
}
