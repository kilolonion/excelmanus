"use client";

import React, { useState, useCallback, useEffect, useRef, useMemo } from "react";
import {
  Wrench,
  CheckCircle2,
  XCircle,
  Loader2,
  ShieldAlert,
  ChevronDown,
  ChevronRight,
  Upload,
  Check,
  BookOpen,
  PenLine,
  Code,
  Table2,
  ListChecks,
  Search,
  FileText,
  type LucideIcon,
} from "lucide-react";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import { useExcelStore } from "@/stores/excel-store";
import { useSessionStore } from "@/stores/session-store";
import { ExcelPreviewTable } from "@/components/excel/ExcelPreviewTable";
import { ExcelDiffTable } from "@/components/excel/ExcelDiffTable";

// Tool category → icon mapping
const TOOL_ICON_MAP: Record<string, LucideIcon> = {
  read_excel: BookOpen,
  list_sheets: Search,
  write_cells: PenLine,
  insert_rows: Table2,
  insert_columns: Table2,
  create_sheet: Table2,
  delete_sheet: Table2,
  run_code: Code,
  finish_task: ListChecks,
  read_text_file: FileText,
};

function getToolIcon(name: string): LucideIcon {
  return TOOL_ICON_MAP[name] || Wrench;
}

// Build a short summary of tool args for collapsed preview
function argsSummary(name: string, args: Record<string, unknown>): string | null {
  const parts: string[] = [];
  if (args.sheet) parts.push(`sheet: ${args.sheet}`);
  if (args.range) parts.push(`range: ${args.range}`);
  if (args.path) {
    const p = String(args.path);
    parts.push(p.split("/").pop() || p);
  }
  if (name === "run_code" && typeof args.code === "string") {
    const firstLine = args.code.split("\n")[0].slice(0, 50);
    parts.push(firstLine + (args.code.length > 50 ? "…" : ""));
  }
  return parts.length > 0 ? parts.join(" · ") : null;
}

// Status → left border color
const STATUS_BAR_COLOR: Record<string, string> = {
  running: "var(--em-cyan)",
  success: "var(--em-primary)",
  error: "var(--em-error)",
  pending: "var(--em-gold)",
};

const EXCEL_READ_TOOLS = new Set(["read_excel"]);
const EXCEL_WRITE_TOOLS = new Set([
  "write_cells", "insert_rows", "insert_columns",
  "create_sheet", "delete_sheet",
]);
const EXCEL_DIFF_TOOLS = new Set([
  ...EXCEL_WRITE_TOOLS,
  "run_code", "finish_task",
]);

interface ToolCallCardProps {
  toolCallId?: string;
  name: string;
  args: Record<string, unknown>;
  status: "running" | "success" | "error" | "pending";
  result?: string;
  error?: string;
}

