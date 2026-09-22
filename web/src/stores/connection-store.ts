import { create } from "zustand";
import { apiGet, type WebUpgradeStatus } from "@/lib/api";
import { refreshApp } from "@/lib/app-refresh";
import { fetchWebBuild } from "@/lib/web-version";

export const WEB_UPGRADE_REQUEST_KEY = "excelmanus:web-upgrade-request";
type RestartOptions = { requireVersionChange?: boolean; upgradeRequestId?: string };

type ConnectionStatus = "connected" | "restarting" | "disconnected";

interface ConnectionState {
  status: ConnectionStatus;
  restartReason: string | null;
  restartTimeout: boolean;
  /** 重启/重连已等待的秒数 */
  elapsedSeconds: number;
  /** 重启阶段描述（供 UI 显示） */
  phase: string;
  restartError: string | null;

  triggerRestart: (reason?: string, opts?: RestartOptions) => Promise<void>;
  setDisconnected: () => void;
  setConnected: () => void;
  reset: () => void;
}

export function upgradeResultForRequest(status: WebUpgradeStatus, requestId: string): "pending" | "success" | "failed" {
  if (status.request_id !== requestId) return "pending";
  return status.ok === true ? "success" : status.ok === false ? "failed" : "pending";
}

const wait = (ms: number) => new Promise((r) => setTimeout(r, ms));

interface ProbeResult {
  ok: boolean;
  fingerprint?: string;
  gitCommit?: string;
}

/** HTTP 200 且非 draining 才算服务可用。 */
export function healthResponseIsUp(httpOk: boolean, status?: string): boolean {
  return httpOk && status !== "draining";
}

/**
 * 重启 overlay 何时刷新页面。
 * 进程先下线再起来（sawDown）就算完成，不再死等指纹——
 * ff-only 失败时 helper 仍会拉起旧版本。
 */
export function restartShouldReload(opts: {
  probeOk: boolean;
  versionChanged: boolean;
  sawDown: boolean;
  requireVersionChange: boolean;
}): boolean {
  if (!opts.probeOk) return false;
  if (opts.versionChanged) return true;
  if (opts.sawDown) return true;
  return !opts.requireVersionChange;
}

async function probeHealth(): Promise<ProbeResult> {
  try {
    const data = await apiGet<{
      status?: string;
      version_fingerprint?: string;
      git_commit?: string;
    }>("/health", {
      direct: true,
      timeoutMs: 2_000,
      cache: "no-store",
    });
    return {
      ok: healthResponseIsUp(true, data.status),
      fingerprint: data.version_fingerprint ?? undefined,
      gitCommit: data.git_commit ?? undefined,
    };
  } catch {
    return { ok: false };
  }
}

