import { create } from "zustand";
import { persist } from "zustand/middleware";
import type { Session } from "@/lib/types";
import {
  buildDefaultSessionTitle,
  isFallbackSessionTitle,
} from "@/lib/session-title";

interface SessionState {
  sessions: Session[];
  activeSessionId: string | null;
  setSessions: (sessions: Session[]) => void;
  setActiveSession: (id: string | null) => void;
  addSession: (session: Session) => void;
  removeSession: (id: string) => void;
  updateSessionTitle: (id: string, title: string) => void;
  mergeSessions: (remote: Session[]) => void;
  updateSessionStatus: (id: string, status: "active" | "archived") => void;
}

export const useSessionStore = create<SessionState>()(
  persist(
    (set) => ({
      sessions: [],
      activeSessionId: null,
      setSessions: (sessions) => set({ sessions }),
      setActiveSession: (id) => set({ activeSessionId: id }),
      addSession: (session) =>
        set((state) => {
          const withTs = { ...session, updatedAt: session.updatedAt ?? new Date().toISOString() };
          const next = [withTs, ...state.sessions].sort(
            (a, b) => (b.updatedAt ?? "").localeCompare(a.updatedAt ?? "")
          );
          return { sessions: next };
        }),
      removeSession: (id) =>
        set((state) => ({
          sessions: state.sessions.filter((s) => s.id !== id),
          activeSessionId:
            state.activeSessionId === id ? null : state.activeSessionId,
        })),
      updateSessionTitle: (id, title) =>
        set((state) => ({
          sessions: state.sessions.map((s) =>
            s.id === id ? { ...s, title } : s
          ),
        })),
      updateSessionStatus: (id, status) =>
        set((state) => ({
          sessions: state.sessions.map((s) =>
            s.id === id ? { ...s, status } : s
          ),
        })),
      mergeSessions: (remote) =>
        set((state) => {
          const localMap = new Map(state.sessions.map((s) => [s.id, s]));
          for (const rs of remote) {
            const local = localMap.get(rs.id);
            const keepLocalTitle =
              !!local
              && !isFallbackSessionTitle(local.title, rs.id)
              && isFallbackSessionTitle(rs.title, rs.id);
            const merged = { ...local, ...rs };
            const normalizedTitle = (keepLocalTitle ? local?.title : merged.title)?.trim();
            localMap.set(rs.id, {
              ...merged,
              title: normalizedTitle || buildDefaultSessionTitle(rs.id),
            });
          }

          // Remove local-only sessions not present in the backend response.
          // Keep the active session to protect optimistic creates that haven't
          // reached the server yet.
          const remoteIds = new Set(remote.map((s) => s.id));
          for (const [id] of localMap) {
            if (!remoteIds.has(id) && id !== state.activeSessionId) {
              localMap.delete(id);
            }
          }

          const merged = Array.from(localMap.values()).sort(
            (a, b) => (b.updatedAt ?? "").localeCompare(a.updatedAt ?? "")
          );
          return { sessions: merged };
        }),
    }),
    {
      name: "excelmanus-sessions",
      partialize: (state) => ({
        sessions: state.sessions,
        activeSessionId: state.activeSessionId,
      }),
    }
  )
);
