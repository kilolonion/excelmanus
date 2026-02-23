"use client";

import { useState, useCallback } from "react";
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

export function ToolCallCard({ toolCallId, name, args, status, result, error }: ToolCallCardProps) {
  const [open, setOpen] = useState(false);
  const [applyingInline, setApplyingInline] = useState(false);
  const [appliedInline, setAppliedInline] = useState(false);

  const isExcelRead = EXCEL_READ_TOOLS.has(name);
  const isExcelWrite = EXCEL_WRITE_TOOLS.has(name);

  const activeSessionId = useSessionStore((s) => s.activeSessionId);
  const applyFile = useExcelStore((s) => s.applyFile);
  const pendingBackups = useExcelStore((s) => s.pendingBackups);

  const preview = useExcelStore((s) =>
    toolCallId && isExcelRead ? s.previews[toolCallId] : undefined
  );
  const canHaveDiff = EXCEL_DIFF_TOOLS.has(name);
  const diff = useExcelStore((s) =>
    toolCallId && canHaveDiff
      ? s.diffs.find((d) => d.toolCallId === toolCallId)
      : undefined
  );

  const diffFilePath = diff?.filePath ?? null;
  const hasPendingBackup = diffFilePath
    ? pendingBackups.some((b) => b.original_path === diffFilePath)
    : false;

  const handleInlineApply = useCallback(async () => {
    if (!activeSessionId || !diffFilePath) return;
    setApplyingInline(true);
    const ok = await applyFile(activeSessionId, diffFilePath);
    setApplyingInline(false);
    if (ok) setAppliedInline(true);
  }, [activeSessionId, diffFilePath, applyFile]);

  const StatusIcon = {
    running: <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />,
    success: <CheckCircle2 className="h-4 w-4" style={{ color: "var(--em-primary)" }} />,
    error: <XCircle className="h-4 w-4" style={{ color: "var(--em-error)" }} />,
    pending: <ShieldAlert className="h-4 w-4 text-amber-500" />,
  }[status];

  return (
    <div className="my-2">
      <Collapsible open={open} onOpenChange={setOpen}>
        <CollapsibleTrigger className={`flex items-center gap-2 px-3 py-2 rounded-lg border transition-colors w-full text-left text-sm ${
          status === "pending"
            ? "border-amber-500/40 bg-amber-500/5 hover:bg-amber-500/10"
            : "border-border hover:bg-muted/30"
        }`}>
          <Wrench className="h-4 w-4 text-muted-foreground flex-shrink-0" />
          <span className="font-mono text-xs flex-1 truncate">{name}</span>
          {status === "pending" && (
            <span className="text-[10px] text-amber-600 font-medium">待审批</span>
          )}
          {StatusIcon}
          {open ? (
            <ChevronDown className="h-3 w-3 text-muted-foreground" />
          ) : (
            <ChevronRight className="h-3 w-3 text-muted-foreground" />
          )}
        </CollapsibleTrigger>
        <CollapsibleContent>
          <div className="px-3 py-2 text-xs space-y-2 border border-t-0 border-border rounded-b-lg">
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
      {diff && (
        <div className="relative">
          <ExcelDiffTable data={diff} />
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
}