export const useConnectionStore = create<ConnectionState>((set, get) => {
  let elapsedTimer: ReturnType<typeof setInterval> | null = null;
  let restartAborted = false;
  /** 后到的 triggerRestart(requireVersionChange) 能抬高正在进行的等待条件 */
  let requireVersionChange = false;
  let upgradeRequestId: string | undefined;
  let monitoredUpgradeId: string | undefined;
  let restartGeneration = 0;

  const clearElapsedTimer = () => {
    if (elapsedTimer) {
      clearInterval(elapsedTimer);
      elapsedTimer = null;
    }
  };

  const startElapsedTimer = () => {
    clearElapsedTimer();
    set({ elapsedSeconds: 0 });
    elapsedTimer = setInterval(() => {
      set((s) => ({ elapsedSeconds: s.elapsedSeconds + 1 }));
    }, 1000);
  };

  const finishReload = async (generation: number) => {
    clearElapsedTimer();
    set({ phase: "连接已恢复，正在刷新…" });
    await wait(500);
    if (restartAborted || generation !== restartGeneration) return;
    const blocked = refreshApp();
    if (blocked) set({ restartError: blocked, phase: "新版已就绪，请先保存当前工作" });
  };

  return {
    status: "connected",
    restartReason: null,
    restartTimeout: false,
    elapsedSeconds: 0,
    phase: "",
    restartError: null,

    triggerRestart: async (reason?: string, opts?: RestartOptions) => {
      if (opts?.requireVersionChange) requireVersionChange = true;
      if (opts?.upgradeRequestId) {
        upgradeRequestId = opts.upgradeRequestId;
        try { sessionStorage.setItem(WEB_UPGRADE_REQUEST_KEY, upgradeRequestId); } catch { /* storage may be disabled */ }
      }
      if (get().status === "restarting" && !get().restartTimeout && !get().restartError &&
          (!opts?.upgradeRequestId || monitoredUpgradeId === opts.upgradeRequestId)) {
        if (reason) set({ restartReason: reason });
        return;
      }

      requireVersionChange = opts?.requireVersionChange === true;
      restartAborted = false;
      const generation = ++restartGeneration;
      const interrupted = () => restartAborted || generation !== restartGeneration;
      monitoredUpgradeId = upgradeRequestId;
      set({
        status: "restarting",
        restartReason: reason || null,
        restartTimeout: false,
        restartError: null,
        phase: requireVersionChange ? "正在发起停机更新…" : "正在保存配置…",
        elapsedSeconds: 0,
      });
      startElapsedTimer();

      // A durable request ID distinguishes this update from an older success,
      // a plain restart, or a failed update which merely brought the API back.
      if (upgradeRequestId) {
        const requested = upgradeRequestId;
        for (let i = 0; i < 1200; i++) {
          if (interrupted()) return;
          try {
            const result = await apiGet<WebUpgradeStatus>("/version/upgrade/status", { cache: "no-store", timeoutMs: 2_000 });
            if (interrupted()) return;
            if (result.request_id === requested && result.phase) set({ phase: result.phase });
            const outcome = upgradeResultForRequest(result, requested);
            if (outcome === "failed") {
              clearElapsedTimer();
              try { sessionStorage.removeItem(WEB_UPGRADE_REQUEST_KEY); } catch { /* ignore */ }
              set({ restartError: result.error || "更新未完成，请查看日志后重试", phase: "更新未完成" });
              return;
            }
            if (outcome === "success" && (await probeHealth()).ok && await fetchWebBuild()) {
              if (interrupted()) return;
              try { sessionStorage.removeItem(WEB_UPGRADE_REQUEST_KEY); } catch { /* ignore */ }
              await finishReload(generation);
              return;
            }
          } catch {
            if (interrupted()) return;
            set({ phase: "正在更新程序、安装依赖或重建网页，服务恢复后会核对更新结果…" });
          }
          await wait(1000);
        }
        if (interrupted()) return;
        clearElapsedTimer();
        set({ restartTimeout: true, phase: "更新仍未恢复连接，请查看日志或稍后重试" });
        return;
      }

      const baseline = await probeHealth();
      if (interrupted()) return;
      const baselineFingerprint = baseline.fingerprint;
      const baselineCommit = baseline.gitCommit;

      const hasVersionChanged = (probe: ProbeResult): boolean => {
        if (!probe.ok) return false;
        if (baselineFingerprint && probe.fingerprint && probe.fingerprint !== baselineFingerprint) return true;
        if (baselineCommit && probe.gitCommit && probe.gitCommit !== baselineCommit) return true;
        return false;
      };

      await wait(2000);
      if (interrupted()) return;

      set({ phase: requireVersionChange ? "正在停止旧进程…" : "服务正在重启…" });
      let versionChanged = false;
      let sawDown = !baseline.ok;
      const phase1Iters = requireVersionChange ? 40 : 26;
      for (let i = 0; i < phase1Iters; i++) {
        if (interrupted()) return;
        const probe = await probeHealth();
        if (interrupted()) return;
        if (!probe.ok) {
          sawDown = true;
          break;
        }
        if (!requireVersionChange && hasVersionChanged(probe)) {
          versionChanged = true;
          break;
        }
        await wait(500);
      }

      if (interrupted()) return;

      if (versionChanged) {
        await finishReload(generation);
        return;
      }

      if (!interrupted()) {
        set({
          phase: sawDown
            ? "正在安装依赖并构建，恢复后将自动刷新…"
            : "正在恢复连接…",
        });
      }
      const phase2Iters = sawDown ? 600 : 60;
      let online = false;
      for (let i = 0; i < phase2Iters; i++) {
        if (interrupted()) return;
        const probe = await probeHealth();
        if (interrupted()) return;
        if (
          restartShouldReload({
            probeOk: probe.ok,
            versionChanged: hasVersionChanged(probe),
            sawDown,
            requireVersionChange,
          })
        ) {
          online = true;
          break;
        }
        await wait(1000);
      }

      if (interrupted()) return;

      if (online) {
        await finishReload(generation);
      } else {
        clearElapsedTimer();
        set({ restartTimeout: true, phase: "重启超时" });
      }
    },

    setDisconnected: () => {
      const current = get().status;
      // 如果已经在 restarting 状态，不降级到 disconnected
      if (current === "restarting") return;
      if (current === "disconnected") return;

      restartAborted = false;
      const generation = ++restartGeneration;
      const interrupted = () => restartAborted || generation !== restartGeneration || get().status !== "disconnected";
      set({
        status: "disconnected",
        restartError: null,
        restartReason: null,
        restartTimeout: false,
        phase: "与服务器的连接已中断，正在尝试重新连接…",
        elapsedSeconds: 0,
      });
      startElapsedTimer();

      // 后台自动探活
      const autoReconnect = async () => {
        for (let i = 0; i < 120; i++) {
          if (interrupted()) return;
          const probe = await probeHealth();
          if (interrupted()) return;
          if (probe.ok) {
            clearElapsedTimer();
            const blocked = refreshApp();
            set({
              status: blocked ? "restarting" : "connected",
              phase: blocked ? "连接已恢复，请先保存当前工作" : "",
              restartError: blocked,
              restartTimeout: false,
              elapsedSeconds: 0,
            });
            return;
          }
          await wait(2000);
        }
        // 超时
        if (interrupted()) return;
        clearElapsedTimer();
        set({ restartTimeout: true, phase: "连接恢复超时" });
      };
      autoReconnect();
    },

    setConnected: () => {
      const current = get().status;
      if (current === "connected") return;
      restartAborted = true;
      restartGeneration += 1;
      monitoredUpgradeId = undefined;
      requireVersionChange = false;
      upgradeRequestId = undefined;
      clearElapsedTimer();
      set({
        status: "connected",
        restartReason: null,
        restartTimeout: false,
        phase: "",
        restartError: null,
        elapsedSeconds: 0,
      });
    },

    reset: () => {
      restartAborted = true;
      restartGeneration += 1;
      monitoredUpgradeId = undefined;
      requireVersionChange = false;
      upgradeRequestId = undefined;
      clearElapsedTimer();
      set({
        status: "connected",
        restartReason: null,
        restartTimeout: false,
        phase: "",
        restartError: null,
        elapsedSeconds: 0,
      });
    },
  };
});
