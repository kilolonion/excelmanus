"use client";

import { useCallback, useMemo, useState } from "react";
import dynamic from "next/dynamic";
import { ArrowLeft, Maximize2, MousePointerSquareDashed, Check, XCircle, Download, Paintbrush } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useIsMobile } from "@/hooks/use-mobile";
import { useExcelStore } from "@/stores/excel-store";
import { useSessionStore } from "@/stores/session-store";
import { buildExcelFileUrl, downloadFile } from "@/lib/api";

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

export function ExcelFullView() {
  const isMobile = useIsMobile();
  const fullViewPath = useExcelStore((s) => s.fullViewPath);
  const fullViewSheet = useExcelStore((s) => s.fullViewSheet);
  const closeFullView = useExcelStore((s) => s.closeFullView);
  const openPanel = useExcelStore((s) => s.openPanel);
  const selectionMode = useExcelStore((s) => s.selectionMode);
  const enterSelectionMode = useExcelStore((s) => s.enterSelectionMode);
  const exitSelectionMode = useExcelStore((s) => s.exitSelectionMode);
  const confirmSelection = useExcelStore((s) => s.confirmSelection);
  const activeSessionId = useSessionStore((s) => s.activeSessionId);

  const [pendingRange, setPendingRange] = useState<{ range: string; sheet: string } | null>(null);
  const [withStyles, setWithStyles] = useState(true);

  const handleRangeSelected = useCallback((range: string, sheet: string) => {
    setPendingRange({ range, sheet });
  }, []);

  const handleConfirmRange = useCallback(() => {
    if (pendingRange && fullViewPath) {
      confirmSelection({
        filePath: fullViewPath,
        sheet: pendingRange.sheet,
        range: pendingRange.range,
      });
      setPendingRange(null);
    }
  }, [pendingRange, fullViewPath, confirmSelection]);

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
    () => (fullViewPath ? buildExcelFileUrl(fullViewPath, activeSessionId ?? undefined) : ""),
    [fullViewPath, activeSessionId]
  );

  const fileName = fullViewPath?.split("/").pop() || "未知文件";

  if (!fullViewPath) return null;

  const handleSwitchToPanel = () => {
    openPanel(fullViewPath, fullViewSheet ?? undefined);
    closeFullView();
  };

  return (
    <div className="flex flex-col h-full">
      {/* 顶栏 */}
      <div className="flex items-center gap-2 px-3 py-2 border-b border-border bg-muted/20 flex-shrink-0">
        <Button
          variant="ghost"
          size="sm"
          onClick={closeFullView}
          className="h-7 gap-1.5 text-xs"
        >
          <ArrowLeft className="h-3.5 w-3.5" />
          返回聊天
        </Button>
        <div className="h-4 w-px bg-border" />
        <span className="text-sm font-medium truncate">{fileName}</span>
        {fullViewSheet && (
          <span className="text-xs text-muted-foreground">/ {fullViewSheet}</span>
        )}
        <div className="flex-1" />
        <Button
          variant="ghost"
          size="sm"
          onClick={toggleSelectionMode}
          className={`h-7 gap-1.5 text-xs ${
            selectionMode
              ? "text-[var(--em-primary)] bg-[var(--em-primary)]/10"
              : "text-muted-foreground"
          }`}
          title={selectionMode ? "退出选区模式" : (isMobile ? "选区引用（也可长按表格）" : "选区引用")}
        >
          <MousePointerSquareDashed className="h-3.5 w-3.5" />
          选区引用
        </Button>
        <Button
          variant="ghost"
          size="sm"
          onClick={() => setWithStyles((v) => !v)}
          className={`h-7 gap-1.5 text-xs ${
            withStyles
              ? "text-[var(--em-primary)] bg-[var(--em-primary)]/10"
              : "text-muted-foreground"
          }`}
          title={withStyles ? "关闭样式渲染" : "开启样式渲染"}
        >
          <Paintbrush className="h-3.5 w-3.5" />
          样式
        </Button>
        <Button
          variant="ghost"
          size="sm"
          onClick={() => fullViewPath && downloadFile(fullViewPath, fileName, activeSessionId ?? undefined).catch(() => {})}
          className="h-7 gap-1.5 text-xs text-muted-foreground"
          title="下载文件"
        >
          <Download className="h-3.5 w-3.5" />
          下载
        </Button>
        <Button
          variant="ghost"
          size="sm"
          onClick={handleSwitchToPanel}
          className="h-7 gap-1.5 text-xs text-muted-foreground"
          title="切换到侧边面板"
        >
          <Maximize2 className="h-3.5 w-3.5" />
          侧边面板
        </Button>
      </div>

      {/* Univer 表格 — 占满剩余高度 */}
      <div className="flex-1 min-h-0">
        <UniverSheet
          fileUrl={fileUrl}
          initialSheet={fullViewSheet ?? undefined}
          selectionMode={selectionMode}
          onRangeSelected={handleRangeSelected}
          withStyles={withStyles}
        />
      </div>

      {/* 选区确认栏 */}
      {selectionMode && pendingRange && (
        <div className="border-t border-border bg-muted/40 px-3 py-2 flex items-center gap-2 flex-shrink-0">
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
    </div>
  );
}
