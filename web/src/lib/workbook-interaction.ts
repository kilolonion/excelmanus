import { workbookFocusRanges } from "@/lib/workbook-focus";
import { normalizeRelativePath } from "@/lib/workspace-file-ref";
import { useSessionStore } from "@/stores/session-store";
import { useExcelStore } from "@/stores/excel-store";
import { useChatStore } from "@/stores/chat-store";
import { useWorkbookFocusStore } from "@/stores/workbook-focus-store";
import { useWorkbookInteractionStore } from "@/stores/workbook-interaction-store";
import { openWorkspaceFile } from "@/lib/open-workspace-file";
import { fileRefFromSession } from "@/lib/workspace-file-ref";
import type { WorkbookTarget, WorkbookPresentation } from "@/lib/workbook-interaction-types";
export { parseWorkbookTarget, parseWorkbookPresentation } from "@/lib/workbook-interaction-types";
export type { WorkbookTarget, WorkbookPresentation } from "@/lib/workbook-interaction-types";

export function openWorkbookTarget(target: WorkbookTarget, sessionId: string, stage: "selection" | WorkbookPresentation["stage"]): boolean {
  const state = useSessionStore.getState();
  const session = state.sessions.find((s) => s.id === sessionId);
  if (state.activeSessionId !== sessionId || session?.workspaceId !== target.workspace_id) return false;
  const excel = useExcelStore.getState();
  if (excel.compareMode) excel.closeCompare();
  openWorkspaceFile(target.file_path, {
    sessionId, workspaceId: target.workspace_id, sheet: target.sheet,
    intent: excel.fullViewPath ? "full" : "preview",
  });
  useExcelStore.getState().setPanelTab("sheet");
  if (target.ranges.length) {
    useWorkbookFocusStore.getState().focus(fileRefFromSession(target.file_path, session), target.sheet,
      target.ranges.join(","), target.content_version, { stage, persistent: true, select: false });
  } else {
    useWorkbookFocusStore.getState().clear();
  }
  return true;
}

export function openWorkbookQuestion(questionId: string, target: WorkbookTarget, sessionId: string): boolean {
  if (!openWorkbookTarget(target, sessionId, "selection")) return false;
  useWorkbookInteractionStore.getState().begin({ questionId, target, sessionId });
  useExcelStore.getState().enterSelectionMode();
  return true;
}

export function showWorkbookPresentation(presentation: WorkbookPresentation, sessionId: string): boolean {
  const sessions = useSessionStore.getState();
  if (sessions.activeSessionId !== sessionId || sessions.sessions.find((s) => s.id === sessionId)?.workspaceId !== presentation.target.workspace_id) return false;
  // A pending selection owns the surface until the user answers or leaves it.
  const request = useWorkbookInteractionStore.getState().request;
  if (request) {
    if (request.sessionId === sessionId && useChatStore.getState().pendingQuestion?.selection) return false;
    useWorkbookInteractionStore.getState().finish(request.questionId);
    useExcelStore.getState().exitSelectionMode();
  }
  if (!openWorkbookTarget(presentation.target, sessionId, presentation.stage)) return false;
  useWorkbookInteractionStore.getState().present(presentation, sessionId);
  return true;
}

export function selectionFromDraft(target: WorkbookTarget, draft: {
  path?: string; sheet: string; range: string; contentVersion?: string;
} | null): WorkbookTarget | undefined {
  if (!draft?.path || !draft.contentVersion || draft.sheet !== target.sheet
    || normalizeRelativePath(draft.path) !== normalizeRelativePath(target.file_path)) return;
  const ranges = workbookFocusRanges(draft.range);
  if (!ranges.length || ranges.length > 16) return;
  return { ...target, ranges, content_version: draft.contentVersion };
}
