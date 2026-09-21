import { resolveWorkbookPanelPath } from "@/components/excel/WorkbookPanelButton";
import { isSpreadsheetFile } from "@/lib/file-kind";
import { openWorkspaceFile } from "@/lib/open-workspace-file";
import { useExcelStore } from "@/stores/excel-store";
import { useWordStore } from "@/stores/word-store";
import { useWorkbookConversationStore } from "@/stores/workbook-conversation-store";
import { normalizeRelativePath, workspaceKeyForSessionId } from "@/lib/workspace-file-ref";
import { currentWorkbookTarget } from "@/lib/workbook-conversation";
import { useSessionStore } from "@/stores/session-store";

export type ChatWorkspaceTab = "chat" | "sheet";

export function resolveChatWorkspaceTab(input: {
  fullViewPath: string | null;
  compareMode: boolean;
}): ChatWorkspaceTab {
  if (input.fullViewPath || input.compareMode) return "sheet";
  return "chat";
}

export function resolveSheetFullViewTarget(input: {
  activeFilePath: string | null;
  activeSheet: string | null;
  recentFiles: { path: string; workspaceKey?: string }[];
  workspaceFiles?: { path: string; filename: string; is_dir?: boolean }[];
  workspaceKey?: string | null;
  fullViewPath: string | null;
  fullViewSheet: string | null;
}): { path: string; sheet?: string } | null {
  if (input.fullViewPath && isSpreadsheetFile(input.fullViewPath)) {
    return {
      path: input.fullViewPath,
      sheet: input.fullViewSheet ?? undefined,
    };
  }
  const activeFilePath = input.activeFilePath && isSpreadsheetFile(input.activeFilePath)
    ? input.activeFilePath
    : null;
  const path = resolveWorkbookPanelPath(
    activeFilePath,
    input.recentFiles,
    input.workspaceKey,
  );
  const workspacePath = input.workspaceFiles?.find(
    (file) => !file.is_dir && isSpreadsheetFile(file.filename || file.path),
  )?.path;
  const targetPath = path || workspacePath;
  if (!targetPath) return null;
  return {
    path: targetPath,
    sheet: targetPath === activeFilePath ? input.activeSheet ?? undefined : undefined,
  };
}

/** 空表格视图也可进入文件选择，不以 Agent 生成工作簿为前置条件。 */
export function activateChatWorkspaceTab(key: ChatWorkspaceTab): void {
  const excel = useExcelStore.getState();
  if (key === "chat") {
    if (excel.compareMode) excel.closeCompare();
    if (excel.fullViewPath) excel.closeFullView();
    if (useWordStore.getState().fullViewPath) {
      useWordStore.getState().closeFullView();
    }
    return;
  }
  if (excel.activeWorkspaceKey !== workspaceKeyForSessionId(useSessionStore.getState().activeSessionId)) {
    useWorkbookConversationStore.getState().openPicker();
    return;
  }
  const target = resolveSheetFullViewTarget({
    activeFilePath: excel.activeFilePath,
    activeSheet: excel.activeSheet,
    recentFiles: excel.recentFiles,
    workspaceFiles: excel.workspaceFiles,
    workspaceKey: excel.activeWorkspaceKey,
    fullViewPath: excel.fullViewPath,
    fullViewSheet: excel.fullViewSheet,
  });
  if (!target) { useWorkbookConversationStore.getState().openPicker(); return; }
  if (excel.compareMode) excel.closeCompare();
  const discussion = currentWorkbookTarget(useSessionStore.getState().activeSessionId);
  const sheet = discussion?.file.relative === normalizeRelativePath(target.path) ? discussion.sheet ?? target.sheet : target.sheet;
  openWorkspaceFile(target.path, { intent: "full", sheet, workbookLayout: "embedded" });
}
