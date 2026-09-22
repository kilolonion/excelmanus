import { useExcelStore } from "@/stores/excel-store";
import { useSessionStore } from "@/stores/session-store";
import { useWordStore } from "@/stores/word-store";
import { useWorkbookConversationStore, workbookViewKey, type WorkbookConversationTarget } from "@/stores/workbook-conversation-store";
import { fileRefFromSession, normalizeRelativePath, workspaceKeyFromSession } from "./workspace-file-ref";
import { useWorkbookWorkspaceStore, visibleWorkbookPaths, workbookWorkspaceKey } from "@/stores/workbook-workspace-store";

export interface WorkbookSheetContext {
  workspace_id: string;
  path: string;
  sheet: string;
  range: string;
  observed_version?: string;
}

/** Captured alongside a user message so retry does not adopt newly opened panes. */
export interface WorkbookMessageContext {
  sheet_context?: WorkbookSheetContext;
  sheet_contexts: WorkbookSheetContext[];
}

/** The visible preview wins for this send; hiding it restores the discussion target. */
export function currentWorkbookSendContext(sessionId: string | null | undefined) {
  if (!sessionId || useSessionStore.getState().activeSessionId !== sessionId) return null;
  if (useWordStore.getState().fullViewPath) return null;
  const session = useSessionStore.getState().sessions.find((item) => item.id === sessionId);
  const workspaceKey = workspaceKeyFromSession(session);
  const conversations = useWorkbookConversationStore.getState();
  const bound = conversations.targets[sessionId];
  const discussion = bound?.file.workspaceKey === workspaceKey ? bound : undefined;
  const excel = useExcelStore.getState();
  // In a multi-pane workbook view the focused pane is the visible context;
  // the discussion target remains the first opened workbook until the user
  // explicitly promotes another file.
  const visiblePath = excel.activeWorkspaceKey === workspaceKey && !excel.compareMode
    ? (excel.panelOpen
      ? excel.activeFilePath
      : excel.fullViewPath ? (excel.activeFilePath || excel.fullViewPath) : null)
    : null;
  const isPreview = Boolean(visiblePath && normalizeRelativePath(visiblePath) !== discussion?.file.relative);
  const target: WorkbookConversationTarget | undefined = visiblePath
    ? { file: fileRefFromSession(visiblePath, session),
      sheet: (visiblePath === excel.activeFilePath ? excel.activeSheet : excel.fullViewSheet) ?? (isPreview ? undefined : discussion?.sheet),
      showSheet: true, layout: excel.fullViewLayout }
    : discussion;
  if (!target) return null;
  const view = conversations.views[workbookViewKey(sessionId, target.file)];
  const sheet = view?.sheet ?? target.sheet;
  return { target: { ...target, sheet }, view, isPreview };
}

/** The group is ordered primary first; the singular context identifies focus. */
export function currentWorkbookGroupContexts(sessionId: string | null | undefined): WorkbookSheetContext[] {
  if (!sessionId || useSessionStore.getState().activeSessionId !== sessionId || useWordStore.getState().fullViewPath) return [];
  const session = useSessionStore.getState().sessions.find((item) => item.id === sessionId);
  const workspaceKey = workspaceKeyFromSession(session);
  const excel = useExcelStore.getState();
  if (!session?.workspaceId || excel.activeWorkspaceKey !== workspaceKey || !excel.fullViewPath || excel.panelOpen || excel.compareMode) return [];
  const workspace = useWorkbookWorkspaceStore.getState().workspaces[workbookWorkspaceKey(sessionId, workspaceKey)];
  if (!workspace || workspace.files.length < 2) return [];
  const visible = workspace.visible ?? visibleWorkbookPaths(workspace);
  // The primary identity is retained on a narrow screen even if only a reference
  // is visible. No stale range or version is fabricated for an unloaded file.
  const paths = [...new Set([workspace.files[0].path, ...visible])].slice(0, 3);
  const views = useWorkbookConversationStore.getState().views;
  return paths.flatMap((path) => {
    const file = fileRefFromSession(path, session);
    const view = visible.includes(path) ? views[workbookViewKey(sessionId, file)] : undefined;
    if (path.length > 300) return [];
    return [{ workspace_id: session.workspaceId!, path, sheet: view?.status === "ready" ? view.sheet ?? "" : "",
      range: view?.status === "ready" && view.range && view.range.length <= 4096 ? view.range : "",
      ...(view?.status === "ready" && view.version ? { observed_version: view.version } : {}) }];
  });
}

export function workbookSheetContext(context: NonNullable<ReturnType<typeof currentWorkbookSendContext>>): WorkbookSheetContext | undefined {
  const { target, view } = context;
  if (!target.file.workspaceId || view?.status !== "ready" || target.file.relative.length > 300) return undefined;
  return {
    workspace_id: target.file.workspaceId,
    path: target.file.relative,
    sheet: target.sheet ?? "",
    range: view.range && view.range.length <= 4096 ? view.range : "",
    ...(view.version ? { observed_version: view.version } : {}),
  };
}
