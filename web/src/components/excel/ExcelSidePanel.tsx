"use client";

import { useCallback, useMemo, useState, useRef } from "react";
import dynamic from "next/dynamic";
import { motion, AnimatePresence } from "framer-motion";
import { X, RefreshCw, Clock, Maximize2, MousePointerSquareDashed, Check, XCircle, Upload, Loader2, Download, Paintbrush } from "lucide-react";
import { panelSlideVariants, panelSlideVariantsMobile } from "@/lib/sidebar-motion";
import { useShallow } from "zustand/react/shallow";
import { useIsMobile } from "@/hooks/use-mobile";
import { useExcelStore } from "@/stores/excel-store";
import { useSessionStore } from "@/stores/session-store";
import { buildExcelFileUrl, downloadFile, normalizeExcelPath } from "@/lib/api";

const UniverSheet = dynamic(
  () => import("./UniverSheet").then((m) => ({ default: m.UniverSheet })),
  { ssr: false, loading: () => <div className="flex items-center justify-center h-full text-sm text-muted-foreground">加载 Excel 引擎...</div> }
);

export function ExcelSidePanel() {
  const isMobile = useIsMobile();
  const {
    panelOpen, activeFilePath, activeSheet, diffs, closePanel,
    openFullView, selectionMode, enterSelectionMode,
    exitSelectionMode, confirmSelection, pendingBackups, applyFile,
  } = useExcelStore(useShallow((s) => ({
    panelOpen: s.panelOpen,
    activeFilePath: s.activeFilePath,
    activeSheet: s.activeSheet,
    diffs: s.diffs,
    closePanel: s.closePanel,
    openFullView: s.openFullView,
    selectionMode: s.selectionMode,
    enterSelectionMode: s.enterSelectionMode,
    exitSelectionMode: s.exitSelectionMode,
    confirmSelection: s.confirmSelection,
    pendingBackups: s.pendingBackups,
    applyFile: s.applyFile,
  })));

  const activeSessionId = useSessionStore((s) => s.activeSessionId);

  const hasBackupForFile = useMemo(
    () => {
      if (!activeFilePath) return false;
      const norm = normalizeExcelPath(activeFilePath);
      return pendingBackups.some((b) => normalizeExcelPath(b.original_path) === norm);
    },
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
  const [withStyles, setWithStyles] = useState(true);

  // Swipe-down to close on mobile
  const touchRef = useRef<{ startY: number; startTime: number } | null>(null);
  const handlePanelTouchStart = useCallback((e: React.TouchEvent) => {
    if (!isMobile) return;
    touchRef.current = { startY: e.touches[0].clientY, startTime: Date.now() };
  }, [isMobile]);
  const handlePanelTouchEnd = useCallback((e: React.TouchEvent) => {
    if (!isMobile || !touchRef.current) return;
    const dy = e.changedTouches[0].clientY - touchRef.current.startY;
    const dt = Date.now() - touchRef.current.startTime;
    touchRef.current = null;
    if (dy > 80 && dt < 400) {
      closePanel();
    }
  }, [isMobile, closePanel]);

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
    () => (activeFilePath ? buildExcelFileUrl(activeFilePath, activeSessionId ?? undefined) : ""),
    [activeFilePath, activeSessionId]
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

  const isOpen = panelOpen && !!activeFilePath;

  return (
    <AnimatePresence>
      {isOpen && (
        <motion.div
          key="excel-side-panel"
          variants={isMobile ? panelSlideVariantsMobile : panelSlideVariants}
          initial="initial"
          animate="animate"
          exit="exit"
          className={
            isMobile
              ? "fixed inset-0 z-50 flex flex-col bg-background"
              : "flex flex-col h-full border-l border-border bg-background"
          }
          style={isMobile ? undefined : { width: 520 }}
          onTouchStart={handlePanelTouchStart}
          onTouchEnd={handlePanelTouchEnd}
        >
      {/* Swipe indicator for mobile */}
      {isMobile && (
        <div className="flex justify-center py-1.5 flex-shrink-0">
          <div className="w-10 h-1 rounded-full bg-muted-foreground/30" />
        </div>
      )}
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
            className={`p-2 md:p-1.5 rounded transition-colors ${
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
            className="p-2 md:p-1.5 rounded hover:bg-muted transition-colors text-muted-foreground hover:text-foreground"
            title="刷新"
          >
            <RefreshCw className="h-3.5 w-3.5" />
          </button>
          <button
              onClick={() => setWithStyles((v) => !v)}
              className={`flex-shrink-0 h-6 w-6 flex items-center justify-center rounded-md transition-colors duration-150 hover:bg-accent ${
                withStyles
                  ? "text-[var(--em-primary)]"
                  : "text-muted-foreground"
              }`}
              title={withStyles ? "关闭样式渲染" : "开启样式渲染"}
            >
              <Paintbrush className="h-3.5 w-3.5" />
            </button>
            <button
            onClick={() => activeFilePath && downloadFile(activeFilePath, fileName, activeSessionId ?? undefined).catch(() => {})}
            className="p-2 md:p-1.5 rounded hover:bg-muted transition-colors text-muted-foreground hover:text-foreground"
            title="下载文件"
          >
            <Download className="h-3.5 w-3.5" />
          </button>
          <button
            onClick={() => openFullView(activeFilePath!, activeSheet ?? undefined)}
            className="p-2 md:p-1.5 rounded hover:bg-muted transition-colors text-muted-foreground hover:text-foreground"
            title="展开到聊天区域"
          >
            <Maximize2 className="h-3.5 w-3.5" />
          </button>
          <button
            onClick={closePanel}
            className="p-2 md:p-1.5 rounded hover:bg-muted transition-colors text-muted-foreground hover:text-foreground"
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
          withStyles={withStyles}
        
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
        </motion.div>
      )}
    </AnimatePresence>
  );
}
