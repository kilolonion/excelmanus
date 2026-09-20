"use client";

import { MessageSquareText, TableProperties } from "lucide-react";
import { useShallow } from "zustand/react/shallow";
import { Button } from "@/components/ui/button";
import { useIsMobile } from "@/hooks/use-mobile";
import { prefetchExcelView } from "@/lib/excel-view-prefetch";
import { recentFilesForWorkspace } from "@/lib/workspace-file-ref";
import { useExcelStore } from "@/stores/excel-store";
import { useWordStore } from "@/stores/word-store";

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

export function WorkbookPanelButton() {
  const isMobile = useIsMobile();
  const {
    panelOpen,
    panelTab,
    activeFilePath,
    activeSheet,
    recentFiles,
    activeWorkspaceKey,
    fullViewPath,
    compareMode,
    openPanel,
    openFullView,
    setPanelTab,
    closePanel,
    closeFullView,
    closeCompare,
  } = useExcelStore(
    useShallow((s) => ({
      panelOpen: s.panelOpen,
      panelTab: s.panelTab,
      activeFilePath: s.activeFilePath,
      activeSheet: s.activeSheet,
      recentFiles: s.recentFiles,
      activeWorkspaceKey: s.activeWorkspaceKey,
      fullViewPath: s.fullViewPath,
      compareMode: s.compareMode,
      openPanel: s.openPanel,
      openFullView: s.openFullView,
      setPanelTab: s.setPanelTab,
      closePanel: s.closePanel,
      closeFullView: s.closeFullView,
      closeCompare: s.closeCompare,
    })),
  );

  const targetPath = resolveWorkbookPanelPath(activeFilePath, recentFiles, activeWorkspaceKey);
  const mobileSheetActive = Boolean(fullViewPath || compareMode);

  const handleClick = () => {
    if (isMobile) {
      closePanel();
      if (mobileSheetActive) {
        if (compareMode) closeCompare();
        if (fullViewPath) closeFullView();
        return;
      }
      if (!targetPath) return;
      closeWordSurfaces();
      openFullView(
        targetPath,
        targetPath === activeFilePath ? activeSheet ?? undefined : undefined,
      );
      return;
    }

    if (panelOpen && panelTab === "sheet") {
      closePanel();
      return;
    }
    if (panelOpen && panelTab === "history") {
      setPanelTab("sheet");
      return;
    }
    closeWordSurfaces();
    if (fullViewPath) closeFullView();
    openPanel(targetPath);
  };

  const buttonActive = isMobile ? mobileSheetActive : panelOpen && panelTab === "sheet";
  const buttonLabel = isMobile
    ? mobileSheetActive
      ? "切换到对话"
      : targetPath
        ? "切换到表格"
        : "先打开一个工作簿"
    : "工作表";

  return (
    <Button
      variant="ghost"
      size="icon"
      className={`h-8 w-8 rounded-full p-0 transition-colors ${buttonActive ? "bg-[var(--em-primary-alpha-12)] text-[var(--em-primary)]" : "text-muted-foreground"}`}
      title={buttonLabel}
      aria-label={buttonLabel}
      aria-pressed={buttonActive}
      disabled={isMobile && !mobileSheetActive && !targetPath}
      data-coach-id="coach-workbook-entry"
      onMouseEnter={() => prefetchExcelView(targetPath)}
      onFocus={() => prefetchExcelView(targetPath)}
      onClick={handleClick}
    >
      {isMobile && mobileSheetActive ? (
        <MessageSquareText className="h-[17px] w-[17px]" />
      ) : (
        <TableProperties className="h-[18px] w-[18px]" />
      )}
    </Button>
  );
}
