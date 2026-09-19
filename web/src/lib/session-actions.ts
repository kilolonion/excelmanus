import { createSession, fetchWorkspaces } from "@/lib/api";
import { DEMO_SESSION_PREFIX } from "@/components/onboarding/demo-session";
import { resolvePreferredWorkspace } from "@/lib/preferred-workspace";
import { buildDefaultSessionTitle } from "@/lib/session-title";
import { useChatStore } from "@/stores/chat-store";
import { useSessionStore } from "@/stores/session-store";
import type { Session, WorkspaceFolder } from "@/lib/types";

function mapCreatedSession(raw: {
  id: string;
  title: string;
  message_count: number;
  in_flight: boolean;
  updated_at: string;
  workspace_path: string;
  workspace_id: string | null;
  workspace_title: string;
  blank: boolean;
}): Session {
  return {
    id: raw.id,
    title: (raw.title || "").trim() || buildDefaultSessionTitle(raw.id),
    messageCount: raw.message_count ?? 0,
    inFlight: raw.in_flight ?? false,
    updatedAt: raw.updated_at,
    workspacePath: raw.workspace_path,
    workspaceId: raw.workspace_id,
    workspaceTitle: raw.workspace_title,
    blank: raw.blank,
    pendingApproval: false,
    pendingQuestion: false,
    createdAt: Date.now(),
  };
}

export async function createOrReuseSession(opts?: {
  workspaceId?: string | null;
  workspacePath?: string | null;
  activate?: boolean;
}): Promise<Session> {
  const created = mapCreatedSession(await createSession({
    workspaceId: opts?.workspaceId,
    workspacePath: opts?.workspacePath,
  }));
  const store = useSessionStore.getState();
  if (!store.sessions.some((s) => s.id === created.id)) {
    store.addSession(created);
  } else {
    store.patchSession(created.id, created);
  }
  if (opts?.activate !== false) {
    store.setActiveSession(created.id);
    useChatStore.getState().switchSession(created.id);
  }
  return created;
}

let landingInFlight: Promise<Session | null> | null = null;

/** 进入应用或当前没有会话时，保证首选工作区有一条可复用的空白「新对话」。 */
export async function ensureLandingSession(opts?: {
  workspaces?: WorkspaceFolder[];
}): Promise<Session | null> {
  if (landingInFlight) return landingInFlight;
  landingInFlight = ensureLandingSessionOnce(opts).finally(() => {
    landingInFlight = null;
  });
  return landingInFlight;
}

async function ensureLandingSessionOnce(opts?: {
  workspaces?: WorkspaceFolder[];
}): Promise<Session | null> {
  const store = useSessionStore.getState();
  const currentActive = store.activeSessionId;
  if (currentActive?.startsWith(DEMO_SESSION_PREFIX)) return null;

  let workspaces = opts?.workspaces;
  if (!workspaces) {
    try {
      workspaces = await fetchWorkspaces();
    } catch {
      workspaces = [];
    }
  }

  const preferred = resolvePreferredWorkspace({
    sessions: store.sessions,
    workspaces,
    lastWorkspaceId: store.lastWorkspaceId,
    lastWorkspacePath: store.lastWorkspacePath,
    lastOpenedSessionId: currentActive,
  });
  const keepActive = Boolean(
    currentActive && store.sessions.some((session) => session.id === currentActive),
  );
  return createOrReuseSession({
    ...preferred,
    activate: !keepActive,
  });
}
