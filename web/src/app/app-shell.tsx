"use client";

import { useEffect, useState, useRef, type ComponentType, type ReactNode } from "react";
import { usePathname } from "next/navigation";
import { useAuthConfigStore } from "@/stores/auth-config-store";
import { LoadingScreen } from "@/components/ui/LoadingScreen";
import { VersionUpdateToast } from "@/components/VersionUpdateToast";
import { GlobalRestartOverlay } from "@/components/GlobalRestartOverlay";
import { ManageTokenGate } from "@/components/ManageTokenGate";
import { ensureHealthHubPolling, useHealthHubStore } from "@/stores/health-hub-store";
import { pathnameStartsWith } from "@/lib/pathname";
import { getManageToken } from "@/lib/api";

type ClientLayoutComponent = ComponentType<{ children: ReactNode }>;

const STANDALONE_PATHS = ["/admin"];
const RETRY_INTERVAL_MS = 400;

export function AppShell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const checkBackendHealth = useAuthConfigStore((s) => s.checkBackendHealth);
  const authRequired = useAuthConfigStore((s) => s.authRequired);
  const [ready, setReady] = useState(false);
  const [Layout, setLayout] = useState<ClientLayoutComponent | null>(null);
  const [tokenReady, setTokenReady] = useState(() => Boolean(getManageToken()));
  const [retryCount, setRetryCount] = useState(0);
  const cancelledRef = useRef(false);
  const newVersionAvailable = useHealthHubStore((s) => s.newVersionAvailable);
  const apiIncompatible = useHealthHubStore((s) => s.apiIncompatible);
  const remoteVersion = useHealthHubStore((s) => s.remoteVersion);
  const dismissVersion = useHealthHubStore((s) => s.dismissVersion);
  const refreshNow = useHealthHubStore((s) => s.refreshNow);
  const isStandalone = pathnameStartsWith(pathname, STANDALONE_PATHS);

  useEffect(() => {
    cancelledRef.current = false;
    let timer: ReturnType<typeof setTimeout> | undefined;

    void import("./client-layout").then((mod) => {
      if (!cancelledRef.current) setLayout(() => mod.ClientLayout);
    });

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
      if (timer) clearTimeout(timer);
    };
  }, [checkBackendHealth]);

  useEffect(() => {
    ensureHealthHubPolling();
  }, []);

  const splashMessage =
    retryCount === 0
      ? undefined
      : retryCount < 3
        ? "正在连接服务器..."
        : "服务器连接中，请确认后端已启动";
  const waitingForShell = !ready || (!isStandalone && Layout == null);
  if (waitingForShell) {
    return <LoadingScreen message={splashMessage} />;
  }

  if (authRequired && !tokenReady) {
    return <ManageTokenGate onSaved={() => setTokenReady(true)} />;
  }

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

  if (Layout == null) {
    return <LoadingScreen message={splashMessage} />;
  }

  return (
    <>
      <Layout>{children}</Layout>
      {versionToast}
      <GlobalRestartOverlay />
    </>
  );
}
