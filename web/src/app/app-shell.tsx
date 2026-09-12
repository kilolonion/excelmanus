"use client";

import { useEffect, useState, useRef } from "react";
import dynamic from "next/dynamic";
import { usePathname } from "next/navigation";
import { useAuthConfigStore } from "@/stores/auth-config-store";
import { LoadingScreen } from "@/components/ui/LoadingScreen";
import { VersionUpdateToast } from "@/components/VersionUpdateToast";
import { GlobalRestartOverlay } from "@/components/GlobalRestartOverlay";
import { ManageTokenGate } from "@/components/ManageTokenGate";
import { ensureHealthHubPolling, useHealthHubStore } from "@/stores/health-hub-store";
import { pathnameStartsWith } from "@/lib/pathname";
import { getManageToken } from "@/lib/api";

const loadClientLayout = () =>
  import("./client-layout").then((m) => ({ default: m.ClientLayout }));

const ClientLayout = dynamic(loadClientLayout, {
  ssr: false,
  loading: () => <LoadingScreen />,
});

const STANDALONE_PATHS = ["/admin"];
const RETRY_INTERVAL_MS = 3000;

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const { checkBackendHealth } = useAuthConfigStore();
  const authRequired = useAuthConfigStore((s) => s.authRequired);
  const [ready, setReady] = useState(false);
  const [tokenReady, setTokenReady] = useState(() => Boolean(getManageToken()));
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
    void loadClientLayout();

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

  if (authRequired && !tokenReady) {
    return <ManageTokenGate onSaved={() => setTokenReady(true)} />;
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