export const ToolCallCard = React.memo(function ToolCallCard({ toolCallId, name, args, status, result, error }: ToolCallCardProps) {
  const [open, setOpen] = useState(false);
  const [applyingInline, setApplyingInline] = useState(false);
  const [appliedInline, setAppliedInline] = useState(false);

  // Elapsed timer for running tools
  const startRef = useRef<number | null>(null);
  const [elapsed, setElapsed] = useState(0);
  useEffect(() => {
    if (status !== "running") {
      startRef.current = null;
      return;
    }
    if (startRef.current === null) startRef.current = Date.now();
    const start = startRef.current;
    setElapsed(0);
    const timer = setInterval(() => {
      setElapsed(Math.round((Date.now() - start) / 1000));
    }, 1000);
    return () => clearInterval(timer);
  }, [status]);

  const isExcelRead = EXCEL_READ_TOOLS.has(name);
  const isExcelWrite = EXCEL_WRITE_TOOLS.has(name);

  const activeSessionId = useSessionStore((s) => s.activeSessionId);
  const applyFile = useExcelStore((s) => s.applyFile);
  const pendingBackups = useExcelStore((s) => s.pendingBackups);

  const preview = useExcelStore((s) =>
    toolCallId && isExcelRead ? s.previews[toolCallId] : undefined
  );
  const canHaveDiff = EXCEL_DIFF_TOOLS.has(name);
  
  // 使用 useMemo 缓存 diffs 计算结果，避免无限循环
  const allDiffs = useExcelStore((s) => s.diffs);
  const diffs = useMemo(() => {
    if (!toolCallId || !canHaveDiff) return [];
    return allDiffs.filter((d) => d.toolCallId === toolCallId);
  }, [toolCallId, canHaveDiff, allDiffs]);

  // 按文件去重获取涉及的文件路径
  const diffFilePaths = Array.from(new Set(diffs.map((d) => d.filePath).filter(Boolean)));
  const hasPendingBackup = diffFilePaths.some((fp) =>
    pendingBackups.some((b) => b.original_path === fp)
  );

  const handleInlineApply = useCallback(async () => {
    if (!activeSessionId || diffFilePaths.length === 0) return;
    setApplyingInline(true);
    let anyOk = false;
    for (const fp of diffFilePaths) {
      const ok = await applyFile(activeSessionId, fp);
      if (ok) anyOk = true;
    }
    setApplyingInline(false);
    if (anyOk) setAppliedInline(true);
  }, [activeSessionId, diffFilePaths, applyFile]);

  const StatusIcon = {
    running: <Loader2 className="h-3.5 w-3.5 animate-spin" style={{ color: "var(--em-cyan)" }} />,
    success: <CheckCircle2 className="h-3.5 w-3.5" style={{ color: "var(--em-primary)" }} />,
    error: <XCircle className="h-3.5 w-3.5" style={{ color: "var(--em-error)" }} />,
    pending: <ShieldAlert className="h-3.5 w-3.5" style={{ color: "var(--em-gold)" }} />,
  }[status];

  const ToolIcon = getToolIcon(name);
  const summary = !open ? argsSummary(name, args) : null;
  const barColor = STATUS_BAR_COLOR[status] || "var(--border)";

  return (
    <div className="my-1">
      <Collapsible open={open} onOpenChange={setOpen}>
        <CollapsibleTrigger className={`flex items-center gap-2 rounded-lg border transition-colors w-full text-left text-sm overflow-hidden ${
          status === "pending"
            ? "border-amber-500/40 bg-amber-500/5 hover:bg-amber-500/10"
            : status === "error"
              ? "border-red-300/40 dark:border-red-500/30 hover:bg-red-500/5"
              : "border-border hover:bg-muted/30"
        }`}>
          {/* Left status color bar */}
          <div
            className="self-stretch w-[3px] flex-shrink-0 rounded-l-lg transition-colors duration-500"
            style={{ backgroundColor: barColor }}
          />
          <div className="flex items-center gap-2 flex-1 min-w-0 px-2 py-1.5">
            <ToolIcon className="h-3.5 w-3.5 text-muted-foreground flex-shrink-0" />
            <span className="font-mono text-xs flex-shrink-0">{name}</span>
            {summary && (
              <span className="text-[10px] text-muted-foreground truncate min-w-0">
                {summary}
              </span>
            )}
            <span className="ml-auto flex items-center gap-1.5 flex-shrink-0">
              {status === "pending" && (
                <span className="text-[10px] font-medium" style={{ color: "var(--em-gold)" }}>待审批</span>
              )}
              {status === "running" && elapsed > 0 && (
                <span className="text-[10px] text-muted-foreground tabular-nums">{elapsed}s</span>
              )}
              <span className="transition-transform duration-300" style={{ transform: status === "success" ? "scale(1.15)" : "scale(1)" }}>
                {StatusIcon}
              </span>
              {open ? (
                <ChevronDown className="h-3 w-3 text-muted-foreground" />
              ) : (
                <ChevronRight className="h-3 w-3 text-muted-foreground" />
              )}
            </span>
          </div>
        </CollapsibleTrigger>
        <CollapsibleContent>
          <div className="px-3 py-2 text-xs space-y-2 border border-t-0 border-border rounded-b-lg ml-[3px]">
            {Object.keys(args).length > 0 && (
              <div>
                <p className="font-semibold text-muted-foreground mb-1">参数</p>
                <pre className="bg-muted/30 rounded p-2 overflow-x-auto whitespace-pre-wrap">
                  {JSON.stringify(args, null, 2)}
                </pre>
              </div>
            )}
            {result && (
              <div>
                <p className="font-semibold text-muted-foreground mb-1">结果</p>
                <pre className="bg-muted/30 rounded p-2 overflow-x-auto whitespace-pre-wrap max-h-48">
                  {result}
                </pre>
              </div>
            )}
            {error && (
              <div>
                <p className="font-semibold mb-1" style={{ color: "var(--em-error)" }}>错误</p>
                <pre className="bg-red-50 rounded p-2 overflow-x-auto whitespace-pre-wrap text-red-700">
                  {error}
                </pre>
              </div>
            )}
          </div>
        </CollapsibleContent>
      </Collapsible>

      {/* Excel inline preview / diff — always visible when data available */}
      {preview && <ExcelPreviewTable data={preview} />}
      {diffs.length > 0 && (
        <div className="relative">
          {diffs.map((d, i) => (
            <ExcelDiffTable key={`${d.toolCallId}-${d.sheet}-${i}`} data={d} />
          ))}
          {/* Inline apply button for this diff's file */}
          {(hasPendingBackup || appliedInline) && (
            <div className="flex justify-end px-3 -mt-1 mb-2">
              {appliedInline ? (
                <span className="flex items-center gap-1 text-[11px] text-emerald-600 dark:text-emerald-400">
                  <Check className="h-3 w-3" />
                  已应用到原文件
                </span>
              ) : (
                <button
                  onClick={handleInlineApply}
                  disabled={applyingInline}
                  className="flex items-center gap-1 px-2 py-0.5 rounded text-[11px] font-medium transition-colors text-white"
                  style={{ backgroundColor: "var(--em-primary)" }}
                >
                  {applyingInline ? (
                    <Loader2 className="h-3 w-3 animate-spin" />
                  ) : (
                    <>
                      <Upload className="h-3 w-3" />
                      Apply 到原文件
                    </>
                  )}
                </button>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
});
