"use client";

import { useCallback, useEffect, useMemo, useState, useRef } from "react";
import dynamic from "next/dynamic";
import { motion } from "framer-motion";
import { X, RefreshCw, Clock, Maximize2, MousePointerSquareDashed, Check, XCircle, Upload, Loader2, Download, Paintbrush, MoreHorizontal, History, FileSpreadsheet } from "lucide-react";
import { OperationTimeline } from "./OperationTimeline";
import {
  DropdownMenu,
  DropdownMenuTrigger,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuCheckboxItem,
} from "@/components/ui/dropdown-menu";
import { panelSlideVariants, panelSlideVariantsMobile, panelSlideVariantsMedium } from "@/lib/sidebar-motion";
import { useShallow } from "zustand/react/shallow";
import { useIsMobile, useIsDesktop, useIsMediumScreen } from "@/hooks/use-mobile";
import { useResizablePanel } from "@/hooks/use-resizable-panel";
import { useExcelStore } from "@/stores/excel-store";
import { useSessionStore } from "@/stores/session-store";
import { buildExcelFileUrl, downloadFile, normalizeExcelPath, invalidateSnapshotCache } from "@/lib/api";

const UniverSheet = dynamic(
  () => import("./UniverSheet").then((m) => ({ default: m.UniverSheet })),
  { ssr: false, loading: () => <div className="flex items-center justify-center h-full text-sm text-muted-foreground">加载 Excel 引擎...</div> }
);

