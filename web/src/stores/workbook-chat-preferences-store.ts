import { create } from "zustand";
import { persist } from "zustand/middleware";

export const WORKBOOK_CHAT_LEARNING_WINDOW_MS = 10_000;
const SAMPLE_COUNT = 5;
const CHANGE_THRESHOLD = 4;

interface WorkbookChatPreferences {
  autoReturnToChat: boolean;
  learnFromNavigation: boolean;
  recentChoices: boolean[];
  preferenceVersion: number;
  setAutoReturnToChat: (enabled: boolean) => void;
  setLearnFromNavigation: (enabled: boolean) => void;
  recordChoice: (returnToChat: boolean) => void;
}

/** Only local preferences and five anonymous choices survive a reload. */
export const useWorkbookChatPreferencesStore = create<WorkbookChatPreferences>()(persist((set) => ({
  autoReturnToChat: true,
  learnFromNavigation: true,
  recentChoices: [],
  preferenceVersion: 0,
  setAutoReturnToChat: (autoReturnToChat) => set((state) => ({
    autoReturnToChat, recentChoices: [], preferenceVersion: state.preferenceVersion + 1,
  })),
  setLearnFromNavigation: (learnFromNavigation) => set((state) => ({
    learnFromNavigation, recentChoices: [], preferenceVersion: state.preferenceVersion + 1,
  })),
  recordChoice: (returnToChat) => set((state) => {
    if (!state.learnFromNavigation) return state;
    const recentChoices = [...state.recentChoices, returnToChat].slice(-SAMPLE_COUNT);
    const contraryCount = recentChoices.filter((choice) => choice !== state.autoReturnToChat).length;
    if (recentChoices.length === SAMPLE_COUNT && contraryCount >= CHANGE_THRESHOLD) {
      // Start fresh after adapting, so old samples cannot immediately reverse the decision.
      return { autoReturnToChat: !state.autoReturnToChat, recentChoices: [] };
    }
    return { recentChoices };
  }),
}), {
  name: "excelmanus-workbook-chat-preferences",
  partialize: (state) => ({
    autoReturnToChat: state.autoReturnToChat,
    learnFromNavigation: state.learnFromNavigation,
    recentChoices: state.recentChoices,
  }),
  merge: (persisted, current) => {
    const saved = persisted as Partial<Record<keyof WorkbookChatPreferences, unknown>> | null;
    return {
      ...current,
      autoReturnToChat: typeof saved?.autoReturnToChat === "boolean" ? saved.autoReturnToChat : current.autoReturnToChat,
      learnFromNavigation: typeof saved?.learnFromNavigation === "boolean" ? saved.learnFromNavigation : current.learnFromNavigation,
      recentChoices: Array.isArray(saved?.recentChoices)
        ? saved.recentChoices.filter((choice): choice is boolean => typeof choice === "boolean").slice(-SAMPLE_COUNT)
        : [],
    };
  },
}));
