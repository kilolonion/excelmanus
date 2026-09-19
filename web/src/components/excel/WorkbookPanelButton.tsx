"use client";

import { Table2 } from "lucide-react";
import { useShallow } from "zustand/react/shallow";
import { Button } from "@/components/ui/button";
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
  const {
    panelOpen,
    panelTab,
    activeFilePath,
    recentFiles,
    activeWorkspaceKey,
    fullViewPath,
    openPanel,
    setPanelTab,
    closePanel,
    closeFullView,
  } = useExcelStore(
    useShallow((s) => ({
      panelOpen: s.panelOpen,
      panelTab: s.panelTab,
      activeFilePath: s.activeFilePath,
      recentFiles: s.recentFiles,
      activeWorkspaceKey: s.activeWorkspaceKey,
      fullViewPath: s.fullViewPath,
      openPanel: s.openPanel,
      setPanelTab: s.setPanelTab,
      closePanel: s.closePanel,
      closeFullView: s.closeFullView,
    })),
  );

  const handleClick = () => {
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
    openPanel(resolveWorkbookPanelPath(activeFilePath, recentFiles, activeWorkspaceKey));
  };

  const warmTarget = resolveWorkbookPanelPath(activeFilePath, recentFiles, activeWorkspaceKey);

  return (
    <Button
      variant="ghost"
      size="icon"
      className={`h-8 w-8 rounded-full p-0 ${panelOpen && panelTab === "sheet" ? "bg-[var(--em-primary-alpha-12)] text-[var(--em-primary)]" : "text-muted-foreground"}`}
      title="工作表"
      aria-label="工作表"
      aria-pressed={panelOpen && panelTab === "sheet"}
      data-coach-id="coach-workbook-entry"
      onMouseEnter={() => prefetchExcelView(warmTarget)}
      onFocus={() => prefetchExcelView(warmTarget)}
      onClick={handleClick}
    >
      <Table2 className="h-3.5 w-3.5" />
    </Button>
  );
}