export function ExcelSidePanel() {
  const isMobile = useIsMobile();
  const isDesktop = useIsDesktop();
  const isMediumScreen = useIsMediumScreen();

  const {
    panelOpen, activeFilePath, activeSheet, diffs, closePanel,
    openFullView, selectionMode, enterSelectionMode,
    exitSelectionMode, confirmSelection, pendingBackups, applyFile,
    recentFiles, openPanel, removeRecentFile,
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
    recentFiles: s.recentFiles,
    openPanel: s.openPanel,
    removeRecentFile: s.removeRecentFile,
  })));

  const activeSessionId = useSessionStore((s) => s.activeSessionId);

  // ── 桌面端拖拽调宽 ──
  const {
    panelWidth: resizableWidth,
    isFloatingByResize,
    handleProps: resizeHandleProps,
    isDragging: isResizing,
  } = useResizablePanel(isDesktop);

  // 根据屏幕尺寸决定显示模式
  // 桌面端（>=1280px）：固定右侧栏（拖宽超过 50vw 时切换为浮动）
  // 中等屏幕（1024-1279px）：浮层模式，类似移动端但更大
  // 移动端（<1024px）：全屏浮层
  const useFloatingMode = !isDesktop || isFloatingByResize;
  const panelWidth = isMobile ? undefined : isDesktop ? resizableWidth : 600;

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

  // 来自 Univer 选区的待确认范围（尚未确认）
  const [pendingRange, setPendingRange] = useState<{ range: string; sheet: string } | null>(null);
  const [withStyles, setWithStyles] = useState(true);
  const [activeTab, setActiveTab] = useState<"sheet" | "timeline">("sheet");

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
    // 清除 snapshot 缓存后递增计数器强制刷新
    if (activeFilePath) invalidateSnapshotCache(activeFilePath);
    useExcelStore.setState((s) => ({ refreshCounter: s.refreshCounter + 1 }));
  }, [activeFilePath]);

  const isOpen = panelOpen && !!activeFilePath;

  // 面板首次打开后保持挂载，关闭时用 CSS 隐藏，避免 Univer 实例被销毁重建
  const [hasEverMounted, setHasEverMounted] = useState(false);
  useEffect(() => {
    if (isOpen && !hasEverMounted) setHasEverMounted(true);
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
            : "relative flex flex-col h-full border-l border-border bg-background"
        }
        style={{
          ...(useFloatingMode ? (isMobile ? {} : { width: panelWidth }) : { width: panelWidth }),
          // 拖拽中禁用 transition 以获得流畅体验
          ...(isResizing ? { transition: "none" } : {}),
          // 关闭时隐藏但保持挂载
          ...(!isOpen ? { display: "none" } : {}),
        }}
        onTouchStart={handlePanelTouchStart}
        onTouchEnd={handlePanelTouchEnd}
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
              <div className="flex justify-center py-1.5 flex-shrink-0">
                <div className="w-10 h-1 rounded-full bg-muted-foreground/30" />
              </div>
            )}

          {/* 多文件 Tab 栏 */}
          {recentFiles.length > 1 && (
            <div ref={tabBarRef} className="flex items-center bg-muted/20 border-b border-border min-h-[32px] select-none overflow-x-auto scrollbar-none flex-shrink-0">
              {recentFiles.slice(0, 10).map((file) => {
                const isActive = file.path === activeFilePath;
                return (
                  <div
                    key={file.path}
                    onClick={() => openPanel(file.path)}
                    className={`group relative flex items-center gap-1.5 px-2.5 h-[32px] text-[11px] cursor-pointer shrink-0 border-r border-border/40 transition-colors ${
                      isActive
                        ? "bg-background text-foreground"
                        : "text-muted-foreground hover:bg-muted/40 hover:text-foreground"
                    }`}
                  >
                    {isActive && (
                      <div className="absolute top-0 left-0 right-0 h-[2px]" style={{ backgroundColor: "var(--em-primary)" }} />
                    )}
                    <FileSpreadsheet className="h-3 w-3 shrink-0 text-emerald-600 dark:text-emerald-400" />
                    <span className="truncate max-w-[100px]">{file.filename}</span>
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        removeRecentFile(file.path);
                        if (isActive) {
                          const remaining = recentFiles.filter((f) => f.path !== file.path);
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

          {/* 头部 */}
          <div className={`flex items-center justify-between px-3 border-b border-border bg-muted/30 ${isMobile ? "py-1" : "py-2"}`}>
            <div className="flex items-center gap-2 min-w-0 flex-1">
              <span className="text-sm font-medium truncate">{fileName}</span>
              {activeSheet && (
                <span className="text-xs text-muted-foreground flex-shrink-0">/ {activeSheet}</span>
              )}
            </div>
            <div className="flex items-center gap-1 flex-shrink-0">
              {/* 桌面端/中等屏幕：全部按钮直接显示 */}
              {!isMobile && (
                <>
                  <button
                    onClick={toggleSelectionMode}
                    className={`p-1.5 rounded transition-colors ${selectionMode
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
                    onClick={() => setWithStyles((v) => !v)}
                    className={`p-1.5 rounded transition-colors ${withStyles
                        ? "text-[var(--em-primary)]"
                        : "text-muted-foreground hover:text-foreground"
                      } hover:bg-muted`}
                    title={withStyles ? "关闭样式渲染" : "开启样式渲染"}
                  >
                    <Paintbrush className="h-3.5 w-3.5" />
                  </button>
                  <button
                    onClick={() => activeFilePath && downloadFile(activeFilePath, fileName, activeSessionId ?? undefined).catch(() => { })}
                    className="p-1.5 rounded hover:bg-muted transition-colors text-muted-foreground hover:text-foreground"
                    title="下载文件"
                  >
                    <Download className="h-3.5 w-3.5" />
                  </button>
                  <button
                    onClick={() => openFullView(activeFilePath!, activeSheet ?? undefined)}
                    className="p-1.5 rounded hover:bg-muted transition-colors text-muted-foreground hover:text-foreground"
                    title="展开到聊天区域"
                  >
                    <Maximize2 className="h-3.5 w-3.5" />
                  </button>
                </>
              )}

              {/* 移动端：刷新 + 溢出菜单 + 关闭 */}
              {isMobile && (
                <>
                  <button
                    onClick={handleRefresh}
                    className="p-2 rounded hover:bg-muted transition-colors text-muted-foreground hover:text-foreground"
                    title="刷新"
                  >
                    <RefreshCw className="h-3.5 w-3.5" />
                  </button>
                  <DropdownMenu>
                    <DropdownMenuTrigger asChild>
                      <button
                        className="p-2 rounded hover:bg-muted transition-colors text-muted-foreground hover:text-foreground"
                        title="更多操作"
                      >
                        <MoreHorizontal className="h-3.5 w-3.5" />
                      </button>
                    </DropdownMenuTrigger>
                    <DropdownMenuContent align="end" sideOffset={4}>
                      <DropdownMenuItem onClick={toggleSelectionMode}>
                        <MousePointerSquareDashed className="h-4 w-4" />
                        {selectionMode ? "退出选区模式" : "选区引用（也可长按表格）"}
                      </DropdownMenuItem>
                      <DropdownMenuCheckboxItem
                        checked={withStyles}
                        onCheckedChange={() => setWithStyles((v) => !v)}
                      >
                        样式渲染
                      </DropdownMenuCheckboxItem>
                      <DropdownMenuItem
                        onClick={() => activeFilePath && downloadFile(activeFilePath, fileName, activeSessionId ?? undefined).catch(() => { })}
                      >
                        <Download className="h-4 w-4" />
                        下载文件
                      </DropdownMenuItem>
                      <DropdownMenuItem
                        onClick={() => openFullView(activeFilePath!, activeSheet ?? undefined)}
                      >
                        <Maximize2 className="h-4 w-4" />
                        展开到聊天区域
                      </DropdownMenuItem>
                    </DropdownMenuContent>
                  </DropdownMenu>
                </>
              )}

              <button
                onClick={closePanel}
                className={`${isMobile ? "p-2" : "p-1.5"} rounded hover:bg-muted transition-colors text-muted-foreground hover:text-foreground`}
                title="关闭"
              >
                <X className="h-3.5 w-3.5" />
              </button>
            </div>
          </div>

          {/* Tab 切换栏 */}
          <div className="flex border-b border-border bg-muted/20 px-1">
            <button
              onClick={() => setActiveTab("sheet")}
              className={`flex items-center gap-1 px-3 py-1.5 text-xs font-medium border-b-2 transition-colors ${
                activeTab === "sheet"
                  ? "border-[var(--em-primary)] text-foreground"
                  : "border-transparent text-muted-foreground hover:text-foreground"
              }`}
            >
              <FileSpreadsheet className="h-3 w-3" />
              表格
            </button>
            <button
              onClick={() => setActiveTab("timeline")}
              className={`flex items-center gap-1 px-3 py-1.5 text-xs font-medium border-b-2 transition-colors ${
                activeTab === "timeline"
                  ? "border-[var(--em-primary)] text-foreground"
                  : "border-transparent text-muted-foreground hover:text-foreground"
              }`}
            >
              <History className="h-3 w-3" />
              操作历史
            </button>
          </div>

          {/* 内容区 */}
          {activeTab === "sheet" ? (
            <div className="flex-1 overflow-hidden">
              <UniverSheet
                fileUrl={fileUrl}
                initialSheet={activeSheet || undefined}
                selectionMode={selectionMode}
                onRangeSelected={handleRangeSelected}
                withStyles={withStyles}
              />
            </div>
          ) : (
            <div className="flex-1 overflow-hidden">
              <OperationTimeline />
            </div>
          )}

          {/* 选区确认栏 */}
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

          {/* 应用到原文件栏 */}
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

          {/* 变更历史（底栏） */}
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
    </>
  );
}
