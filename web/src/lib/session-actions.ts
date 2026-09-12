import { createSession } from "@/lib/api";
import { buildDefaultSessionTitle } from "@/lib/session-title";
import { useChatStore } from "@/stores/chat-store";
import { useSessionStore } from "@/stores/session-store";
import type { Session } from "@/lib/types";

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
}): Promise<Session> {
  const created = mapCreatedSession(await createSession(opts));
  const store = useSessionStore.getState();
  if (!store.sessions.some((s) => s.id === created.id)) {
    store.addSession(created);
  }
  store.setActiveSession(created.id);
  useChatStore.getState().switchSession(created.id);
  return created;
}
