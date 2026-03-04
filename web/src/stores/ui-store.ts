import { create } from "zustand";
import { persist } from "zustand/middleware";
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
  sidebarTab: "chats" | "files";
  profileOpen: boolean;
  channelsOpen: boolean;
  adminOpen: boolean;
  configReady: boolean | null;
  configError: string | null;
  configPlaceholderItems: { name: string; field: string; model: string }[];
  modelProfileVersion: number;
  toggleSidebar: () => void;
  setSidebarOpen: (open: boolean) => void;
  setCurrentModel: (model: string) => void;
  setFullAccessEnabled: (enabled: boolean) => void;
  setVisionCapable: (capable: boolean) => void;
  setChatMode: (mode: "write" | "read" | "plan") => void;
  setThinkingEffort: (effort: string) => void;
  setSidebarTab: (tab: "chats" | "files") => void;
  openSettings: (tab?: string) => void;
  closeSettings: () => void;
  openProfile: () => void;
  closeProfile: () => void;
  openChannels: () => void;
  closeChannels: () => void;
  openAdmin: () => void;
  closeAdmin: () => void;
  setConfigReady: (ready: boolean) => void;
  setConfigError: (error: string | null) => void;
  setConfigPlaceholderItems: (items: { name: string; field: string; model: string }[]) => void;
  bumpModelProfiles: () => void;
}

export const useUIStore = create<UIState>()(
  persist(
    (set) => ({
  // 只有在桌面端（>=1280px）时才默认打开侧边栏
  sidebarOpen: !getIsMobile() && getIsDesktop(),
  currentModel: "",
  fullAccessEnabled: false,
  visionCapable: false,
  chatMode: "write" as const,
  thinkingEffort: "medium",
  settingsOpen: false,
  settingsTab: "model",
  sidebarTab: "chats" as const,
  profileOpen: false,
  channelsOpen: false,
  adminOpen: false,
  configReady: null,
  configError: null,
  configPlaceholderItems: [],
  modelProfileVersion: 0,
  toggleSidebar: () => set((state) => ({ sidebarOpen: !state.sidebarOpen })),
  setSidebarOpen: (open) => set({ sidebarOpen: open }),
  setCurrentModel: (model) => set({ currentModel: model }),
  setFullAccessEnabled: (enabled) => set({ fullAccessEnabled: enabled }),
  setVisionCapable: (capable) => set({ visionCapable: capable }),
  setChatMode: (mode) => set({ chatMode: mode }),
  setThinkingEffort: (effort) => set({ thinkingEffort: effort }),
  setSidebarTab: (tab) => set({ sidebarTab: tab }),
  openSettings: (tab) => set({ settingsOpen: true, settingsTab: tab || "model" }),
  closeSettings: () => set({ settingsOpen: false }),
  openProfile: () => set({ profileOpen: true }),
  closeProfile: () => set({ profileOpen: false }),
  openChannels: () => set({ channelsOpen: true }),
  closeChannels: () => set({ channelsOpen: false }),
  openAdmin: () => set({ adminOpen: true }),
  closeAdmin: () => set({ adminOpen: false }),
  setConfigReady: (ready) => set({ configReady: ready, ...(ready ? { configError: null } : {}) }),
  setConfigError: (error) => set({ configError: error }),
  setConfigPlaceholderItems: (items) => set({ configPlaceholderItems: items }),
  bumpModelProfiles: () => set((s) => ({ modelProfileVersion: s.modelProfileVersion + 1 })),
    }),
    {
      name: "excelmanus-ui",
      partialize: (state) => ({ fullAccessEnabled: state.fullAccessEnabled }),
    }
  )
);
