"use client";

import { useEffect, useState, useCallback, useRef } from "react";
import { usePathname, useRouter } from "next/navigation";
import { useAuthStore } from "@/stores/auth-store";
import {
  fetchCurrentUser,
  refreshAccessToken,
  isTokenExpired,
} from "@/lib/auth-api";

const PUBLIC_PATHS = ["/login", "/register", "/auth/callback"];

interface AuthProviderProps {
  children: React.ReactNode;
  authEnabled?: boolean;
}

/**
 * Optimistic auth provider.
 *
 * If localStorage already has a non-expired JWT the page renders immediately;
 * a background fetch to /auth/me validates the session silently.
 * The loading spinner only appears when there is genuinely no local token
 * and we must wait for a refresh attempt.
 */
export function AuthProvider({ children, authEnabled = false }: AuthProviderProps) {
  const pathname = usePathname();
  const router = useRouter();
  const [hydrated, setHydrated] = useState(false);
  // `gateOpen` controls whether children are rendered.
  // Starts `true` — we only close the gate when we know for sure the user
  // has no usable local token and must wait for a network round-trip.
  const [gateOpen, setGateOpen] = useState(true);
  const [showSpinner, setShowSpinner] = useState(false);
  const validatedRef = useRef(false);

  // ── Hydration listener ───────────────────────────────────
  useEffect(() => {
    const unsub = useAuthStore.persist.onFinishHydration(() => setHydrated(true));
    if (useAuthStore.persist.hasHydrated()) setHydrated(true);
    return unsub;
  }, []);

  // ── Decide gate state immediately after hydration ────────
  useEffect(() => {
    if (!hydrated || !authEnabled) return;

    const { isAuthenticated, accessToken } = useAuthStore.getState();
    const isPublic = PUBLIC_PATHS.some((p) => pathname.startsWith(p));

    if (isPublic) {
      // Public pages always render immediately
      setGateOpen(true);
      return;
    }

    if (isAuthenticated && accessToken && !isTokenExpired(accessToken)) {
      // Good local token → render immediately, validate in background
      setGateOpen(true);
      return;
    }

    // No usable token locally — must wait for refresh / redirect
    setGateOpen(false);
    setShowSpinner(true);
  }, [hydrated, authEnabled, pathname]);

  // ── Background validation (runs once per mount) ──────────
  const validateSession = useCallback(async () => {
    if (!authEnabled) return;

    const isPublic = PUBLIC_PATHS.some((p) => pathname.startsWith(p));
    const { isAuthenticated, accessToken, refreshToken } = useAuthStore.getState();

    // Case 1: No token at all
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

    // Case 2: Authenticated user on a public page → redirect to home
    if (isPublic) {
      router.replace("/");
      return;
    }

    // Case 3: Token expired locally → try refresh first
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

    // Case 4: Token looks valid locally → silent background check
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

  // ── Render logic ─────────────────────────────────────────

  if (!authEnabled) return <>{children}</>;

  // Still waiting for zustand to hydrate from localStorage
  if (!hydrated) return null;

  // Gate closed: no usable local token, waiting for network
  if (!gateOpen && showSpinner) {
    return (
      <div className="h-screen flex items-center justify-center bg-background">
        <div className="flex flex-col items-center gap-3">
          <div className="h-8 w-8 animate-spin rounded-full border-2 border-muted-foreground border-t-transparent" />
          <span className="text-sm text-muted-foreground">验证身份中...</span>
        </div>
      </div>
    );
  }

  const isPublic = PUBLIC_PATHS.some((p) => pathname.startsWith(p));
  const { isAuthenticated } = useAuthStore.getState();
  if (!isAuthenticated && !isPublic && !gateOpen) return null;

  return <>{children}</>;
}
