import { create } from "zustand";
import { buildDirectHealthUrl } from "@/lib/backend-origin";
import { useConnectionStore } from "@/stores/connection-store";

export interface HealthData {
  status: string;
  version: string;
  model: string;
  tools: string[];
  skillpacks: string[];
  active_sessions: number;
  channels?: string[];
  restart_reason?: string;
}

interface HealthPayload extends HealthData {
  build_id?: string | null;
  version_fingerprint?: string | null;
  api_schema_version?: number;
  min_frontend_build_id?: string | null;
}

interface HealthHubState {
  health: HealthData | null;
  connected: boolean | null;
  newVersionAvailable: boolean;
  apiIncompatible: boolean;
  remoteVersion: string | null;
  dismissVersion: () => void;
  refreshNow: () => void;
}

const INIT_DELAY_MS = 2_000;
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

let dismissVersionNotice = false;
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
  active_sessions: typeof raw.active_sessions === "number" ? raw.active_sessions : 0,
  channels: Array.isArray(raw.channels) ? raw.channels : undefined,
  restart_reason: typeof raw.restart_reason === "string" ? raw.restart_reason : undefined,
});

function schedulePoll(delayMs: number): void {
  if (pollTimer) {
    clearTimeout(pollTimer);
  }
  pollTimer = setTimeout(() => {
    void pollHealth();
  }, delayMs);
}

function applyVersionState(data: HealthPayload): void {
  const remoteBuildId = data.build_id ?? null;
  const remoteFingerprint = data.version_fingerprint ?? null;
  const remoteSchema = data.api_schema_version ?? null;

  if (!baselineRef.initialized) {
    baselineRef.buildId = remoteBuildId;
    baselineRef.fingerprint = remoteFingerprint;
    baselineRef.apiSchemaVersion = remoteSchema;
    baselineRef.initialized = true;
    return;
  }

  if (
    remoteSchema !== null
    && baselineRef.apiSchemaVersion !== null
    && remoteSchema > baselineRef.apiSchemaVersion
  ) {
    useHealthHubStore.setState({
      newVersionAvailable: false,
      apiIncompatible: true,
      remoteVersion: data.version ?? null,
    });
    return;
  }

  const minBuildId = data.min_frontend_build_id ?? null;
  if (
    minBuildId !== null
    && baselineRef.buildId !== null
    && baselineRef.buildId !== minBuildId
    && baselineRef.buildId < minBuildId
  ) {
    useHealthHubStore.setState({
      newVersionAvailable: false,
      apiIncompatible: true,
      remoteVersion: data.version ?? null,
    });
    return;
  }

  let changed = false;
  if (remoteBuildId !== null && baselineRef.buildId !== null) {
    changed = remoteBuildId !== baselineRef.buildId;
  } else if (remoteFingerprint !== null && baselineRef.fingerprint !== null) {
    changed = remoteFingerprint !== baselineRef.fingerprint;
  }

  useHealthHubStore.setState((state) => ({
    apiIncompatible: false,
    remoteVersion: data.version ?? state.remoteVersion,
    newVersionAvailable: dismissVersionNotice ? state.newVersionAvailable : (state.newVersionAvailable || changed),
  }));
}

async function pollHealth(): Promise<void> {
  if (typeof document !== "undefined" && document.hidden) {
    schedulePoll(STEADY_INTERVAL_MS);
    return;
  }

  pollCount += 1;
  try {
    const resp = await fetch(buildDirectHealthUrl(), {
      method: "GET",
      signal: AbortSignal.timeout(10_000),
    });
    if (!resp.ok) {
      throw new Error(`health_http_${resp.status}`);
    }

    const data = (await resp.json()) as HealthPayload;
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
  dismissVersion: () => {
    dismissVersionNotice = true;
    set((state) => ({ ...state, newVersionAvailable: false }));
  },
  refreshNow: () => {
    if (typeof window !== "undefined") {
      window.location.reload();
    }
  },
}));
