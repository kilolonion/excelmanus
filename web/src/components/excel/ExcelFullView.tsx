"use client";

import { useCallback, useMemo, useRef, useState } from "react";
import dynamic from "next/dynamic";
import { Check, XCircle } from "lucide-react";
import { useShallow } from "zustand/react/shallow";
import { FileHistoryWorkspace } from "@/components/history/FileHistoryWorkspace";
import { ExcelRibbonChrome } from "@/components/excel/ExcelRibbonChrome";
import { HistoryPaneOverlay } from "@/components/excel/HistoryPaneOverlay";
import { useIsMobile } from "@/hooks/use-mobile";
import { useExcelStore } from "@/stores/excel-store";
import { useSessionStore } from "@/stores/session-store";
import { buildExcelFileUrl, downloadFile, invalidateWorkbookCaches } from "@/lib/api";
import { fileBaseName } from "@/lib/revision-display";
import { useExcelCellEdit } from "@/hooks/use-excel-cell-edit";
import { ExcelWriteConflictBar } from "@/components/excel/ExcelWriteConflictBar";
import { rememberFullViewTarget } from "@/lib/workspace-surface";
import { fileRefFromSession, workspaceKeyFromSession } from "@/lib/workspace-file-ref";

const UniverSheet = dynamic(
  () => import("./UniverSheet").then((m) => ({ default: m.UniverSheet })),
  {
    ssr: false,
    loading: () => (
      <div className="flex items-center justify-center h-full text-sm text-muted-foreground">
        加载 Excel 引擎...
      </div>
    ),
  }
);

function formatSelectionConfirmLabel(
  fileName: string,
  sheet: string,
  range: string,
  cellValue?: string,
): string {
  const colon = range.indexOf(":");
  const start = colon === -1 ? range : range.slice(0, colon);
  const end = colon === -1 ? range : range.slice(colon + 1);
  const isSingle = start === end;
  const addr = isSingle ? start : range;
  const label = `引用 ${fileName} · ${sheet}!${addr}`;
  if (!isSingle || !cellValue) return label;
  const shown = cellValue.length > 40 ? `${cellValue.slice(0, 40)}…` : cellValue;
  return `${label}（值：${shown}）`;
}

