import { create } from "zustand";
import { persist } from "zustand/middleware";
import { shallow } from "zustand/shallow";
import type { Session } from "@/lib/types";
import {
  buildDefaultSessionTitle,
  isFallbackSessionTitle,
} from "@/lib/session-title";
interface SessionState {
  sessions: Session[];
  sidebarSessionOrder: Record<string, string[]>;
  readAtByWorkspace: Record<string, string>;
  setSidebarSessionOrder: (groupKey: string, ids: string[]) => void;
  markWorkspaceRead: (groupKey: string) => void;
  activeSessionId: string | null;
  lastWorkspaceId: string | null;
  lastWorkspacePath: string | null;
  setSessions: (sessions: Session[]) => void;
  setActiveSession: (id: string | null) => void;
  addSession: (session: Session) => void;
  removeSession: (id: string) => void;
  updateSessionTitle: (id: string, title: string) => void;
  patchSession: (id: string, patch: Partial<Session>) => void;
  mergeSessions: (remote: Session[]) => void;
}

/** 当前选中会话 id 的唯一可变事实源。 */
export function getActiveSessionId(): string | null {
  return useSessionStore.getState().activeSessionId;
}

export const useSessionStore = create<SessionState>()(
  persist(
    (set, get) => ({
      sessions: [],
      sidebarSessionOrder: {},
      readAtByWorkspace: {},
      setSidebarSessionOrder: (groupKey, ids) => set((state) => ({
        sidebarSessionOrder: { ...state.sidebarSessionOrder, [groupKey]: [...new Set(ids)] },
      })),
      markWorkspaceRead: (groupKey) => set((state) => ({
        readAtByWorkspace: { ...state.readAtByWorkspace, [groupKey]: new Date().toISOString() },
      })),
      activeSessionId: null,
      lastWorkspaceId: null,
      lastWorkspacePath: null,
      setSessions: (sessions) => set({ sessions }),
      setActiveSession: (id) =>
        set((state) => {
          if (!id) return { activeSessionId: null };
          const session = state.sessions.find((item) => item.id === id);
          return {
            activeSessionId: id,
            lastWorkspaceId: session?.workspaceId ?? state.lastWorkspaceId,
            lastWorkspacePath: session?.workspacePath ?? state.lastWorkspacePath,
          };
        }),
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
      patchSession: (id, patch) => {
        const sessions = get().sessions;
        const index = sessions.findIndex((s) => s.id === id);
        if (index < 0) return;
        const next = { ...sessions[index], ...patch };
        if (shallow(sessions[index], next)) return;
        set({ sessions: sessions.map((s, i) => i === index ? next : s) });
      },
      mergeSessions: (remote) => {
          const state = get();
          const localMap = new Map(state.sessions.map((s) => [s.id, s]));
          for (const rs of remote) {
            const local = localMap.get(rs.id);
            const keepLocalTitle =
              !!local
              && !isFallbackSessionTitle(local.title, rs.id)
              && isFallbackSessionTitle(rs.title, rs.id);
            const merged = { ...local, ...rs };
            const normalizedTitle = (keepLocalTitle ? local?.title : merged.title)?.trim();
            const next = {
              ...merged,
              title: normalizedTitle || buildDefaultSessionTitle(rs.id),
            };
            localMap.set(rs.id, local && shallow(local, next) ? local : next);
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
                // Never prune onboarding demo sessions — they are local-only
                if (id.startsWith("__onboarding_demo__")) continue;
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
          // Preserve references and skip persistence/subscriber work when a poll
          // returns the same sessions. Only changed rows get a new object.
          if (merged.length === state.sessions.length && merged.every((s, i) => s === state.sessions[i])) return;
          set({ sessions: merged });
        },
    }),
    {
      name: "excelmanus-sessions",
      partialize: (state) => ({
        sessions: state.sessions,
        sidebarSessionOrder: state.sidebarSessionOrder,
        readAtByWorkspace: state.readAtByWorkspace,
        activeSessionId: state.activeSessionId,
        lastWorkspaceId: state.lastWorkspaceId,
        lastWorkspacePath: state.lastWorkspacePath,
      }),
    }
  )
);

export function waitForSessionHydration(): Promise<void> {
  const persistApi = useSessionStore.persist;
  if (persistApi.hasHydrated()) return Promise.resolve();
  return new Promise((resolve) => {
    const unsub = persistApi.onFinishHydration(() => {
      if (typeof unsub === "function") unsub();
      resolve();
    });
  });
}
