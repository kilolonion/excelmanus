"use client";

import { useEffect, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import { useAuthStore } from "@/stores/auth-store";
import { fetchCurrentUser } from "@/lib/auth-api";

const PUBLIC_PATHS = ["/login", "/register"];

interface AuthProviderProps {
  children: React.ReactNode;
  authEnabled?: boolean;
}

export function AuthProvider({ children, authEnabled = false }: AuthProviderProps) {
  const pathname = usePathname();
  const router = useRouter();
  const { isAuthenticated, accessToken } = useAuthStore();
  const [checking, setChecking] = useState(true);

  useEffect(() => {
    if (!authEnabled) {
      setChecking(false);
      return;
    }

    const isPublic = PUBLIC_PATHS.some((p) => pathname.startsWith(p));

    if (!isAuthenticated || !accessToken) {
      if (!isPublic) {
        router.replace("/login");
      }
      setChecking(false);
      return;
    }

    if (isPublic) {
      router.replace("/");
      setChecking(false);
      return;
    }

    // Validate token by fetching current user
    fetchCurrentUser()
      .then((user) => {
        if (!user) {
          useAuthStore.getState().logout();
          router.replace("/login");
        }
      })
      .catch(() => {
        useAuthStore.getState().logout();
        router.replace("/login");
      })
      .finally(() => setChecking(false));
  }, [pathname, isAuthenticated, accessToken, authEnabled, router]);

  if (!authEnabled) return <>{children}</>;

  if (checking) {
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
  if (!isAuthenticated && !isPublic) return null;

  return <>{children}</>;
}
