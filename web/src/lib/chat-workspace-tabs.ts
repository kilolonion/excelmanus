import { resolveWorkbookPanelPath } from "@/components/excel/WorkbookPanelButton";
import { isSpreadsheetFile } from "@/lib/file-kind";

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
  if (input.fullViewPath) {
    return {
      path: input.fullViewPath,
      sheet: input.fullViewSheet ?? undefined,
    };
  }
  const path = resolveWorkbookPanelPath(
    input.activeFilePath,
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
    sheet: targetPath === input.activeFilePath ? input.activeSheet ?? undefined : undefined,
  };
}