export function ExcelFullView() {
  const isMobile = useIsMobile();
  const {
    fullViewPath,
    fullViewSheet,
    closeFullView,
    openPanel,
    selectionMode,
    enterSelectionMode,
    exitSelectionMode,
    confirmSelection,
    draftRange,
    setDraftRange,
    diffs,
    panelTab,
    historySubview,
    setPanelTab,
    setHistorySubview,
    operations,
  } = useExcelStore(
    useShallow((s) => ({
      fullViewPath: s.fullViewPath,
      fullViewSheet: s.fullViewSheet,
      closeFullView: s.closeFullView,
      openPanel: s.openPanel,
      selectionMode: s.selectionMode,
      enterSelectionMode: s.enterSelectionMode,
      exitSelectionMode: s.exitSelectionMode,
      confirmSelection: s.confirmSelection,
      draftRange: s.draftRange,
      setDraftRange: s.setDraftRange,
      diffs: s.diffs,
      panelTab: s.panelTab,
      historySubview: s.historySubview,
      setPanelTab: s.setPanelTab,
      setHistorySubview: s.setHistorySubview,
      operations: s.operations,
    })),
  );
  const activeSessionId = useSessionStore((s) => s.activeSessionId);
  const session = useSessionStore((s) => s.sessions.find((item) => item.id === s.activeSessionId));
  const viewGeneration = useExcelStore((s) => s.viewGeneration);
  const workspaceKey = workspaceKeyFromSession(session);
  const lastTargetRef = useRef<{ path: string; sheet?: string; workspaceKey?: string } | null>(null);
  const target = rememberFullViewTarget(
    { path: fullViewPath, sheet: fullViewSheet, workspaceKey },
    lastTargetRef.current,
  );
  if (target) lastTargetRef.current = target;
  const displayPath = target?.path ?? null;
  const displaySheet = target?.sheet;
  const {
    handleCellEdit,
    conflict: writeConflict,
    writeError,
    reloadAfterConflict,
  } = useExcelCellEdit(displayPath);

  const [withStyles, setWithStyles] = useState(true);
  const [draftCellValue, setDraftCellValue] = useState<string | undefined>(undefined);

  const handleRangeSelected = useCallback((range: string, sheet: string, cellValue?: string) => {
    const path = displayPath || undefined;
    const contentVersion = path
      ? useExcelStore.getState().getContentVersion(path) ?? undefined
      : undefined;
    setDraftRange({ range, sheet, path, contentVersion });
    setDraftCellValue(cellValue);
  }, [setDraftRange, displayPath]);

  const handleConfirmRange = useCallback(() => {
    if (draftRange && displayPath) {
      confirmSelection({
        filePath: displayPath,
        sheet: draftRange.sheet,
        range: draftRange.range,
      });
    }
    setDraftCellValue(undefined);
  }, [draftRange, displayPath, confirmSelection]);

  const handleCancelRange = useCallback(() => {
    setDraftCellValue(undefined);
    exitSelectionMode();
  }, [exitSelectionMode]);

  const toggleSelectionMode = useCallback(() => {
    if (selectionMode) {
      handleCancelRange();
    } else {
      enterSelectionMode();
    }
  }, [selectionMode, enterSelectionMode, handleCancelRange]);

  const handleRefresh = useCallback(() => {
    if (displayPath) {
      invalidateWorkbookCaches({ workspaceKey, relative: displayPath });
    }
    useExcelStore.setState((s) => ({ refreshCounter: s.refreshCounter + 1 }));
  }, [displayPath, workspaceKey]);

  const handleSwitchToPanel = useCallback(() => {
    if (!fullViewPath) return;
    openPanel(fullViewPath, fullViewSheet ?? undefined);
    closeFullView();
  }, [fullViewPath, fullViewSheet, openPanel, closeFullView]);

  const fileUrl = useMemo(
    () => (displayPath ? buildExcelFileUrl(displayPath, activeSessionId ?? undefined) : ""),
    [displayPath, activeSessionId]
  );

  const fileName = fileBaseName(displayPath) || "未知文件";
  const fileDiffs = useMemo(
    () => diffs.filter((d) => d.filePath === displayPath).slice(-20),
    [diffs, displayPath]
  );

  if (!displayPath) return null;

  const confirmLabel = draftRange
    ? formatSelectionConfirmLabel(fileName, draftRange.sheet, draftRange.range, draftCellValue)
    : "";

  return (
    <div className="flex flex-col h-full">
      <div className="relative flex-1 min-h-0 overflow-hidden">
        <UniverSheet
          fileUrl={fileUrl}
          fileRef={fileRefFromSession(displayPath, session)}
          sessionId={activeSessionId}
          viewGeneration={viewGeneration}
          initialSheet={displaySheet}
          selectionMode={selectionMode}
          onRangeSelected={handleRangeSelected}
          withStyles={withStyles}
          onCellEdit={handleCellEdit}
          historyActive={panelTab === "history"}
          onNativeRibbonTab={() => setPanelTab("sheet")}
          ribbonSlot={
            <ExcelRibbonChrome
              historyActive={panelTab === "history"}
              selectionMode={selectionMode}
              withStyles={withStyles}
              isMobile={isMobile}
              onHistory={() => setPanelTab("history")}
              onToggleSelection={toggleSelectionMode}
              onCancelSelection={handleCancelRange}
              onToggleStyles={() => setWithStyles((v) => !v)}
              onRefresh={handleRefresh}
              onDownload={() => downloadFile(displayPath, fileName, activeSessionId ?? undefined).catch(() => {})}
              onExpand={handleSwitchToPanel}
              expandTitle="切换到侧边面板"
              onClose={closeFullView}
            />
          }
        />
        {panelTab === "history" && (
          <HistoryPaneOverlay>
            <FileHistoryWorkspace
              filePath={displayPath}
              active={panelTab === "history"}
              view={historySubview}
              onViewChange={setHistorySubview}
              operationCount={operations.length}
              cellDiffs={fileDiffs}
            />
          </HistoryPaneOverlay>
        )}
      </div>

      {(writeConflict || writeError) && (
        <ExcelWriteConflictBar
          onReload={reloadAfterConflict}
          error={writeConflict ? null : writeError}
        />
      )}

      {selectionMode && draftRange && (
        <div className="border-t border-border bg-muted/40 px-3 py-2 flex items-center gap-2 shrink-0">
          <span
            className="text-xs flex-1 min-w-0 truncate"
            style={{ color: "var(--em-primary)" }}
            title={confirmLabel}
          >
            {confirmLabel}
          </span>
          <button
            onClick={handleConfirmRange}
            className="flex items-center gap-1 px-3 py-1.5 sm:px-2 sm:py-1 rounded text-xs font-medium text-white transition-colors"
            style={{ backgroundColor: "var(--em-primary)" }}
          >
            <Check className="h-3 w-3" />
            确认
          </button>
          <button
            onClick={handleCancelRange}
            className="flex items-center gap-1 px-3 py-1.5 sm:px-2 sm:py-1 rounded text-xs font-medium text-muted-foreground hover:text-foreground hover:bg-muted transition-colors"
          >
            <XCircle className="h-3 w-3" />
            取消
          </button>
        </div>
      )}
    </div>
  );
}
