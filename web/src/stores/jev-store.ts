import { create } from "zustand";
import { persist, createJSONStorage } from "zustand/middleware";
import { parseJevTrace, type JevTrace } from "@/lib/jev-trace";

interface JevState {
  traces: JevTrace[];
  sessionId: string | null;
  pending: boolean;
  drawerOpen: boolean;
  pinned: boolean;
  railCollapsed: boolean;
  chatEnabled: boolean;
  seq: number;
  beginTurn: (sessionId?: string | null) => void;
  appendFromEvent: (data: Record<string, unknown>) => JevTrace | null;
  finishTurn: () => void;
  reset: () => void;
  setDrawerOpen: (open: boolean) => void;
  togglePinned: () => void;
  setRailCollapsed: (collapsed: boolean) => void;
  setChatEnabled: (enabled: boolean) => void;
}

export const useJevStore = create<JevState>()(
  persist(
    (set, get) => ({
      traces: [],
      sessionId: null,
      pending: false,
      drawerOpen: false,
      pinned: false,
      railCollapsed: false,
      chatEnabled: false,
      seq: 0,
      beginTurn: (sessionId) => {
        if (!get().chatEnabled) {
          set({ traces: [], sessionId: sessionId ?? get().sessionId, pending: false });
          return;
        }
        set({
          traces: [],
          sessionId: sessionId ?? get().sessionId,
          pending: true,
        });
      },
      appendFromEvent: (data) => {
        if (!get().chatEnabled) return null;
        const nextSeq = get().seq + 1;
        const parsed = parseJevTrace(data, `jev-${nextSeq}`);
        if (!parsed) return null;
        set((state) => ({
          seq: nextSeq,
          traces: [...state.traces, parsed],
          pending: true,
        }));
        return parsed;
      },
      finishTurn: () => set({ pending: false }),
      reset: () => set({ traces: [], pending: false, sessionId: null }),
      setDrawerOpen: (open) => set({ drawerOpen: open }),
      togglePinned: () =>
        set((state) => {
          const pinned = !state.pinned;
          return { pinned, drawerOpen: pinned ? true : state.drawerOpen };
        }),
      setRailCollapsed: (collapsed) => set({ railCollapsed: collapsed }),
      setChatEnabled: (enabled) =>
        set(
          enabled
            ? { chatEnabled: true }
            : {
                chatEnabled: false,
                drawerOpen: false,
                pinned: false,
                traces: [],
                pending: false,
                sessionId: null,
              },
        ),
    }),
    {
      name: "excelmanus-jev-ui",
      storage: createJSONStorage(() => {
        if (typeof window === "undefined") {
          return {
            getItem: () => null,
            setItem: () => {},
            removeItem: () => {},
          };
        }
        return localStorage;
      }),
      partialize: (state) => ({
        pinned: state.pinned,
        railCollapsed: state.railCollapsed,
      }),
    },
  ),
);
