"use client";

import { useCallback, useMemo, useState } from "react";
import dynamic from "next/dynamic";
import { X, RefreshCw, Clock, Maximize2, MousePointerSquareDashed, Check, XCircle, Upload, Loader2 } from "lucide-react";
import { useExcelStore } from "@/stores/excel-store";
import { useSessionStore } from "@/stores/session-store";
import { buildExcelFileUrl } from "@/lib/api";

const UniverSheet = dynamic(
  () => import("./UniverSheet").then((m) => ({ default: m.UniverSheet })),
  { ssr: false, loading: () => <div className="flex items-center justify-center h-full text-sm text-muted-foreground">加载 Excel 引擎...</div> }
);

export function ExcelSidePanel() {
  const panelOpen = useExcelStore((s) => s.panelOpen);
  const activeFilePath = useExcelStore((s) => s.activeFilePath);
  const activeSheet = useExcelStore((s) => s.activeSheet);
  const diffs = useExcelStore((s) => s.diffs);
  const closePanel = useExcelStore((s) => s.closePanel);
  const openFullView = useExcelStore((s) => s.openFullView);
  const refreshCounter = useExcelStore((s) => s.refreshCounter);
  const selectionMode = useExcelStore((s) => s.selectionMode);
  const enterSelectionMode = useExcelStore((s) => s.enterSelectionMode);
  const exitSelectionMode = useExcelStore((s) => s.exitSelectionMode);
  const confirmSelection = useExcelStore((s) => s.confirmSelection);

  const activeSessionId = useSessionStore((s) => s.activeSessionId);
  const pendingBackups = useExcelStore((s) => s.pendingBackups);
  const applyFile = useExcelStore((s) => s.applyFile);

  const hasBackupForFile = useMemo(
    () => pendingBackups.some((b) => b.original_path === activeFilePath),
    [pendingBackups, activeFilePath]
  );

  const [applyingSidePanel, setApplyingSidePanel] = useState(false);
  const [appliedSidePanel, setAppliedSidePanel] = useState(false);

  const handleApplyCurrentFile = useCallback(async () => {
    if (!activeSessionId || !activeFilePath) return;
    setApplyingSidePanel(true);
    const ok = await applyFile(activeSessionId, activeFilePath);
    setApplyingSidePanel(false);
    if (ok) setAppliedSidePanel(true);
  }, [activeSessionId, activeFilePath, applyFile]);

  // Pending range from Univer selection (not yet confirmed)
  const [pendingRange, setPendingRange] = useState<{ range: string; sheet: string } | null>(null);

  const handleRangeSelected = useCallback((range: string, sheet: string) => {
    setPendingRange({ range, sheet });
  }, []);

  const handleConfirmRange = useCallback(() => {
    if (pendingRange && activeFilePath) {
      confirmSelection({
        filePath: activeFilePath,
        sheet: pendingRange.sheet,
        range: pendingRange.range,
      });
      setPendingRange(null);
    }
  }, [pendingRange, activeFilePath, confirmSelection]);

  const handleCancelRange = useCallback(() => {
    exitSelectionMode();
    setPendingRange(null);
  }, [exitSelectionMode]);

  const toggleSelectionMode = useCallback(() => {
    if (selectionMode) {
      handleCancelRange();
    } else {
      enterSelectionMode();
      setPendingRange(null);
    }
  }, [selectionMode, enterSelectionMode, handleCancelRange]);

  const fileUrl = useMemo(
    () => (activeFilePath ? buildExcelFileUrl(activeFilePath) : ""),
    [activeFilePath]
  );

  const fileName = activeFilePath?.split("/").pop() || "未知文件";

  const fileDiffs = useMemo(
    () => diffs.filter((d) => d.filePath === activeFilePath).slice(-20),
    [diffs, activeFilePath]
  );

  const handleRefresh = useCallback(() => {
    // Force refresh by incrementing counter
    useExcelStore.setState((s) => ({ refreshCounter: s.refreshCounter + 1 }));
  }, []);

  if (!panelOpen || !activeFilePath) return null;

  return (
    <div className="flex flex-col h-full border-l border-border bg-background" style={{ width: 520 }}>
      {/* Header */}
      <div className="flex items-center justify-between px-3 py-2 border-b border-border bg-muted/30">
        <div className="flex items-center gap-2 min-w-0">
          <span className="text-sm font-medium truncate">{fileName}</span>
          {activeSheet && (
            <span className="text-xs text-muted-foreground">/ {activeSheet}</span>
          )}
        </div>
        <div className="flex items-center gap-1">
          <button
            onClick={toggleSelectionMode}
            className={`p-1.5 rounded transition-colors ${
              selectionMode
                ? "bg-[var(--em-primary)]/20 text-[var(--em-primary)]"
                : "hover:bg-muted text-muted-foreground hover:text-foreground"
            }`}
            title={selectionMode ? "退出选区模式" : "选区引用"}
          >
            <MousePointerSquareDashed className="h-3.5 w-3.5" />
          </button>
          <button
            onClick={handleRefresh}
            className="p-1.5 rounded hover:bg-muted transition-colors text-muted-foreground hover:text-foreground"
            title="刷新"
          >
            <RefreshCw className="h-3.5 w-3.5" />
          </button>
          <button
            onClick={() => openFullView(activeFilePath!, activeSheet ?? undefined)}
            className="p-1.5 rounded hover:bg-muted transition-colors text-muted-foreground hover:text-foreground"
            title="展开到聊天区域"
          >
            <Maximize2 className="h-3.5 w-3.5" />
          </button>
          <button
            onClick={closePanel}
            className="p-1.5 rounded hover:bg-muted transition-colors text-muted-foreground hover:text-foreground"
            title="关闭"
          >
            <X className="h-3.5 w-3.5" />
          </button>
        </div>
      </div>

      {/* Univer Sheet */}
      <div className="flex-1 overflow-hidden">
        <UniverSheet
          fileUrl={fileUrl}
          initialSheet={activeSheet || undefined}
          selectionMode={selectionMode}
          onRangeSelected={handleRangeSelected}
        />
      </div>

      {/* Selection confirmation bar */}
      {selectionMode && pendingRange && (
        <div className="border-t border-border bg-muted/40 px-3 py-2 flex items-center gap-2">
          <span className="text-xs font-mono flex-1 truncate" style={{ color: "var(--em-primary)" }}>
            {pendingRange.sheet}!{pendingRange.range}
          </span>
          <button
            onClick={handleConfirmRange}
            className="flex items-center gap-1 px-2 py-1 rounded text-xs font-medium text-white transition-colors"
            style={{ backgroundColor: "var(--em-primary)" }}
          >
            <Check className="h-3 w-3" />
            确认
          </button>
          <button
            onClick={handleCancelRange}
            className="flex items-center gap-1 px-2 py-1 rounded text-xs font-medium text-muted-foreground hover:text-foreground hover:bg-muted transition-colors"
          >
            <XCircle className="h-3 w-3" />
            取消
          </button>
        </div>
      )}

      {/* Apply to original file bar */}
      {(hasBackupForFile || appliedSidePanel) && (
        <div className="border-t border-border bg-muted/30 px-3 py-2 flex items-center justify-between">
          <span className="text-[11px] text-muted-foreground">沙盒文件</span>
          {appliedSidePanel ? (
            <span className="flex items-center gap-1 text-[11px] text-emerald-600 dark:text-emerald-400 font-medium">
              <Check className="h-3 w-3" />
              已应用到原文件
            </span>
          ) : (
            <button
              onClick={handleApplyCurrentFile}
              disabled={applyingSidePanel}
              className="flex items-center gap-1.5 px-2.5 py-1 rounded text-[11px] font-medium transition-colors text-white"
              style={{ backgroundColor: "var(--em-primary)" }}
            >
              {applyingSidePanel ? (
                <Loader2 className="h-3 w-3 animate-spin" />
              ) : (
                <>
                  <Upload className="h-3 w-3" />
                  应用到原文件
                </>
              )}
            </button>
          )}
        </div>
      )}

      {/* Diff History (bottom bar) */}
      {fileDiffs.length > 0 && (
        <div className="border-t border-border bg-muted/20 max-h-[120px] overflow-y-auto">
          <div className="px-3 py-1.5 text-[10px] text-muted-foreground font-medium flex items-center gap-1">
            <Clock className="h-3 w-3" />
            变更历史
          </div>
          {fileDiffs.map((d, i) => {
            const time = new Date(d.timestamp).toLocaleTimeString("zh-CN", {
              hour: "2-digit",
              minute: "2-digit",
              second: "2-digit",
            });
            return (
              <div
                key={i}
                className="px-3 py-0.5 text-[10px] text-muted-foreground hover:bg-muted/30 cursor-default"
              >
                <span className="text-foreground/70">{time}</span>{" "}
                <span>{d.affectedRange}</span>{" "}
                <span>({d.changes.length} cells)</span>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
