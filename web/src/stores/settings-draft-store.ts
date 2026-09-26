import { create } from "zustand";

export type SettingValue = boolean | number | string;

// Keep edits across settings navigation, but never persist them as applied settings.
export const useSettingsDraftStore = create<{
  drafts: Record<string, SettingValue>;
  update: (key: string, value: SettingValue, original: SettingValue | undefined) => void;
  discard: (keys: string[]) => void;
}>((set) => ({
  drafts: {},
  update: (key, value, original) => set((state) => {
    const drafts = { ...state.drafts };
    if (value === original || (typeof original === "number" && value !== "" && Number(value) === original)) {
      delete drafts[key];
    } else {
      drafts[key] = value;
    }
    return { drafts };
  }),
  discard: (keys) => set((state) => ({
    drafts: Object.fromEntries(Object.entries(state.drafts).filter(([key]) => !keys.includes(key))),
  })),
}));
