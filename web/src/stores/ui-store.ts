import { create } from "zustand";
import { persist } from "zustand/middleware";
import { getIsMobile, getIsDesktop } from "@/hooks/use-mobile";
import { settingsCache } from "@/lib/settings-cache";
import type { MessageDispatchMode } from "@/lib/types";
import {
  DEFAULT_THINKING_EFFORT_OPTIONS,
  type ThinkingEffort,
} from "@/lib/thinking";

interface UIState {
  sidebarOpen: boolean;
  currentModel: string;
  fullAccessEnabled: boolean;
  autoApproveEnabled: boolean;
  visionCapable: boolean | null;
  chatMode: "write" | "read" | "plan";
  messageDispatchDefault: MessageDispatchMode;
  chatModeOwned: boolean;
  thinkingEffort: string;
  thinkingEffortOptions: ThinkingEffort[];
  settingsOpen: boolean;
  settingsTab: string;
  sidebarTab: "chats" | "files";
  adminOpen: boolean;
  configReady: boolean | null;
  configError: string | null;
  configPlaceholderItems: { name: string; field: string; model: string }[];
  modelProfileVersion: number;
  toggleSidebar: () => void;
  setSidebarOpen: (open: boolean) => void;
  setCurrentModel: (model: string) => void;
  setFullAccessEnabled: (enabled: boolean) => void;
  setAutoApproveEnabled: (enabled: boolean) => void;
  setVisionCapable: (capable: boolean | null) => void;
  setChatMode: (mode: "write" | "read" | "plan") => void;
  setMessageDispatchDefault: (mode: MessageDispatchMode) => void;
  hydrateChatMode: (mode: "write" | "read" | "plan") => void;
  releaseChatModeOwnership: () => void;
  setThinkingEffort: (effort: string) => void;
  setThinkingEffortOptions: (efforts: ThinkingEffort[]) => void;
  setSidebarTab: (tab: "chats" | "files") => void;
  openSettings: (tab?: string) => void;
  closeSettings: () => void;
  openAdmin: () => void;
  closeAdmin: () => void;
  setConfigReady: (ready: boolean) => void;
  setConfigError: (error: string | null) => void;
  setConfigPlaceholderItems: (items: { name: string; field: string; model: string }[]) => void;
  bumpModelProfiles: () => void;
}

// 跨标签页模型档案同步
let _modelProfileChannel: BroadcastChannel | null = null;
if (typeof BroadcastChannel !== "undefined") {
  try {
    _modelProfileChannel = new BroadcastChannel("excelmanus-model-profiles");
  } catch { /* SSR / 不支持时静默 */ }
}

export const useUIStore = create<UIState>()(
  persist(
    (set) => ({
  // 只有在桌面端（>=1280px）时才默认打开侧边栏
  sidebarOpen: !getIsMobile() && getIsDesktop(),
  currentModel: "",
  fullAccessEnabled: false,
  autoApproveEnabled: false,
  visionCapable: null,
  chatMode: "write" as const,
  messageDispatchDefault: "steer" as const,
  chatModeOwned: false,
  thinkingEffort: "medium",
  thinkingEffortOptions: [...DEFAULT_THINKING_EFFORT_OPTIONS],
  settingsOpen: false,
  settingsTab: "model",
  sidebarTab: "chats" as const,
  adminOpen: false,
  configReady: null,
  configError: null,
  configPlaceholderItems: [],
  modelProfileVersion: 0,
  toggleSidebar: () => set((state) => ({ sidebarOpen: !state.sidebarOpen })),
  setSidebarOpen: (open) => set({ sidebarOpen: open }),
  setCurrentModel: (model) => set({ currentModel: model }),
  setFullAccessEnabled: (enabled) => set({ fullAccessEnabled: enabled }),
  setAutoApproveEnabled: (enabled) => set({ autoApproveEnabled: enabled }),
  setVisionCapable: (capable) => set({ visionCapable: capable }),
  setChatMode: (mode) => set({ chatMode: mode, chatModeOwned: true }),
  setMessageDispatchDefault: (mode) => set({ messageDispatchDefault: mode }),
  hydrateChatMode: (mode) =>
    set((s) => (s.chatModeOwned ? s : { chatMode: mode })),
  releaseChatModeOwnership: () => set({ chatModeOwned: false }),
  setThinkingEffort: (effort) => set({ thinkingEffort: effort }),
  setThinkingEffortOptions: (efforts) => set({ thinkingEffortOptions: efforts }),
  setSidebarTab: (tab) => set({ sidebarTab: tab }),
  openSettings: (tab) => set({ settingsOpen: true, settingsTab: tab || "model" }),
  closeSettings: () => set({ settingsOpen: false }),
  openAdmin: () => set({ adminOpen: true }),
  closeAdmin: () => set({ adminOpen: false }),
  setConfigReady: (ready) => set({ configReady: ready, ...(ready ? { configError: null } : {}) }),
  setConfigError: (error) => set({ configError: error }),
  setConfigPlaceholderItems: (items) => set({ configPlaceholderItems: items }),
  bumpModelProfiles: () => {
      settingsCache.delete("/config/models");
      settingsCache.delete("_capsMap");
      set((s) => ({ modelProfileVersion: s.modelProfileVersion + 1 }));
      // 跨标签页同步：通知其他标签页刷新模型列表
      try { _modelProfileChannel?.postMessage("bump"); } catch { /* 静默 */ }
    },
    }),
    {
      name: "excelmanus-ui",
      partialize: (state) => ({
        fullAccessEnabled: state.fullAccessEnabled,
        autoApproveEnabled: state.autoApproveEnabled,
      }),
      // 只恢复白名单中的可持久化偏好，其他 UI 状态始终使用当前默认值。
      merge: (persisted, current) => {
        const saved = persisted as { fullAccessEnabled?: unknown; autoApproveEnabled?: unknown } | null;
        return {
          ...current,
          ...(typeof saved?.fullAccessEnabled === "boolean"
            ? { fullAccessEnabled: saved.fullAccessEnabled }
            : {}),
          ...(typeof saved?.autoApproveEnabled === "boolean"
            ? { autoApproveEnabled: saved.autoApproveEnabled }
            : {}),
        };
      },
    }
  )
);

// 跨标签页接收：其他标签页的 bumpModelProfiles 广播到达时，本地也递增版本号
if (_modelProfileChannel) {
  _modelProfileChannel.onmessage = () => {
    settingsCache.delete("/config/models");
    settingsCache.delete("_capsMap");
    useUIStore.setState((s) => ({ modelProfileVersion: s.modelProfileVersion + 1 }));
  };
}
