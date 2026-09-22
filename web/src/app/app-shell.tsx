"use client";

import { useEffect, useRef, useState, type ComponentType, type ReactNode } from "react";
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
const LAYOUT_LOAD_TIMEOUT_MS = 15_000;
const STARTUP_RECOVERY_KEY = "excelmanus:startup-recovery";
const STARTUP_RECOVERY_WINDOW_MS = 60_000;

function claimStartupRecovery(): boolean {
  if (typeof window === "undefined") return false;
  try {
    const now = Date.now();
    const previous = Number(window.sessionStorage.getItem(STARTUP_RECOVERY_KEY));
    if (Number.isFinite(previous) && now - previous < STARTUP_RECOVERY_WINDOW_MS) return false;
    window.sessionStorage.setItem(STARTUP_RECOVERY_KEY, String(now));
    return true;
  } catch {
    // Private browsing / restricted storage should never block the workspace.
    return false;
  }
}

function clearStartupRecovery(): void {
  if (typeof window === "undefined") return;
  try {
    window.sessionStorage.removeItem(STARTUP_RECOVERY_KEY);
  } catch {
    /* ignore restricted storage */
  }
}

export function AppShell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const checkBackendHealth = useAuthConfigStore((s) => s.checkBackendHealth);
  const [ready, setReady] = useState(false);
  const [Layout, setLayout] = useState<ClientLayoutComponent | null>(null);
  const [layoutError, setLayoutError] = useState<string | null>(null);
  const [layoutRecovering, setLayoutRecovering] = useState(false);
  const recoveryAttemptRef = useRef(false);
  const [access, setAccess] = useState<AccessStatus | null>(null);
  const [retryCount, setRetryCount] = useState(0);
  const newVersionAvailable = useHealthHubStore((s) => s.newVersionAvailable);
  const apiIncompatible = useHealthHubStore((s) => s.apiIncompatible);
  const remoteVersion = useHealthHubStore((s) => s.remoteVersion);
  const dismissVersion = useHealthHubStore((s) => s.dismissVersion);
  const refreshNow = useHealthHubStore((s) => s.refreshNow);
  const refreshError = useHealthHubStore((s) => s.refreshError);
  const isStandalone = pathnameStartsWith(pathname, STANDALONE_PATHS);

  useEffect(() => {
    if (isStandalone) return;
    let cancelled = false;
    let timeout: ReturnType<typeof setTimeout> | undefined;
    let reloadTimer: ReturnType<typeof setTimeout> | undefined;
    const modulePromise = import("./client-layout");
    const timeoutPromise = new Promise<never>((_, reject) => {
      timeout = setTimeout(() => reject(new Error("工作区界面加载超时")), LAYOUT_LOAD_TIMEOUT_MS);
    });

    void Promise.race([modulePromise, timeoutPromise])
      .then((mod) => {
        if (cancelled) return;
        clearStartupRecovery();
        setLayoutRecovering(false);
        setLayout(() => mod.ClientLayout);
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        console.error("Workspace interface failed to load:", error);
        const canRecover = recoveryAttemptRef.current || claimStartupRecovery();
        recoveryAttemptRef.current = true;
        if (canRecover) {
          // A stale or partially downloaded Next chunk is cached as a rejected
          // module in the current document. One bounded full reload is the
          // only reliable way to ask the browser for a fresh chunk graph.
          setLayoutRecovering(true);
          reloadTimer = setTimeout(() => {
            if (!cancelled) window.location.reload();
          }, 500);
        } else {
          setLayoutRecovering(false);
          setLayoutError("工作区界面未能加载，请重新加载页面");
        }
      });

    return () => {
      cancelled = true;
      if (timeout) clearTimeout(timeout);
      if (reloadTimer) clearTimeout(reloadTimer);
    };
  }, [isStandalone]);

  useEffect(() => {
    // Each effect owns its cancellation flag so Strict Mode cannot revive an
    // earlier request and create a second, permanent retry loop.
    let cancelled = false;
    let failures = 0;
    let connected = false;
    let probing = false;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;

    const scheduleRetry = () => {
      failures += 1;
      setRetryCount(failures);
      // Desktop loads this shell while the backend is still warming; keep
      // the backoff short so readiness is noticed promptly after boot.
      timer = setTimeout(tryConnect, Math.min(2_500, RETRY_INTERVAL_MS * 2 ** Math.min(failures - 1, 4)));
    };

    const tryConnect = () => {
      if (cancelled || connected || probing) return;
      probing = true;
      void checkBackendHealth()
        .then(() => {
          if (cancelled) return;
          // /health already tells us whether this instance is protected. Do
          // not wait for an optional auth/status route before opening a public
          // workspace; a slow reverse proxy must not hold the splash for 10s.
          if (!useAuthConfigStore.getState().authRequired) {
            setAccess({
              auth_required: false,
              authenticated: true,
              login_method: "none",
              username: null,
            });
            connected = true;
            setReady(true);
            return;
          }
          return fetchAccessStatus({ signal: controller.signal, timeoutMs: 10_000 })
            .then((status) => {
              if (cancelled) return;
              setAccess(status);
              connected = true;
              setReady(true);
            })
            .catch(() => {
              if (!cancelled) scheduleRetry();
            });
        })
        .catch(() => {
          if (!cancelled) scheduleRetry();
        })
        .finally(() => {
          probing = false;
        });
    };

    const retryImmediately = () => {
      if (cancelled || connected) return;
      if (timer) clearTimeout(timer);
      timer = undefined;
      tryConnect();
    };

    window.addEventListener("online", retryImmediately);
    window.addEventListener("focus", retryImmediately);

    tryConnect();

    return () => {
      cancelled = true;
      controller.abort();
      if (timer) clearTimeout(timer);
      window.removeEventListener("online", retryImmediately);
      window.removeEventListener("focus", retryImmediately);
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
      if (document.hidden || !ready) return;
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
  }, [ready]);

  useEffect(() => {
    if (ready) ensureHealthHubPolling();
  }, [ready]);

  const splashMessage =
    layoutRecovering
      ? "界面资源加载异常，正在自动恢复..."
      : ready
      ? "服务已连接，正在加载工作区界面..."
      : retryCount === 0
        ? "正在连接服务..."
        : `服务尚未就绪，正在重试（第 ${retryCount} 次）...`;
  const waitingForShell = !ready || (!isStandalone && Layout == null);
  if (waitingForShell) {
    return <LoadingScreen message={splashMessage} error={isStandalone || layoutRecovering ? null : layoutError} />;
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
      refreshError={refreshError}
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
