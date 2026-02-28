"use client";

import { useEffect, useState, useRef } from "react";
import { usePathname } from "next/navigation";
import { ClientLayout } from "./client-layout";
import { AuthProvider } from "@/components/providers/AuthProvider";
import { useAuthConfigStore } from "@/stores/auth-config-store";
import { LoadingScreen } from "@/components/ui/LoadingScreen";

const AUTH_BYPASS_PATHS = ["/login", "/register", "/auth/callback", "/forgot-password"];
const STANDALONE_PATHS = ["/admin"];
const RETRY_INTERVAL_MS = 3000;

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const isBypass = AUTH_BYPASS_PATHS.some((p) => pathname.startsWith(p));
  const { authEnabled, checked, checkAuthEnabled } = useAuthConfigStore();
  const [ready, setReady] = useState(false);
  const [retryCount, setRetryCount] = useState(0);
  const cancelledRef = useRef(false);

  useEffect(() => {
    cancelledRef.current = false;
    let timer: ReturnType<typeof setTimeout>;

    const tryConnect = () => {
      checkAuthEnabled()
        .then(() => {
          if (!cancelledRef.current) setReady(true);
        })
        .catch(() => {
          if (!cancelledRef.current) {
            setRetryCount((c) => c + 1);
            timer = setTimeout(tryConnect, RETRY_INTERVAL_MS);
          }
        });
    };

    tryConnect();

    return () => {
      cancelledRef.current = true;
      clearTimeout(timer);
    };
  }, [checkAuthEnabled]);

  // bypass 路径（login/register/callback）立即渲染，不等 /health
  if (isBypass) {
    return <>{children}</>;
  }

  if (!ready) {
    const msg =
      retryCount === 0
        ? undefined
        : retryCount < 3
          ? "正在连接服务器..."
          : "服务器连接中，请确认后端已启动";
    return <LoadingScreen message={msg} />;
  }

  const isStandalone = STANDALONE_PATHS.some((p) => pathname.startsWith(p));

  if (isStandalone) {
    return (
      <AuthProvider authEnabled={checked && authEnabled === true}>
        {children}
      </AuthProvider>
    );
  }

  return (
    <AuthProvider authEnabled={checked && authEnabled === true}>
      <ClientLayout>{children}</ClientLayout>
    </AuthProvider>
  );
}
