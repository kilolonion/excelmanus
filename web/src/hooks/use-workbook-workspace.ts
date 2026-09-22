import { useSessionStore } from "@/stores/session-store";
import { workspaceKeyFromSession } from "@/lib/workspace-file-ref";
import { EMPTY_WORKBOOK_WORKSPACE, useWorkbookWorkspaceStore, workbookWorkspaceKey } from "@/stores/workbook-workspace-store";

export function useWorkbookWorkspace() {
  const session = useSessionStore((state) => state.sessions.find((item) => item.id === state.activeSessionId));
  const workspaceKey = workspaceKeyFromSession(session);
  const key = workbookWorkspaceKey(session?.id, workspaceKey);
  const workspace = useWorkbookWorkspaceStore((state) => state.workspaces[key] ?? EMPTY_WORKBOOK_WORKSPACE);
  return { session, workspaceKey, key, workspace };
}
