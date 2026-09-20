"use client";

import { MessageSquareText, TableProperties } from "lucide-react";
import { useShallow } from "zustand/react/shallow";
import { Button } from "@/components/ui/button";
import { useIsMobile } from "@/hooks/use-mobile";
import { prefetchExcelView } from "@/lib/excel-view-prefetch";
import { normalizeRelativePath, recentFilesForWorkspace, workspaceKeyFromSession } from "@/lib/workspace-file-ref";
import { NO_WORKBOOK_HINT } from "@/lib/no-workbook-hint";
import { useHintTooltip } from "@/hooks/use-hint-tooltip";
import { useExcelStore } from "@/stores/excel-store";
import { useWordStore } from "@/stores/word-store";
import { useWorkbookConversationStore } from "@/stores/workbook-conversation-store";
import { useSessionStore } from "@/stores/session-store";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";

export function resolveWorkbookPanelPath(
  activeFilePath: string | null,
  recentFiles: { path: string; workspaceKey?: string }[],
  workspaceKey?: string | null,
): string | undefined {
  const scoped = recentFilesForWorkspace(recentFiles, workspaceKey);
  return activeFilePath || scoped[0]?.path || undefined;
}

function closeWordSurfaces() {
  const word = useWordStore.getState();
  word.closePanel();
  word.closeFullView();
}

/** The original side-panel entry owns the side-by-side discussion layout. */
export function toggleWorkbookPanelView(isMobile: boolean) {
  const excel = useExcelStore.getState();
  const sessions = useSessionStore.getState();
  const session = sessions.sessions.find((item) => item.id === sessions.activeSessionId);
  if (isMobile && (excel.fullViewPath || excel.compareMode)) {
    excel.closePanel();
    excel.closeCompare();
    excel.closeFullView();
    return;
  }
  if (!isMobile && excel.fullViewPath && excel.fullViewLayout === "split") {
    if (excel.panelTab === "history") excel.setPanelTab("sheet");
    else excel.closeFullView();
    return;
  }
  const path = excel.activeWorkspaceKey === workspaceKeyFromSession(session)
    ? excel.fullViewPath || resolveWorkbookPanelPath(excel.activeFilePath, excel.recentFiles, excel.activeWorkspaceKey)
    : undefined;
  if (!path) {
    useWorkbookConversationStore.getState().openPicker(isMobile ? "embedded" : "split");
    return;
  }
  const target = session ? useWorkbookConversationStore.getState().targets[session.id] : undefined;
  const sheet = target?.file.workspaceKey === excel.activeWorkspaceKey && target.file.relative === normalizeRelativePath(path)
    ? target.sheet : path === excel.fullViewPath ? excel.fullViewSheet : excel.activeSheet;
  closeWordSurfaces();
  excel.closeCompare();
  excel.openFullView(path, sheet ?? undefined, isMobile ? "embedded" : "split");
}

export function WorkbookPanelButton() {
  const isMobile = useIsMobile();
  const session = useSessionStore((s) => s.sessions.find((item) => item.id === s.activeSessionId));
  const hint = useHintTooltip();
  const {
    panelOpen,
    panelTab,
    activeFilePath,
    recentFiles,
    activeWorkspaceKey,
    fullViewPath,
    fullViewLayout,
    compareMode,
  } = useExcelStore(
    useShallow((s) => ({
      panelOpen: s.panelOpen,
      panelTab: s.panelTab,
      activeFilePath: s.activeFilePath,
      recentFiles: s.recentFiles,
      activeWorkspaceKey: s.activeWorkspaceKey,
      fullViewPath: s.fullViewPath,
      fullViewLayout: s.fullViewLayout,
      compareMode: s.compareMode,
    })),
  );

  const targetPath = activeWorkspaceKey === workspaceKeyFromSession(session)
    ? resolveWorkbookPanelPath(activeFilePath, recentFiles, activeWorkspaceKey) : undefined;
  const mobileSheetActive = Boolean(fullViewPath || compareMode);

  const buttonActive = isMobile ? mobileSheetActive
    : panelTab === "sheet" && (panelOpen || Boolean(fullViewPath && fullViewLayout === "split"));
  const buttonLabel = isMobile
    ? mobileSheetActive
      ? "切换到对话"
      : targetPath
        ? "切换到表格"
        : "打开表格"
    : "工作表";
  const sheetUnavailable = isMobile && !mobileSheetActive && !targetPath;

  const button = (
    <Button
      variant="ghost"
      size="icon"
      className={`h-8 w-8 rounded-full p-0 transition-colors ${buttonActive ? "bg-[var(--em-primary-alpha-12)] text-[var(--em-primary)]" : "text-muted-foreground"}`}
      title={buttonLabel}
      aria-label={buttonLabel}
      aria-pressed={buttonActive}
      data-coach-id="coach-workbook-entry"
      onMouseEnter={() => prefetchExcelView(targetPath)}
      onFocus={() => prefetchExcelView(targetPath)}
      onClick={() => toggleWorkbookPanelView(isMobile)}
    >
      {isMobile && mobileSheetActive ? (
        <MessageSquareText className="h-[17px] w-[17px]" />
      ) : (
        <TableProperties className="h-[18px] w-[18px]" />
      )}
    </Button>
  );

  if (!sheetUnavailable) return button;

  return (
    <TooltipProvider delayDuration={400}>
      <Tooltip open={hint.open} onOpenChange={hint.onOpenChange}>
        <TooltipTrigger asChild>{button}</TooltipTrigger>
        <TooltipContent side="bottom" sideOffset={6} className="text-xs">
          {NO_WORKBOOK_HINT}
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}
