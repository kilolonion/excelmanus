import { useExcelStore } from "@/stores/excel-store";
import { useSessionStore } from "@/stores/session-store";
import { useWorkbookConversationStore, workbookViewKey } from "@/stores/workbook-conversation-store";
import { normalizeRelativePath, workspaceKeyFromSession } from "@/lib/workspace-file-ref";

/** Send identities only. Cell values and workbook contents never enter this hint. */
export function buildJevSheetContext(sessionId?: string | null) {
  if (!sessionId) return undefined;
  const session = useSessionStore.getState().sessions.find((item) => item.id === sessionId);
  if (!session?.workspaceId) return undefined;
  const workspaceKey = workspaceKeyFromSession(session);
  const conversations = useWorkbookConversationStore.getState();
  const target = conversations.targets[sessionId];
  const view = target ? conversations.views[workbookViewKey(sessionId, target.file)] : undefined;
  if (target?.file.workspaceKey === workspaceKey && view?.status === "ready") {
    if (target.file.relative.length > 300) return undefined;
    return {
      workspace_id: session.workspaceId,
      path: target.file.relative,
      sheet: view.sheet || target.sheet || "",
      range: view.range || "",
    };
  }
  if (target) return undefined; // A loading or failed view must not revive stale selection state.
  const excel = useExcelStore.getState();
  if (excel.activeWorkspaceKey !== workspaceKey) return undefined;
  const path = excel.fullViewPath || (excel.panelOpen ? excel.activeFilePath : null);
  if (!path || path.length > 300) return undefined;
  const sheet = (excel.fullViewPath ? excel.fullViewSheet : excel.activeSheet) || "";
  const draft = excel.draftRange;
  const live = excel.liveSelection;
  const range = draft?.path && normalizeRelativePath(draft.path) === normalizeRelativePath(path)
    && draft.sheet === sheet ? draft.range
    : live && normalizeRelativePath(live.path) === normalizeRelativePath(path)
      && live.sheet === sheet ? live.range : "";
  return { workspace_id: session.workspaceId, path, sheet, range };
}
