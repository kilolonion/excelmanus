import { create } from "zustand";

interface UIState {
  sidebarOpen: boolean;
  currentModel: string;
  fullAccessEnabled: boolean;
  planModeEnabled: boolean;
  settingsOpen: boolean;
  settingsTab: string;
  toggleSidebar: () => void;
  setSidebarOpen: (open: boolean) => void;
  setCurrentModel: (model: string) => void;
  setFullAccessEnabled: (enabled: boolean) => void;
  setPlanModeEnabled: (enabled: boolean) => void;
  openSettings: (tab?: string) => void;
  closeSettings: () => void;
}

export const useUIStore = create<UIState>((set) => ({
  sidebarOpen: true,
  currentModel: "",
  fullAccessEnabled: false,
  planModeEnabled: false,
  settingsOpen: false,
  settingsTab: "model",
  toggleSidebar: () => set((state) => ({ sidebarOpen: !state.sidebarOpen })),
  setSidebarOpen: (open) => set({ sidebarOpen: open }),
  setCurrentModel: (model) => set({ currentModel: model }),
  setFullAccessEnabled: (enabled) => set({ fullAccessEnabled: enabled }),
  setPlanModeEnabled: (enabled) => set({ planModeEnabled: enabled }),
  openSettings: (tab) => set({ settingsOpen: true, settingsTab: tab || "model" }),
  closeSettings: () => set({ settingsOpen: false }),
}));
