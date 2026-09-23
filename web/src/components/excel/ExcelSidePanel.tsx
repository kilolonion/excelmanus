"use client";

import { useCallback, useEffect, useMemo, useState, useRef } from "react";
import dynamic from "next/dynamic";
import { motion } from "framer-motion";
import { X, Check, XCircle, FileSpreadsheet } from "lucide-react";
import { FileHistoryWorkspace } from "@/components/history/FileHistoryWorkspace";
import { formatSelectionConfirmLabel } from "@/lib/excel-selection";
import { fileBaseName } from "@/lib/revision-display";
import { panelSlideVariants, panelSlideVariantsMobile, panelSlideVariantsMedium } from "@/lib/sidebar-motion";
import { ExcelRibbonChrome } from "@/components/excel/ExcelRibbonChrome";
import { HistoryPaneOverlay } from "@/components/excel/HistoryPaneOverlay";
import { useShallow } from "zustand/react/shallow";
import { useIsMobile, useIsTablet, useIsDesktop, useIsMediumScreen } from "@/hooks/use-mobile";
import { useResizablePanel } from "@/hooks/use-resizable-panel";
import { useExcelStore } from "@/stores/excel-store";
import { useSessionStore } from "@/stores/session-store";
import { buildExcelFileUrl, downloadFile } from "@/lib/api";
import { useExcelCellEdit } from "@/hooks/use-excel-cell-edit";
import { fileRefFromSession, recentFilesForWorkspace, workspaceKeyFromSession } from "@/lib/workspace-file-ref";
import { ExcelWriteConflictBar } from "@/components/excel/ExcelWriteConflictBar";
import { useWorkbookConversationStore, type WorkbookViewState } from "@/stores/workbook-conversation-store";
import { WorkbookInteractionBar, useWorkbookQuestionRequest } from "./WorkbookInteractionBar";
import { useWorkbookWorkspaceStore, workbookWorkspaceKey } from "@/stores/workbook-workspace-store";
import { WorkbookLoadingState } from "./WorkbookLoadingState";

const UniverSheet = dynamic(
  () => import("./UniverSheet").then((m) => ({ default: m.UniverSheet })),
  { ssr: false, loading: () => <WorkbookLoadingState /> }
);

