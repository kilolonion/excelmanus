"use client";

import { Download, Maximize2, MousePointerSquareDashed, Paintbrush, RefreshCw, X } from "lucide-react";
import type { ReactNode } from "react";
import {
  NATIVE_RIBBON_TAB_CLASS,
  NATIVE_RIBBON_TAB_IDLE_CLASS,
  NATIVE_RIBBON_TAB_SELECTED_CLASS,
} from "@/lib/excel-ribbon-actions";

function RibbonIconButton({
  onClick,
  title,
  pressed,
  children,
}: {
  onClick: () => void;
  title: string;
  pressed?: boolean;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      title={title}
      aria-label={title}
      aria-pressed={pressed}
      className={`inline-flex items-center justify-center size-7 rounded-md shrink-0 transition-colors ${
        pressed
          ? "bg-[var(--em-primary)]/20 text-[var(--em-primary)]"
          : "text-muted-foreground hover:bg-black/5 hover:text-foreground dark:hover:bg-white/10"
      }`}
    >
      {children}
    </button>
  );
}

function RibbonDivider() {
  return <div className="mx-0.5 h-4 w-px bg-border/80" aria-hidden />;
}

export function ExcelRibbonChrome({
  historyActive,
  selectionMode,
  withStyles,
  isMobile,
  onHistory,
  onToggleSelection,
  onCancelSelection,
  onToggleStyles,
  onRefresh,
  onDownload,
  onExpand,
  onClose,
  expandTitle = "展开到聊天区域",
}: {
  historyActive: boolean;
  selectionMode: boolean;
  withStyles: boolean;
  isMobile: boolean;
  onHistory: () => void;
  onToggleSelection: () => void;
  onCancelSelection: () => void;
  onToggleStyles: () => void;
  onRefresh: () => void;
  onDownload: () => void;
  onExpand: () => void;
  onClose: () => void;
  expandTitle?: string;
}) {
  return (
    <>
      <button
        type="button"
        role="tab"
        title="历史"
        data-em-ribbon="history"
        aria-selected={historyActive}
        onClick={onHistory}
        className={`${NATIVE_RIBBON_TAB_CLASS} ${
          historyActive ? NATIVE_RIBBON_TAB_SELECTED_CLASS : NATIVE_RIBBON_TAB_IDLE_CLASS
        }`}
      >
        历史
      </button>
      <div className="flex-1 min-w-2" />
      <div className="em-ribbon-actions flex items-center gap-0.5 shrink-0">
        {selectionMode && (
          <button
            type="button"
            data-em-ribbon="selection-chip"
            onClick={onCancelSelection}
            className="inline-flex items-center gap-1 h-7 px-2 rounded-md text-xs font-medium bg-[var(--em-primary)]/20 text-[var(--em-primary)]"
            title="退出选区模式"
          >
            <MousePointerSquareDashed className="h-3.5 w-3.5" />
            选区中
            <X className="h-3 w-3 opacity-70" />
          </button>
        )}
        <RibbonIconButton
          onClick={onToggleSelection}
          title={selectionMode ? "退出选区模式" : isMobile ? "选区引用（也可长按表格）" : "选区引用（Ctrl / ⌘ 可多选不连续区域，也支持整行、整列）"}
          pressed={selectionMode}
        >
          <MousePointerSquareDashed className="h-3.5 w-3.5" />
        </RibbonIconButton>
        <RibbonIconButton
          onClick={onToggleStyles}
          title={withStyles ? "关闭样式渲染" : "开启样式渲染"}
          pressed={withStyles}
        >
          <Paintbrush className="h-3.5 w-3.5" />
        </RibbonIconButton>
        <RibbonDivider />
        <RibbonIconButton onClick={onRefresh} title="刷新">
          <RefreshCw className="h-3.5 w-3.5" />
        </RibbonIconButton>
        <RibbonIconButton onClick={onDownload} title="下载文件">
          <Download className="h-3.5 w-3.5" />
        </RibbonIconButton>
        <RibbonDivider />
        <RibbonIconButton onClick={onExpand} title={expandTitle}>
          <Maximize2 className="h-3.5 w-3.5" />
        </RibbonIconButton>
        <RibbonIconButton onClick={onClose} title="关闭">
          <X className="h-3.5 w-3.5" />
        </RibbonIconButton>
      </div>
    </>
  );
}
