import type { Session, WorkspaceFolder } from "@/lib/types";

export function normalizeWorkspacePath(path?: string | null): string {
  return (path || "").trim().replace(/\\/g, "/").replace(/\/+$/, "");
}

export function matchWorkspace(
  workspaces: WorkspaceFolder[],
  workspaceId?: string | null,
  workspacePath?: string | null,
): { workspaceId?: string | null; workspacePath?: string | null } | null {
  const id = workspaceId?.trim() || "";
  const path = normalizeWorkspacePath(workspacePath);
  if (id) {
    const byId = workspaces.find((ws) => ws.id === id);
    if (byId) return { workspaceId: byId.id, workspacePath: byId.path };
  }
  if (path) {
    const byPath = workspaces.find((ws) => normalizeWorkspacePath(ws.path) === path);
    if (byPath) return { workspaceId: byPath.id, workspacePath: byPath.path };
  }
  if ((id || path) && workspaces.length === 0) {
    return {
      workspaceId: id || null,
      workspacePath: workspacePath ?? null,
    };
  }
  return null;
}

/** 上次打开/改动/使用的聊天工作区；都不可用时回退列表中的第一个工作区。 */
export function resolvePreferredWorkspace(input: {
  sessions: Session[];
  workspaces: WorkspaceFolder[];
  lastWorkspaceId?: string | null;
  lastWorkspacePath?: string | null;
  lastOpenedSessionId?: string | null;
}): { workspaceId?: string | null; workspacePath?: string | null } {
  const fromLast = matchWorkspace(
    input.workspaces,
    input.lastWorkspaceId,
    input.lastWorkspacePath,
  );
  if (fromLast) return fromLast;

  const opened = input.lastOpenedSessionId
    ? input.sessions.find((session) => session.id === input.lastOpenedSessionId)
    : undefined;
  const fromOpened = matchWorkspace(
    input.workspaces,
    opened?.workspaceId,
    opened?.workspacePath,
  );
  if (fromOpened) return fromOpened;

  const sorted = [...input.sessions].sort((a, b) =>
    (b.updatedAt ?? "").localeCompare(a.updatedAt ?? ""),
  );
  for (const session of sorted) {
    const found = matchWorkspace(
      input.workspaces,
      session.workspaceId,
      session.workspacePath,
    );
    if (found) return found;
  }

  const first = input.workspaces[0];
  if (first) return { workspaceId: first.id, workspacePath: first.path };
  return {};
}
