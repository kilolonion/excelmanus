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

  triggerRestart: (reason?: string) => Promise<void>;
  setDisconnected: () => void;
  setConnected: () => void;
  reset: () => void;
}

/** 解析后端直连健康检查 URL（绕过 Next.js 代理） */
function resolveHealthUrl(): string {
  return buildDirectHealthUrl();
}

const wait = (ms: number) => new Promise((r) => setTimeout(r, ms));

async function probeHealth(): Promise<boolean> {
  try {
    const r = await fetch(resolveHealthUrl(), {
      method: "GET",
      signal: AbortSignal.timeout(2000),
    });
    return r.ok;
  } catch {
    return false;
  }
}

export const useConnectionStore = create<ConnectionState>((set, get) => {
  let elapsedTimer: ReturnType<typeof setInterval> | null = null;
  let restartAborted = false;

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

  return {
    status: "connected",
    restartReason: null,
    restartTimeout: false,
    elapsedSeconds: 0,
    phase: "",

    triggerRestart: async (reason?: string) => {
      // 防止重复触发
      if (get().status === "restarting") return;

      restartAborted = false;
      set({
        status: "restarting",
        restartReason: reason || null,
        restartTimeout: false,
        phase: "正在保存配置…",
        elapsedSeconds: 0,
      });
      startElapsedTimer();

      // Phase 1: 等待后端下线（最多 15 秒）
      await wait(2000);
      if (restartAborted) return;

      set({ phase: "服务正在重启…" });
      for (let i = 0; i < 26; i++) {
        if (restartAborted) return;
        if (!(await probeHealth())) break;
        await wait(500);
      }

      // Phase 2: 等待后端上线（最多 60 秒）
      if (!restartAborted) {
        set({ phase: "正在恢复连接…" });
      }
      let online = false;
      for (let i = 0; i < 60; i++) {
        if (restartAborted) return;
        if (await probeHealth()) {
          online = true;
          break;
        }
        await wait(1000);
      }

      clearElapsedTimer();

      if (restartAborted) return;

      if (online) {
        set({ phase: "连接已恢复，正在刷新…" });
        await wait(500);
        window.location.reload();
      } else {
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
          if (await probeHealth()) {
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
