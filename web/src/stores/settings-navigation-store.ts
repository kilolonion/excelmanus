import { create } from "zustand";

export const useSettingsNavigationStore = create<{
  runtimeCategory: string;
  targetKey: string | null;
  targetVersion: number;
}>(() => ({ runtimeCategory: "conversation", targetKey: null, targetVersion: 0 }));
