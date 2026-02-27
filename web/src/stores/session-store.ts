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
          const withTs = {
            ...session,
            updatedAt: session.updatedAt ?? new Date().toISOString(),
            createdAt: session.createdAt ?? Date.now(),
          };
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

          // 移除仅存在于本地、后端未返回的会话。
          // 保留当前活跃会话，以保护尚未到达服务端的乐观创建。
          // F3：最近 30 秒内本地创建的会话也保留（宽限期），避免首条消息
          // 到达后端前过早裁剪乐观创建。
          // F7：当后端返回空列表时跳过裁剪（可能是瞬时错误），避免会话列表闪烁。
          const remoteIds = new Set(remote.map((s) => s.id));
          const GRACE_PERIOD_MS = 30_000;
          const now = Date.now();
          if (remote.length > 0) {
            for (const [id, local] of localMap) {
              if (!remoteIds.has(id) && id !== state.activeSessionId) {
                if (local.createdAt && (now - local.createdAt) < GRACE_PERIOD_MS) {
                  continue;
                }
                localMap.delete(id);
              }
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
