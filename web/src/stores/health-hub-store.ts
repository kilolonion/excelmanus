import { create } from "zustand";
import { useConnectionStore, WEB_UPGRADE_REQUEST_KEY } from "@/stores/connection-store";
import { apiGet } from "@/lib/api";
import { refreshApp } from "@/lib/app-refresh";
import { fetchWebBuild, versionDifference } from "@/lib/web-version";

export interface HealthData {
  status: string;
  version: string;
  model: string;
  tools: string[];
  skillpacks: string[];
  tool_count?: number;
  skillpack_count?: number;
  active_sessions: number;
  restart_reason?: string;
}

interface HealthPayload extends HealthData {
  build_id?: string | null;
  version_fingerprint?: string | null;
  api_schema_version?: number;
}

interface HealthHubState {
  health: HealthData | null;
  connected: boolean | null;
  newVersionAvailable: boolean;
  apiIncompatible: boolean;
  remoteVersion: string | null;
  refreshError: string | null;
  dismissVersion: () => void;
  refreshNow: () => void;
}

const INIT_DELAY_MS = 200;
const QUICK_CONFIRM_MS = 15_000;
const STEADY_INTERVAL_MS = 30_000;
const ERROR_INTERVAL_MS = 15_000;

const baselineRef: {
  buildId: string | null;
  fingerprint: string | null;
  apiSchemaVersion: number | null;
  initialized: boolean;
} = {
  buildId: null,
  fingerprint: null,
  apiSchemaVersion: null,
  initialized: false,
};

let dismissedVersion: string | null = null;
let observedVersion: string | null = null;
let frontendBuildId: string | null = process.env.NEXT_PUBLIC_WEB_BUILD_ID || null;
let frontendChanged = false;
let latestHealth: HealthPayload | null = null;
let pollCount = 0;
let failCount = 0;
let pollTimer: ReturnType<typeof setTimeout> | null = null;
let pollingStarted = false;
let visibilityListenerBound = false;

const normalizeHealthData = (raw: HealthPayload): HealthData => ({
  status: typeof raw.status === "string" ? raw.status : "unknown",
  version: typeof raw.version === "string" ? raw.version : "",
  model: typeof raw.model === "string" ? raw.model : "",
  tools: Array.isArray(raw.tools) ? raw.tools : [],
  skillpacks: Array.isArray(raw.skillpacks) ? raw.skillpacks : [],
  tool_count: typeof raw.tool_count === "number" ? raw.tool_count : undefined,
  skillpack_count: typeof raw.skillpack_count === "number" ? raw.skillpack_count : undefined,
  active_sessions: typeof raw.active_sessions === "number" ? raw.active_sessions : 0,
  restart_reason: typeof raw.restart_reason === "string" ? raw.restart_reason : undefined,
});

export function healthToolCount(health: HealthData): number {
  return typeof health.tool_count === "number" ? health.tool_count : health.tools.length;
}

export function healthSkillpackCount(health: HealthData): number {
  return typeof health.skillpack_count === "number" ? health.skillpack_count : health.skillpacks.length;
}

function schedulePoll(delayMs: number): void {
  if (pollTimer) {
    clearTimeout(pollTimer);
  }
  pollTimer = setTimeout(() => {
    void pollHealth();
  }, delayMs);
}

function applyVersionState(data: HealthPayload): void {
  latestHealth = data;
  const remoteBuildId = data.build_id ?? null;
  const remoteFingerprint = data.version_fingerprint ?? null;
  const remoteSchema = data.api_schema_version ?? null;

  if (!baselineRef.initialized) {
    baselineRef.buildId = remoteBuildId;
    baselineRef.fingerprint = remoteFingerprint;
    baselineRef.apiSchemaVersion = remoteSchema;
    baselineRef.initialized = true;
  }

  const difference = versionDifference(baselineRef, {
    buildId: remoteBuildId, fingerprint: remoteFingerprint, apiSchemaVersion: remoteSchema,
  });
  if (difference.incompatible) {
    useHealthHubStore.setState({
      newVersionAvailable: false,
      apiIncompatible: true,
      remoteVersion: data.version ?? null,
    });
    return;
  }

  observedVersion = `${remoteBuildId || ""}|${remoteFingerprint || ""}|${latestFrontendBuild || ""}`;
  useHealthHubStore.setState((state) => ({
    apiIncompatible: false,
    remoteVersion: data.version ?? state.remoteVersion,
    newVersionAvailable: observedVersion !== dismissedVersion && (difference.changed || frontendChanged),
  }));
}

let latestFrontendBuild: string | null = null;

async function pollFrontendVersion(): Promise<void> {
  if (typeof window === "undefined" || window.excelManusDesktop) return;
  try {
    const build = await fetchWebBuild();
    if (!build) return;
    latestFrontendBuild = build;
    if (frontendBuildId === null) frontendBuildId = build;
    frontendChanged = build !== frontendBuildId;
    if (latestHealth) applyVersionState(latestHealth);
    else {
      observedVersion = `||${build}`;
      useHealthHubStore.setState({ newVersionAvailable: frontendChanged && observedVersion !== dismissedVersion });
    }
  } catch { /* A temporary frontend outage is handled by connection recovery. */ }
}

async function pollHealth(): Promise<void> {
  if (typeof document !== "undefined" && document.hidden) {
    schedulePoll(STEADY_INTERVAL_MS);
    return;
  }

  pollCount += 1;
  await pollFrontendVersion();
  try {
    const data = await apiGet<HealthPayload>("/health", {
      direct: true,
      timeoutMs: 10_000,
      cache: "no-store",
    });
    useHealthHubStore.setState({
      health: normalizeHealthData(data),
      connected: true,
    });
    failCount = 0;

    const connectionStore = useConnectionStore.getState();
    if (data.status === "draining") {
      void connectionStore.triggerRestart(data.restart_reason || "服务正在重启");
    } else if (connectionStore.status === "disconnected") {
      connectionStore.setConnected();
    }

    applyVersionState(data);

    schedulePoll(pollCount <= 1 ? QUICK_CONFIRM_MS : STEADY_INTERVAL_MS);
  } catch {
    failCount += 1;
    useHealthHubStore.setState({ connected: false });
    if (failCount >= 2 && useConnectionStore.getState().status === "connected") {
      useConnectionStore.getState().setDisconnected();
    }
    schedulePoll(ERROR_INTERVAL_MS);
  }
}

export function ensureHealthHubPolling(): void {
  if (pollingStarted) return;
  pollingStarted = true;
  try {
    const requestId = sessionStorage.getItem(WEB_UPGRADE_REQUEST_KEY);
    if (requestId) void useConnectionStore.getState().triggerRestart("恢复网页更新进度", { upgradeRequestId: requestId });
  } catch { /* storage may be unavailable */ }
  pollCount = 0;
  failCount = 0;

  if (typeof document !== "undefined" && !visibilityListenerBound) {
    document.addEventListener("visibilitychange", () => {
      if (!document.hidden) {
        if (pollTimer) {
          clearTimeout(pollTimer);
          pollTimer = null;
        }
        void pollHealth();
      }
    });
    visibilityListenerBound = true;
  }

  schedulePoll(INIT_DELAY_MS);
}

export const useHealthHubStore = create<HealthHubState>((set) => ({
  health: null,
  connected: null,
  newVersionAvailable: false,
  apiIncompatible: false,
  remoteVersion: null,
  refreshError: null,
  dismissVersion: () => {
    dismissedVersion = observedVersion;
    set((state) => ({ ...state, newVersionAvailable: false }));
  },
  refreshNow: () => {
    set({ refreshError: refreshApp() });
  },
}));
