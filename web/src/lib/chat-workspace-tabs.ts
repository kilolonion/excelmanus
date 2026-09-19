import { resolveWorkbookPanelPath } from "@/components/excel/WorkbookPanelButton";

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
  if (!path) return null;
  return {
    path,
    sheet: path === input.activeFilePath ? input.activeSheet ?? undefined : undefined,
  };
}
