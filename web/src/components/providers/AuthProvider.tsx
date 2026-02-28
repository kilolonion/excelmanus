"use client";

import { useEffect, useState, useCallback, useRef } from "react";
import { usePathname, useRouter } from "next/navigation";
import { useAuthStore } from "@/stores/auth-store";
import {
  fetchCurrentUser,
  refreshAccessToken,
  isTokenExpired,
} from "@/lib/auth-api";
import { LoadingScreen } from "@/components/ui/LoadingScreen";

const PUBLIC_PATHS = ["/login", "/register", "/auth/callback", "/forgot-password", "/terms", "/privacy"];

interface AuthProviderProps {
  children: React.ReactNode;
  authEnabled?: boolean;
}

/**
 * 乐观认证提供者。
 *
 * 若 localStorage 中已有未过期的 JWT 则立即渲染页面；
 * 通过后台请求 /auth/me 静默校验会话。
 * 仅当确实没有本地 token 且需等待刷新尝试时才显示加载动画。
 */
export function AuthProvider({ children, authEnabled = false }: AuthProviderProps) {
  const pathname = usePathname();
  const router = useRouter();
  const [hydrated, setHydrated] = useState(false);
  // gateOpen 控制是否渲染子节点。初始为 true，仅在确认用户无可用本地 token 且需等待网络请求时才关闭。
  const [gateOpen, setGateOpen] = useState(true);
  const [showSpinner, setShowSpinner] = useState(false);
  const validatedRef = useRef(false);

  // ── 水合监听 ───────────────────────────────────
  useEffect(() => {
    const unsub = useAuthStore.persist.onFinishHydration(() => setHydrated(true));
    if (useAuthStore.persist.hasHydrated()) setHydrated(true);
    return unsub;
  }, []);

  // ── 水合后立即决定 gate 状态 ────────
  useEffect(() => {
    if (!hydrated || !authEnabled) return;

    const { isAuthenticated, accessToken } = useAuthStore.getState();
    const isPublic = PUBLIC_PATHS.some((p) => pathname.startsWith(p));

    if (isPublic) {
      // 公开页始终立即渲染
      setGateOpen(true);
      return;
    }

    if (isAuthenticated && accessToken && !isTokenExpired(accessToken)) {
      // 本地 token 有效 → 立即渲染，后台校验
      setGateOpen(true);
      return;
    }

    // 本地无可用 token，需等待刷新或重定向
    setGateOpen(false);
    setShowSpinner(true);
  }, [hydrated, authEnabled, pathname]);

  // ── 后台校验（每次挂载执行一次）──────────
  const validateSession = useCallback(async () => {
    if (!authEnabled) return;

    const isPublic = PUBLIC_PATHS.some((p) => pathname.startsWith(p));
    const { isAuthenticated, accessToken, refreshToken } = useAuthStore.getState();

    // 情况 1：完全没有 token
    if (!isAuthenticated || !accessToken) {
      if (refreshToken) {
        const refreshed = await refreshAccessToken();
        if (refreshed) {
          setGateOpen(true);
          setShowSpinner(false);
          return;
        }
      }
      if (!isPublic) router.replace("/login");
      setShowSpinner(false);
      return;
    }

    // 情况 2：已认证用户访问登录/注册页 → 重定向到首页
    const REDIRECT_WHEN_AUTHED = ["/login", "/register"];
    if (REDIRECT_WHEN_AUTHED.some((p) => pathname.startsWith(p))) {
      router.replace("/");
      return;
    }

    // 情况 3：本地 token 已过期 → 先尝试刷新
    if (isTokenExpired(accessToken)) {
      if (refreshToken) {
        const refreshed = await refreshAccessToken();
        if (refreshed) {
          setGateOpen(true);
          setShowSpinner(false);
          return;
        }
      }
      useAuthStore.getState().logout();
      router.replace("/login");
      setShowSpinner(false);
      return;
    }

    // 情况 4：本地 token 看似有效 → 静默后台校验
    try {
      const user = await fetchCurrentUser();
      if (!user) {
        const refreshed = await refreshAccessToken();
        if (!refreshed) {
          useAuthStore.getState().logout();
          router.replace("/login");
        }
      }
    } catch {
      useAuthStore.getState().logout();
      router.replace("/login");
    }
  }, [authEnabled, pathname, router]);

  useEffect(() => {
    if (!hydrated || validatedRef.current) return;
    validatedRef.current = true;
    validateSession();
  }, [hydrated, validateSession]);

  // ── 渲染逻辑 ─────────────────────────────────────────

  if (!authEnabled) return <>{children}</>;

  // 仍在等待 zustand 从 localStorage 水合
  if (!hydrated) return null;

  // gate 关闭：无可用本地 token，等待网络
  if (!gateOpen && showSpinner) {
    return <LoadingScreen message="验证身份中..." />;
  }

  const isPublic = PUBLIC_PATHS.some((p) => pathname.startsWith(p));
  const { isAuthenticated } = useAuthStore.getState();
  if (!isAuthenticated && !isPublic && !gateOpen) return null;

  return <>{children}</>;
}
