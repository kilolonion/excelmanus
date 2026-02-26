import { create } from "zustand";
import { getIsMobile, getIsDesktop } from "@/hooks/use-mobile";

interface UIState {
  sidebarOpen: boolean;
  currentModel: string;
  fullAccessEnabled: boolean;
  visionCapable: boolean;
  chatMode: "write" | "read" | "plan";
  settingsOpen: boolean;
  settingsTab: string;
  toggleSidebar: () => void;
  setSidebarOpen: (open: boolean) => void;
  setCurrentModel: (model: string) => void;
  setFullAccessEnabled: (enabled: boolean) => void;
  setVisionCapable: (capable: boolean) => void;
  setChatMode: (mode: "write" | "read" | "plan") => void;
  openSettings: (tab?: string) => void;
  closeSettings: () => void;
}

export const useUIStore = create<UIState>((set) => ({
  // 只有在桌面端（>=1280px）时才默认打开侧边栏
  sidebarOpen: !getIsMobile() && getIsDesktop(),
  currentModel: "",
  fullAccessEnabled: false,
  visionCapable: false,
  chatMode: "write" as const,
  settingsOpen: false,
  settingsTab: "model",
  toggleSidebar: () => set((state) => ({ sidebarOpen: !state.sidebarOpen })),
  setSidebarOpen: (open) => set({ sidebarOpen: open }),
  setCurrentModel: (model) => set({ currentModel: model }),
  setFullAccessEnabled: (enabled) => set({ fullAccessEnabled: enabled }),
  setVisionCapable: (capable) => set({ visionCapable: capable }),
  setChatMode: (mode) => set({ chatMode: mode }),
  openSettings: (tab) => set({ settingsOpen: true, settingsTab: tab || "model" }),
  closeSettings: () => set({ settingsOpen: false }),
}));
