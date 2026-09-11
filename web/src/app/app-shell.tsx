"use client";

import { useEffect, useState, useRef } from "react";
import { usePathname } from "next/navigation";
import { ClientLayout } from "./client-layout";
import { useAuthConfigStore } from "@/stores/auth-config-store";
import { LoadingScreen } from "@/components/ui/LoadingScreen";
import { VersionUpdateToast } from "@/components/VersionUpdateToast";
import { GlobalRestartOverlay } from "@/components/GlobalRestartOverlay";
import { ensureHealthHubPolling, useHealthHubStore } from "@/stores/health-hub-store";
import { pathnameStartsWith } from "@/lib/pathname";

const STANDALONE_PATHS = ["/admin"];
const RETRY_INTERVAL_MS = 3000;

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const { checkBackendHealth } = useAuthConfigStore();
  const [ready, setReady] = useState(false);
  const [retryCount, setRetryCount] = useState(0);
  const cancelledRef = useRef(false);
  const newVersionAvailable = useHealthHubStore((s) => s.newVersionAvailable);
  const apiIncompatible = useHealthHubStore((s) => s.apiIncompatible);
  const remoteVersion = useHealthHubStore((s) => s.remoteVersion);
  const dismissVersion = useHealthHubStore((s) => s.dismissVersion);
  const refreshNow = useHealthHubStore((s) => s.refreshNow);

  useEffect(() => {
    cancelledRef.current = false;
    let timer: ReturnType<typeof setTimeout>;

    const tryConnect = () => {
      checkBackendHealth()
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
  }, [checkBackendHealth]);

  useEffect(() => {
    ensureHealthHubPolling();
  }, []);

  if (!ready) {
    const msg =
      retryCount === 0
        ? undefined
        : retryCount < 3
          ? "正在连接服务器..."
          : "服务器连接中，请确认后端已启动";
    return <LoadingScreen message={msg} />;
  }

  const isStandalone = pathnameStartsWith(pathname, STANDALONE_PATHS);

  const versionToast = (
    <VersionUpdateToast
      newVersionAvailable={newVersionAvailable}
      apiIncompatible={apiIncompatible}
      remoteVersion={remoteVersion}
      onDismiss={dismissVersion}
      onRefresh={refreshNow}
    />
  );

  if (isStandalone) {
    return (
      <>
        {children}
        {versionToast}
        <GlobalRestartOverlay />
      </>
    );
  }

  return (
    <>
      <ClientLayout>{children}</ClientLayout>
      {versionToast}
      <GlobalRestartOverlay />
    </>
  );
}
