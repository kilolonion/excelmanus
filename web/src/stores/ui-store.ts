import { create } from "zustand";
import { getIsMobile, getIsDesktop } from "@/hooks/use-mobile";

interface UIState {
  sidebarOpen: boolean;
  currentModel: string;
  fullAccessEnabled: boolean;
  visionCapable: boolean;
  chatMode: "write" | "read" | "plan";
  thinkingEffort: string;
  settingsOpen: boolean;
  settingsTab: string;
  configReady: boolean | null;
  configError: string | null;
  configPlaceholderItems: { name: string; field: string; model: string }[];
  toggleSidebar: () => void;
  setSidebarOpen: (open: boolean) => void;
  setCurrentModel: (model: string) => void;
  setFullAccessEnabled: (enabled: boolean) => void;
  setVisionCapable: (capable: boolean) => void;
  setChatMode: (mode: "write" | "read" | "plan") => void;
  setThinkingEffort: (effort: string) => void;
  openSettings: (tab?: string) => void;
  closeSettings: () => void;
  setConfigReady: (ready: boolean) => void;
  setConfigError: (error: string | null) => void;
  setConfigPlaceholderItems: (items: { name: string; field: string; model: string }[]) => void;
}

export const useUIStore = create<UIState>((set) => ({
  // 只有在桌面端（>=1280px）时才默认打开侧边栏
  sidebarOpen: !getIsMobile() && getIsDesktop(),
  currentModel: "",
  fullAccessEnabled: false,
  visionCapable: false,
  chatMode: "write" as const,
  thinkingEffort: "medium",
  settingsOpen: false,
  settingsTab: "model",
  configReady: null,
  configError: null,
  configPlaceholderItems: [],
  toggleSidebar: () => set((state) => ({ sidebarOpen: !state.sidebarOpen })),
  setSidebarOpen: (open) => set({ sidebarOpen: open }),
  setCurrentModel: (model) => set({ currentModel: model }),
  setFullAccessEnabled: (enabled) => set({ fullAccessEnabled: enabled }),
  setVisionCapable: (capable) => set({ visionCapable: capable }),
  setChatMode: (mode) => set({ chatMode: mode }),
  setThinkingEffort: (effort) => set({ thinkingEffort: effort }),
  openSettings: (tab) => set({ settingsOpen: true, settingsTab: tab || "model" }),
  closeSettings: () => set({ settingsOpen: false }),
  setConfigReady: (ready) => set({ configReady: ready, ...(ready ? { configError: null } : {}) }),
  setConfigError: (error) => set({ configError: error }),
  setConfigPlaceholderItems: (items) => set({ configPlaceholderItems: items }),
}));