export function ExcelSidePanel() {
  const workbookQuestion = useWorkbookQuestionRequest();
  const isMobile = useIsMobile();
  const isTablet = useIsTablet();
  const isDesktop = useIsDesktop();
  const isMediumScreen = useIsMediumScreen();

  const {
    panelOpen, activeFilePath, activeSheet, diffs, closePanel,
    openFullView, selectionMode, enterSelectionMode,
    exitSelectionMode, confirmSelection, draftRange, setDraftRange,
    recentFiles, activeWorkspaceKey, openPanel, removeRecentFile,
    panelTab, historySubview, setPanelTab, setHistorySubview, operations,
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
    draftRange: s.draftRange,
    setDraftRange: s.setDraftRange,
    recentFiles: s.recentFiles,
    activeWorkspaceKey: s.activeWorkspaceKey,
    openPanel: s.openPanel,
    removeRecentFile: s.removeRecentFile,
    panelTab: s.panelTab,
    historySubview: s.historySubview,
    setPanelTab: s.setPanelTab,
    setHistorySubview: s.setHistorySubview,
    operations: s.operations,
  })));

  const activeSessionId = useSessionStore((s) => s.activeSessionId);
  const session = useSessionStore((s) => s.sessions.find((item) => item.id === s.activeSessionId));
  const viewGeneration = useExcelStore((s) => s.viewGeneration);
  const workspaceKey = activeWorkspaceKey ?? workspaceKeyFromSession(session);
  const reportView = useCallback((view: WorkbookViewState) => {
    if (!activeFilePath || !session || useSessionStore.getState().activeSessionId !== session.id) return;
    useWorkbookConversationStore.getState().observe(session.id, fileRefFromSession(activeFilePath, session), view);
    if (view.status === "ready" && view.sheet) useWorkbookWorkspaceStore.getState().observeSheet(workbookWorkspaceKey(session.id, workspaceKeyFromSession(session)), activeFilePath, view.sheet);
  }, [activeFilePath, session]);
  const visibleRecentFiles = recentFilesForWorkspace(recentFiles, workspaceKey);
  const {
    handleCellEdit,
    conflict: writeConflict,
    writeError,
    reloadAfterConflict,
  } = useExcelCellEdit(activeFilePath);

  // ── 桌面端拖拽调宽 ──
  const {
    panelWidth: resizableWidth,
    isFloatingByResize,
    handleProps: resizeHandleProps,
    isDragging: isResizing,
  } = useResizablePanel(isDesktop);

  // 非手机尺寸下始终作为布局列，让顶栏、对话区和输入框同步缩窄。
  // 仅手机或桌面端手动拖宽超过 50vw 时使用浮层。
  const useFloatingMode = isMobile || isFloatingByResize;
  const panelWidth = isMobile ? undefined : isDesktop ? resizableWidth : isTablet ? 420 : 600;

  // ── Tab 栏鼠标拖拽横向滚动（适配无触摸板的电脑端 + 移动端触摸） ──
  const tabBarRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = tabBarRef.current;
    if (!el) return;
    let isDragging = false;
    let startX = 0;
    let scrollLeft = 0;

    const onMouseDown = (e: MouseEvent) => {
      // 忽略关闭按钮等交互元素的点击
      if ((e.target as HTMLElement).closest("button")) return;
      isDragging = true;
      startX = e.pageX - el.offsetLeft;
      scrollLeft = el.scrollLeft;
      el.style.cursor = "grabbing";
      el.style.userSelect = "none";
    };
    const onMouseMove = (e: MouseEvent) => {
      if (!isDragging) return;
      e.preventDefault();
      const x = e.pageX - el.offsetLeft;
      el.scrollLeft = scrollLeft - (x - startX);
    };
    const onMouseUp = () => {
      if (!isDragging) return;
      isDragging = false;
      el.style.cursor = "";
      el.style.userSelect = "";
    };

    el.addEventListener("mousedown", onMouseDown);
    window.addEventListener("mousemove", onMouseMove);
    window.addEventListener("mouseup", onMouseUp);
    return () => {
      el.removeEventListener("mousedown", onMouseDown);
      window.removeEventListener("mousemove", onMouseMove);
      window.removeEventListener("mouseup", onMouseUp);
    };
  }, []);

  const [withStyles, setWithStyles] = useState(true);
  const [draftCellValue, setDraftCellValue] = useState<string | undefined>(undefined);

  // 移动端下滑关闭
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
    if (dy > 50 && dt < 400) {
      closePanel();
    }
  }, [isMobile, closePanel]);

  const handleRangeSelected = useCallback((range: string, sheet: string, cellValue?: string, contentVersion?: string) => {
    const path = activeFilePath || undefined;
    setDraftRange({ range, sheet, path, contentVersion });
    setDraftCellValue(cellValue);
  }, [setDraftRange, activeFilePath]);

  const handleConfirmRange = useCallback(() => {
    if (draftRange && activeFilePath) {
      confirmSelection({
        filePath: activeFilePath,
        sheet: draftRange.sheet,
        range: draftRange.range,
        contentVersion: draftRange.contentVersion,
      });
    }
    setDraftCellValue(undefined);
  }, [draftRange, activeFilePath, confirmSelection]);

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

  const fileUrl = useMemo(
    () => (activeFilePath ? buildExcelFileUrl(activeFilePath, activeSessionId, session?.workspaceId) : ""),
    [activeFilePath, activeSessionId, session?.workspaceId]
  );

  const fileName = fileBaseName(activeFilePath) || "工作表";
  const confirmLabel = draftRange
    ? formatSelectionConfirmLabel(fileName, draftRange.sheet, draftRange.range, draftCellValue)
    : "";

  const fileDiffs = useMemo(
    () => diffs.filter((d) => d.filePath === activeFilePath).slice(-20),
    [diffs, activeFilePath]
  );

  const handleRefresh = () => {
    if (activeFilePath) {
      useExcelStore.getState().notifyWorkbookChanged(activeFilePath, workspaceKey, undefined, "refresh");
    }
  };

  const isOpen = panelOpen;

  // 面板首次打开后保持挂载，关闭时用 CSS 隐藏，避免 Univer 实例被销毁重建
  const [hasEverMounted, setHasEverMounted] = useState(() => panelOpen);
  useEffect(() => {
    if (!isOpen || hasEverMounted) return;
    const frame = requestAnimationFrame(() => setHasEverMounted(true));
    return () => cancelAnimationFrame(frame);
  }, [isOpen, hasEverMounted]);

  // 根据屏幕尺寸选择合适的动画变体
  const getAnimationVariants = () => {
    if (isMobile) return panelSlideVariantsMobile;
    if (isMediumScreen) return panelSlideVariantsMedium;
    return panelSlideVariants;
  };

  // 面板从未打开过则不渲染任何内容
  if (!hasEverMounted && !isOpen) return null;

  return (
    <>
      {/* 拖拽超阈值浮动时的背景遮罩 */}
      {isDesktop && isFloatingByResize && isOpen && (
        <div
          className="fixed inset-0 z-39 bg-black/20 transition-opacity"
          onClick={closePanel}
        />
      )}

      <motion.div
        key="excel-side-panel"
        data-coach-id="coach-excel-panel"
        aria-hidden={!isOpen}
        variants={isResizing ? undefined : getAnimationVariants()}
        initial={isResizing ? false : "initial"}
        animate={isResizing ? undefined : isOpen ? "animate" : "exit"}
        className={
          useFloatingMode
            ? isMobile
              ? "fixed inset-0 z-50 flex flex-col bg-background"
              : isFloatingByResize
                ? "fixed inset-y-0 right-0 z-40 flex flex-col bg-background border-l border-border shadow-2xl"
                : "fixed inset-y-0 right-0 z-40 flex flex-col bg-background border-l border-border shadow-xl"
            : "relative flex flex-col h-full flex-shrink-0 border-l border-border bg-background"
        }
        style={{
          ...(useFloatingMode ? (isMobile ? {} : { width: panelWidth }) : { width: panelWidth }),
          // 拖拽中禁用 transition 以获得流畅体验
          ...(isResizing ? { transition: "none" } : {}),
          // 关闭时隐藏但保持挂载
          ...(!isOpen ? { display: "none" } : {}),
        }}
      >
        {/* ── 桌面端：左侧拖拽手柄 ── */}
        {isDesktop && isOpen && (
          <div
            {...resizeHandleProps}
            className={`absolute left-0 top-0 bottom-0 w-1 z-10 cursor-col-resize group/handle transition-colors
              ${isResizing ? "bg-[var(--em-primary)]" : "hover:bg-[var(--em-primary)]/60 bg-transparent"}`}
            title="拖拽调整宽度 · 双击恢复默认"
          >
            {/* 抓手指示器 — 居中的三条短线 */}
            <div className="absolute left-0 top-1/2 -translate-y-1/2 flex flex-col gap-1 items-center w-1 py-2 opacity-0 group-hover/handle:opacity-100 transition-opacity">
              <div className="w-0.5 h-1.5 rounded-full bg-muted-foreground/60" />
              <div className="w-0.5 h-1.5 rounded-full bg-muted-foreground/60" />
              <div className="w-0.5 h-1.5 rounded-full bg-muted-foreground/60" />
            </div>
          </div>
        )}

            {/* 移动端与中等屏幕的滑动指示条 */}
            {useFloatingMode && !isFloatingByResize && (
              <div
                className="flex justify-center py-1.5 flex-shrink-0"
                onTouchStart={handlePanelTouchStart}
                onTouchEnd={handlePanelTouchEnd}
                aria-label="下滑关闭表格面板"
              >
                <div className="w-10 h-1 rounded-full bg-muted-foreground/30" />
              </div>
            )}

          {/* 多文件 Tab 栏 */}
          {visibleRecentFiles.length > 1 && (
            <div ref={tabBarRef} className="flex items-center bg-[var(--em-panel-soft)] border-b border-[var(--em-line)] min-h-[36px] select-none overflow-x-auto scrollbar-none flex-shrink-0">
              {visibleRecentFiles.slice(0, 10).map((file) => {
                const isActive = file.path === activeFilePath;
                return (
                  <div
                    key={file.path}
                    onClick={() => openPanel(file.path)}
                    className={`group relative flex items-center gap-1.5 px-2.5 h-[32px] text-[11px] cursor-pointer shrink-0 border-r border-border/40 transition-colors ${
                      isActive
                        ? "bg-[var(--em-panel)] text-foreground"
                        : "text-muted-foreground hover:bg-muted/40 hover:text-foreground"
                    }`}
                  >
                    {isActive && (
                      <div className="absolute top-0 left-0 right-0 h-[2px]" style={{ backgroundColor: "var(--em-primary)" }} />
                    )}
                    <FileSpreadsheet className="h-3 w-3 shrink-0 text-emerald-600 dark:text-emerald-400" />
                    <span className="truncate max-w-[100px]">{file.filename}</span>
                    <button
                      onClick={async (e) => {
                        e.stopPropagation();
                        if (!await useExcelStore.getState().closeWorkbook(file.path)) return;
                        removeRecentFile(file.path);
                        if (isActive) {
                          const remaining = visibleRecentFiles.filter((f) => f.path !== file.path);
                          if (remaining.length > 0) {
                            openPanel(remaining[0].path);
                          } else {
                            closePanel();
                          }
                        }
                      }}
                      className="ml-0.5 p-0.5 rounded opacity-0 group-hover:opacity-60 hover:!opacity-100 hover:bg-muted transition-all"
                      title="关闭"
                    >
                      <X className="w-2.5 h-2.5" />
                    </button>
                  </div>
                );
              })}
            </div>
          )}

          {!activeFilePath && (
              <div className="flex items-center justify-end px-2 h-10 border-b border-[var(--em-line)] bg-[var(--em-panel-soft)] shrink-0">
              <button
                type="button"
                onClick={closePanel}
                title="关闭"
                aria-label="关闭"
                className="inline-flex items-center justify-center size-7 rounded-md text-muted-foreground hover:bg-muted hover:text-foreground"
              >
                <X className="h-3.5 w-3.5" />
              </button>
            </div>
          )}

          {panelTab === "sheet" && !activeFilePath && (
            <div className="flex-1 flex flex-col items-center justify-center gap-2 px-6 text-center text-sm text-muted-foreground">
              <FileSpreadsheet className="h-8 w-8 opacity-40" />
              <p>从左侧文件栏打开一个工作簿</p>
            </div>
          )}
          {!!activeFilePath && (
            <div className="relative flex-1 min-h-0 overflow-hidden">
              <UniverSheet
                active={isOpen && panelTab === "sheet"}
                fileUrl={fileUrl}
                fileRef={activeFilePath ? fileRefFromSession(activeFilePath, session) : null}
                sessionId={activeSessionId}
                viewGeneration={viewGeneration}
                initialSheet={activeSheet || undefined}
                selectionMode={selectionMode}
                readOnly={Boolean(workbookQuestion && selectionMode)}
                onRangeSelected={handleRangeSelected}
                withStyles={withStyles}
                onCellEdit={handleCellEdit}
                onViewState={reportView}
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
                    onDownload={() => downloadFile(activeFilePath, fileName, activeSessionId, session?.workspaceId).catch(() => { })}
                    onExpand={() => openFullView(activeFilePath, activeSheet ?? undefined)}
                    onClose={closePanel}
                  />
                }
              />
              {panelTab === "history" && (
                <HistoryPaneOverlay>
                  <FileHistoryWorkspace
                    filePath={activeFilePath}
                    workspaceId={session?.workspaceId}
                    active={isOpen && panelTab === "history"}
                    view={historySubview}
                    onViewChange={setHistorySubview}
                    operationCount={operations.length}
                    cellDiffs={fileDiffs}
                  />
                </HistoryPaneOverlay>
              )}
            </div>
          )}

          {(writeConflict || writeError) && (
            <ExcelWriteConflictBar
              filePath={activeFilePath ?? undefined}
              onReload={reloadAfterConflict}
              error={writeConflict ? null : writeError}
            />
          )}

          {/* 选区确认栏 */}
          <WorkbookInteractionBar filePath={activeFilePath} />
          {selectionMode && draftRange && !workbookQuestion && (
            <div className="border-t border-border bg-muted/40 px-3 py-2 flex items-center gap-2">
              <span
                className="text-xs flex-1 min-w-0 truncate"
                style={{ color: "var(--em-primary)" }}
                title={confirmLabel}
              >
                {confirmLabel}
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

      </motion.div>
    </>
  );
}
