import type {
  ConfigResponse,
  StatusResponse,
  LogEntry,
  UpdateInfo,
  UpdateResult,
} from "./types";

async function fetchJson<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, init);
  return res.json() as Promise<T>;
}

export const api = {
  getConfig: () => fetchJson<ConfigResponse>("/api/config"),

  getStatus: () => fetchJson<StatusResponse>("/api/status"),

  getLogs: (since: number) =>
    fetchJson<{ logs: LogEntry[] }>(`/api/logs?since=${since}`),

  checkEnv: () => fetchJson<{ ok: boolean }>("/api/check-env", { method: "POST" }),

  deploy: () =>
    fetchJson<{ ok: boolean }>("/api/deploy", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    }),

  quickStart: () =>
    fetchJson<{ ok: boolean }>("/api/quick-start", { method: "POST" }),

  stop: () => fetchJson<{ ok: boolean }>("/api/stop", { method: "POST" }),

  checkUpdateQuick: () =>
    fetchJson<UpdateInfo>("/api/check-update-quick", { method: "POST" }),

  checkUpdate: () =>
    fetchJson<UpdateInfo>("/api/update-check", { method: "POST" }),

  applyUpdate: () =>
    fetchJson<UpdateResult>("/api/update-apply", { method: "POST" }),

  createShortcut: () =>
    fetchJson<{ path?: string; error?: string }>("/api/create-shortcut", {
      method: "POST",
    }),
};
