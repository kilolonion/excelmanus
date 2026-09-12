import { create } from "zustand";
import { buildDirectHealthUrl } from "@/lib/backend-origin";

type ConnectionStatus = "connected" | "restarting" | "disconnected";

interface ConnectionState {
  status: ConnectionStatus;
  restartReason: string | null;
  restartTimeout: boolean;
  /** 重启/重连已等待的秒数 */
  elapsedSeconds: number;
  /** 重启阶段描述（供 UI 显示） */
  phase: string;

  triggerRestart: (reason?: string, opts?: { requireVersionChange?: boolean }) => Promise<void>;
  setDisconnected: () => void;
  setConnected: () => void;
  reset: () => void;
}

/** 解析后端直连健康检查 URL（绕过 Next.js 代理） */
function resolveHealthUrl(): string {
  return buildDirectHealthUrl();
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
    const r = await fetch(resolveHealthUrl(), {
      method: "GET",
      signal: AbortSignal.timeout(2000),
    });
    if (!r.ok) return { ok: false };
    try {
      const data = await r.json();
      return {
        ok: healthResponseIsUp(true, data.status),
        fingerprint: data.version_fingerprint ?? undefined,
        gitCommit: data.git_commit ?? undefined,
      };
    } catch {
      return { ok: true };
    }
  } catch {
    return { ok: false };
  }
}

export const useConnectionStore = create<ConnectionState>((set, get) => {
  let elapsedTimer: ReturnType<typeof setInterval> | null = null;
  let restartAborted = false;
  /** 后到的 triggerRestart(requireVersionChange) 能抬高正在进行的等待条件 */
  let requireVersionChange = false;

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

  const finishReload = async () => {
    clearElapsedTimer();
    set({ phase: "连接已恢复，正在刷新…" });
    await wait(500);
    window.location.reload();
  };

  return {
    status: "connected",
    restartReason: null,
    restartTimeout: false,
    elapsedSeconds: 0,
    phase: "",

    triggerRestart: async (reason?: string, opts?: { requireVersionChange?: boolean }) => {
      if (opts?.requireVersionChange) requireVersionChange = true;
      if (get().status === "restarting") {
        if (reason) set({ restartReason: reason });
        return;
      }

      requireVersionChange = opts?.requireVersionChange === true;
      restartAborted = false;
      set({
        status: "restarting",
        restartReason: reason || null,
        restartTimeout: false,
        phase: requireVersionChange ? "正在发起停机更新…" : "正在保存配置…",
        elapsedSeconds: 0,
      });
      startElapsedTimer();

      const baseline = await probeHealth();
      const baselineFingerprint = baseline.fingerprint;
      const baselineCommit = baseline.gitCommit;

      const hasVersionChanged = (probe: ProbeResult): boolean => {
        if (!probe.ok) return false;
        if (baselineFingerprint && probe.fingerprint && probe.fingerprint !== baselineFingerprint) return true;
        if (baselineCommit && probe.gitCommit && probe.gitCommit !== baselineCommit) return true;
        return false;
      };

      await wait(2000);
      if (restartAborted) return;

      set({ phase: requireVersionChange ? "正在停止旧进程…" : "服务正在重启…" });
      let versionChanged = false;
      let sawDown = !baseline.ok;
      const phase1Iters = requireVersionChange ? 40 : 26;
      for (let i = 0; i < phase1Iters; i++) {
        if (restartAborted) return;
        const probe = await probeHealth();
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

      if (restartAborted) return;

      if (versionChanged) {
        await finishReload();
        return;
      }

      if (!restartAborted) {
        set({
          phase: sawDown
            ? "正在安装依赖并构建，恢复后将自动刷新…"
            : "正在恢复连接…",
        });
      }
      const phase2Iters = sawDown ? 600 : 60;
      let online = false;
      for (let i = 0; i < phase2Iters; i++) {
        if (restartAborted) return;
        const probe = await probeHealth();
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

      if (restartAborted) return;

      if (online) {
        await finishReload();
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
      set({
        status: "disconnected",
        restartReason: null,
        restartTimeout: false,
        phase: "与服务器的连接已中断，正在尝试重新连接…",
        elapsedSeconds: 0,
      });
      startElapsedTimer();

      // 后台自动探活
      const autoReconnect = async () => {
        for (let i = 0; i < 120; i++) {
          if (restartAborted || get().status === "connected") return;
          if ((await probeHealth()).ok) {
            clearElapsedTimer();
            set({
              status: "connected",
              phase: "",
              restartTimeout: false,
              elapsedSeconds: 0,
            });
            // 刷新页面以恢复完整状态
            window.location.reload();
            return;
          }
          await wait(2000);
        }
        // 超时
        clearElapsedTimer();
        set({ restartTimeout: true, phase: "连接恢复超时" });
      };
      autoReconnect();
    },

    setConnected: () => {
      const current = get().status;
      if (current === "connected") return;
      restartAborted = true;
      requireVersionChange = false;
      clearElapsedTimer();
      set({
        status: "connected",
        restartReason: null,
        restartTimeout: false,
        phase: "",
        elapsedSeconds: 0,
      });
    },

    reset: () => {
      restartAborted = true;
      requireVersionChange = false;
      clearElapsedTimer();
      set({
        status: "connected",
        restartReason: null,
        restartTimeout: false,
        phase: "",
        elapsedSeconds: 0,
      });
    },
  };
});
