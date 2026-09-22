import { fetchWorkbookView, uploadFile } from "@/lib/api";
import { isSpreadsheetFile } from "@/lib/file-kind";
import { ensureLandingSession } from "@/lib/session-actions";
import { fileRefFromSession, isScopedWorkspaceKey, workspaceKeyFromSession } from "@/lib/workspace-file-ref";
import { openWorkspaceFile } from "@/lib/open-workspace-file";
import { prefetchExcelView } from "@/lib/excel-view-prefetch";
import { useSessionStore, waitForSessionHydration } from "@/stores/session-store";
import { useExcelStore } from "@/stores/excel-store";
import { useWorkbookConversationStore } from "@/stores/workbook-conversation-store";
import { fileBaseName } from "@/lib/revision-display";
import type { Session } from "@/lib/types";
import type { WorkbookViewLayout } from "@/lib/workspace-surface";

export async function ensureWorkbookSession(): Promise<Session> {
  await waitForSessionHydration();
  const state = useSessionStore.getState();
  const existing = state.sessions.find((session) => session.id === state.activeSessionId);
  if (existing && isScopedWorkspaceKey(workspaceKeyFromSession(existing))) return existing;
  const created = await ensureLandingSession();
  const current = useSessionStore.getState();
  const session = current.sessions.find((item) => item.id === current.activeSessionId) ?? created;
  if (!session || !isScopedWorkspaceKey(workspaceKeyFromSession(session))) {
    throw new Error("暂时无法打开工作区，请重试");
  }
  return session;
}

export function assertWorkbookSession(session: Session): void {
  const state = useSessionStore.getState();
  const active = state.sessions.find((item) => item.id === state.activeSessionId);
  if (active?.id !== session.id || workspaceKeyFromSession(active) !== workspaceKeyFromSession(session)) {
    throw new Error("已切换对话，请在当前工作区重新选择表格");
  }
}

/** Shared import pipeline for the picker and chat attachments; no composer dependency. */
export async function importWorkspaceFile(file: File, session: Session) {
  assertWorkbookSession(session);
  const result = await uploadFile(file, session.id, session.workspaceId);
  if (result.path && isSpreadsheetFile(result.filename || file.name)) {
    useExcelStore.getState().addRecentFile(result, workspaceKeyFromSession(session));
  }
  // Cache invalidation is safe even if the user switched away during the upload.
  useExcelStore.getState().bumpWorkspaceFilesVersion();
  return result;
}

/** Read-only admission. The caller's AbortSignal prevents stale opens stealing focus. */
export async function openWorkbookForConversation(path: string, session: Session, opts?: {
  sheet?: string;
  signal?: AbortSignal;
  layout?: WorkbookViewLayout;
  showSheet?: boolean;
  makePrimary?: boolean;
}) {
  assertWorkbookSession(session);
  if (!isSpreadsheetFile(path)) throw new Error("请选择 Excel 或 CSV 表格");
  opts?.signal?.throwIfAborted();
  const file = fileRefFromSession(path, session);
  prefetchExcelView();
  const view = await fetchWorkbookView({
    path: file.relative, workspaceKey: file.workspaceKey, workspaceId: file.workspaceId,
    sessionId: session.id, sheet: opts?.sheet, withStyles: false, signal: opts?.signal,
  });
  opts?.signal?.throwIfAborted();
  assertWorkbookSession(session);
  if (!view.sheets.length) throw new Error("文件无工作表");
  const excel = useExcelStore.getState();
  excel.rebindSession(excel.activeWorkspaceKey, file.workspaceKey);
  const sheet = view.active_sheet ?? view.windows[0]?.sheet;
  if (opts?.showSheet === false) {
    // Changing the discussion target from chat must not switch the visible surface.
    excel.addRecentFile({ path: file.relative, filename: fileBaseName(file.relative) }, file.workspaceKey);
    useExcelStore.setState({ activeFilePath: file.relative, activeSheet: sheet ?? null });
    const conversation = useWorkbookConversationStore.getState();
    conversation.bind(session.id, file, sheet, opts.layout);
    excel.setPrimaryWorkbook(file.relative, sheet);
    conversation.setShowSheet(session.id, false);
    conversation.observe(session.id, file, { status: "ready", sheet, version: view.content_version });
    return;
  }
  excel.closeCompare();
  openWorkspaceFile(file.relative, {
    intent: "full", sheet,
    sessionId: session.id, workspaceId: session.workspaceId,
    workbookLayout: opts?.layout,
  });
  if (opts?.makePrimary) excel.setPrimaryWorkbook(file.relative);
}
