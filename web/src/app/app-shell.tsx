"use client";

import { useEffect, useState, type ComponentType, type ReactNode } from "react";
import { usePathname } from "next/navigation";
import { useAuthConfigStore } from "@/stores/auth-config-store";
import { LoadingScreen } from "@/components/ui/LoadingScreen";
import { VersionUpdateToast } from "@/components/VersionUpdateToast";
import { GlobalRestartOverlay } from "@/components/GlobalRestartOverlay";
import { LoginGate } from "@/components/LoginGate";
import { ensureHealthHubPolling, useHealthHubStore } from "@/stores/health-hub-store";
import { pathnameStartsWith } from "@/lib/pathname";
import { AUTH_REQUIRED_EVENT } from "@/lib/api";
import { fetchAccessStatus, type AccessStatus } from "@/lib/access-api";

type ClientLayoutComponent = ComponentType<{ children: ReactNode }>;

const STANDALONE_PATHS = ["/admin", "/auth"];
const RETRY_INTERVAL_MS = 400;

export function AppShell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const checkBackendHealth = useAuthConfigStore((s) => s.checkBackendHealth);
  const [ready, setReady] = useState(false);
  const [Layout, setLayout] = useState<ClientLayoutComponent | null>(null);
  const [layoutError, setLayoutError] = useState<string | null>(null);
  const [access, setAccess] = useState<AccessStatus | null>(null);
  const [retryCount, setRetryCount] = useState(0);
  const newVersionAvailable = useHealthHubStore((s) => s.newVersionAvailable);
  const apiIncompatible = useHealthHubStore((s) => s.apiIncompatible);
  const remoteVersion = useHealthHubStore((s) => s.remoteVersion);
  const dismissVersion = useHealthHubStore((s) => s.dismissVersion);
  const refreshNow = useHealthHubStore((s) => s.refreshNow);
  const isStandalone = pathnameStartsWith(pathname, STANDALONE_PATHS);

  useEffect(() => {
    if (isStandalone) return;
    let cancelled = false;
    void import("./client-layout")
      .then((mod) => {
        if (!cancelled) setLayout(() => mod.ClientLayout);
      })
      .catch((error: unknown) => {
        console.error("Workspace interface failed to load:", error);
        if (!cancelled) setLayoutError("工作区界面未能加载，请重新加载页面");
      });
    return () => { cancelled = true; };
  }, [isStandalone]);

  useEffect(() => {
    // Each effect owns its cancellation flag so Strict Mode cannot revive an
    // earlier request and create a second, permanent retry loop.
    let cancelled = false;
    let failures = 0;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;

    const tryConnect = () => {
      // Wait for both probes before retrying, instead of piling up pending
      // access requests whenever the health probe fails first.
      void Promise.allSettled([
        checkBackendHealth(),
        fetchAccessStatus({ signal: controller.signal, timeoutMs: 10_000 }),
      ])
        .then(([health, status]) => {
          if (cancelled) return;
          if (health.status === "fulfilled" && status.status === "fulfilled") {
            setAccess(status.value);
            setReady(true);
          } else {
            failures += 1;
            setRetryCount(failures);
            // Desktop loads this shell while the backend is still warming; keep
            // the backoff short so readiness is noticed promptly after boot.
            timer = setTimeout(tryConnect, Math.min(2_500, RETRY_INTERVAL_MS * 2 ** Math.min(failures - 1, 4)));
          }
        });
    };

    tryConnect();

    return () => {
      cancelled = true;
      controller.abort();
      if (timer) clearTimeout(timer);
    };
  }, [checkBackendHealth]);

  useEffect(() => {
    if (access) useAuthConfigStore.setState({ authRequired: access.auth_required });
  }, [access]);

  useEffect(() => {
    const lock = () => setAccess((state) => state ? { ...state, auth_required: true, authenticated: false } : null);
    // Refresh on focus and periodically so logout/configuration in another tab
    // and idle session expiry also unmount the private workspace.
    const refreshAccess = () => {
      if (document.hidden) return;
      void fetchAccessStatus().then(setAccess).catch(() => {});
    };
    window.addEventListener(AUTH_REQUIRED_EVENT, lock);
    window.addEventListener("focus", refreshAccess);
    const timer = setInterval(refreshAccess, 30_000);
    return () => {
      window.removeEventListener(AUTH_REQUIRED_EVENT, lock);
      window.removeEventListener("focus", refreshAccess);
      clearInterval(timer);
    };
  }, []);

  useEffect(() => {
    if (ready) ensureHealthHubPolling();
  }, [ready]);

  const splashMessage =
    ready
      ? "服务已连接，正在加载工作区界面..."
      : retryCount === 0
        ? "正在连接服务..."
        : `服务尚未就绪，正在重试（第 ${retryCount} 次）...`;
  const waitingForShell = !ready || (!isStandalone && Layout == null);
  if (waitingForShell) {
    return <LoadingScreen message={splashMessage} error={isStandalone ? null : layoutError} />;
  }

  if (access?.auth_required && !access.authenticated) {
    return <LoginGate method={access.login_method} onSignedIn={async (status) => {
      await checkBackendHealth(true);
      setAccess(status);
    }} />;
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
