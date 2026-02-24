"use client";

import { useEffect, useState, useCallback } from "react";
import { usePathname, useRouter } from "next/navigation";
import { useAuthStore } from "@/stores/auth-store";
import { fetchCurrentUser, refreshAccessToken } from "@/lib/auth-api";

const PUBLIC_PATHS = ["/login", "/register", "/auth/callback"];

interface AuthProviderProps {
  children: React.ReactNode;
  authEnabled?: boolean;
}

export function AuthProvider({ children, authEnabled = false }: AuthProviderProps) {
  const pathname = usePathname();
  const router = useRouter();
  const [checking, setChecking] = useState(true);
  const [hydrated, setHydrated] = useState(false);

  useEffect(() => {
    const unsub = useAuthStore.persist.onFinishHydration(() => setHydrated(true));
    if (useAuthStore.persist.hasHydrated()) setHydrated(true);
    return unsub;
  }, []);

  const validateSession = useCallback(async () => {
    if (!authEnabled) {
      setChecking(false);
      return;
    }

    const isPublic = PUBLIC_PATHS.some((p) => pathname.startsWith(p));
    const { isAuthenticated, accessToken, refreshToken } = useAuthStore.getState();

    if (!isAuthenticated || !accessToken) {
      if (refreshToken) {
        const refreshed = await refreshAccessToken();
        if (refreshed) {
          setChecking(false);
          return;
        }
      }
      if (!isPublic) router.replace("/login");
      setChecking(false);
      return;
    }

    if (isPublic) {
      router.replace("/");
      setChecking(false);
      return;
    }

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
    } finally {
      setChecking(false);
    }
  }, [authEnabled, pathname, router]);

  useEffect(() => {
    if (!hydrated) return;
    validateSession();
  }, [hydrated, validateSession]);

  if (!authEnabled) return <>{children}</>;

  if (!hydrated || checking) {
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
  if (!isAuthenticated && !isPublic) return null;

  return <>{children}</>;
}
