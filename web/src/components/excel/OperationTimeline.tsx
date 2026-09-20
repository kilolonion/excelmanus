"use client";

import { createElement, useCallback, useEffect, useMemo, useState } from "react";
import {
  Terminal,
  FileText,
  FileSpreadsheet,
  CheckCircle2,
  XCircle,
  Undo2,
  ChevronDown,
  ChevronRight,
  Clock,
  Loader2,
  FileCode,
  Wrench,
  AlertTriangle,
} from "lucide-react";
import { useExcelStore } from "@/stores/excel-store";
import { useSessionStore } from "@/stores/session-store";
import { useShallow } from "zustand/react/shallow";
import type { OperationRecord, OperationChange } from "@/lib/api";
import { changeSummary, fileBaseName, formatClockTime, operationTouchesFile } from "@/lib/revision-display";

const TOOL_ICON_MAP: Record<string, React.ElementType> = {
  run_code: Terminal,
  run_shell: Terminal,
  write_text_file: FileText,
  inspect_spreadsheet: FileSpreadsheet,
  analyze_spreadsheet: FileSpreadsheet,
  compare_spreadsheets: FileSpreadsheet,
  edit_spreadsheet: FileSpreadsheet,
  format_spreadsheet: FileSpreadsheet,
  manage_spreadsheet_objects: FileSpreadsheet,
  trace_spreadsheet_formulas: FileSpreadsheet,
  manage_spreadsheet_versions: FileSpreadsheet,
};

function getToolIcon(toolName: string): React.ElementType {
  if (TOOL_ICON_MAP[toolName]) return TOOL_ICON_MAP[toolName];
  if (toolName.startsWith("mcp_")) return Wrench;
  if (toolName.includes("excel") || toolName.includes("cell") || toolName.includes("chart"))
    return FileSpreadsheet;
  if (toolName.includes("file") || toolName.includes("text") || toolName.includes("write"))
    return FileText;
  if (toolName.includes("code") || toolName.includes("script")) return FileCode;
  return Wrench;
}

function friendlyToolName(toolName: string): string {
  const map: Record<string, string> = {
    run_code: "运行代码",
    run_shell: "运行命令",
    write_text_file: "写入文件",
    inspect_spreadsheet: "探查表格",
    analyze_spreadsheet: "分析表格",
    compare_spreadsheets: "对比表格",
    edit_spreadsheet: "编辑表格",
    format_spreadsheet: "格式化表格",
    manage_spreadsheet_objects: "管理对象",
    trace_spreadsheet_formulas: "追踪公式",
    manage_spreadsheet_versions: "版本",
  };
  return map[toolName] || toolName;
}

function ChangeTypeBadge({ type }: { type: OperationChange["change_type"] }) {
  const config = {
    added: { label: "新增", cls: "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400" },
    modified: { label: "修改", cls: "bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400" },
    deleted: { label: "删除", cls: "bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400" },
  }[type];
  return (
    <span className={`inline-block px-1.5 py-0.5 rounded text-[10px] font-medium ${config.cls}`}>
      {config.label}
    </span>
  );
}

function formatSize(bytes: number | null): string {
  if (bytes == null) return "-";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function formatDate(isoStr: string): string {
  try {
    const d = new Date(isoStr);
    return d.toLocaleDateString("zh-CN", {
      month: "2-digit",
      day: "2-digit",
    });
  } catch {
    return "";
  }
}

interface OperationTimelineItemProps {
  op: OperationRecord;
  onUndo: (approvalId: string) => void;
  undoing: string | null;
}

function OperationTimelineItem({ op, onUndo, undoing }: OperationTimelineItemProps) {
  const [expanded, setExpanded] = useState(false);
  const isSuccess = op.execution_status === "success";
  const isUndoing = undoing === op.approval_id;
  const summary = useMemo(() => changeSummary(op.changes), [op.changes]);

  return (
    <div className="relative pl-6 pb-3 group">
      <div className="absolute left-[9px] top-0 bottom-0 w-px bg-border group-last:hidden" />
      <div
        className={`absolute left-[4px] top-1.5 w-[11px] h-[11px] rounded-full border-2 ${
          !isSuccess
            ? "border-red-400 bg-red-100 dark:bg-red-900/40"
            : !op.undoable
              ? "border-muted-foreground/40 bg-muted"
              : "border-emerald-400 bg-emerald-100 dark:bg-emerald-900/40"
        }`}
      />
      <div
        className="rounded-xl border border-border/70 bg-card hover:bg-muted/30 transition-colors cursor-pointer"
        onClick={() => setExpanded(!expanded)}
      >
        <div className="flex items-center gap-2 px-3 py-2">
          {createElement(getToolIcon(op.tool_name), {
            className: "h-3.5 w-3.5 text-muted-foreground flex-shrink-0",
          })}
          <span className="text-xs font-medium truncate flex-1">
            {friendlyToolName(op.tool_name)}
          </span>
          {isSuccess ? (
            <CheckCircle2 className="h-3 w-3 text-emerald-500 flex-shrink-0" />
          ) : (
            <XCircle className="h-3 w-3 text-red-500 flex-shrink-0" />
          )}
          {expanded ? (
            <ChevronDown className="h-3 w-3 text-muted-foreground flex-shrink-0" />
          ) : (
            <ChevronRight className="h-3 w-3 text-muted-foreground flex-shrink-0" />
          )}
        </div>
        <div className="px-3 pb-2 flex items-center gap-2 text-[11px] text-muted-foreground">
          <Clock className="h-3 w-3 flex-shrink-0" />
          <span>{formatClockTime(op.applied_at_utc)}</span>
          <span className="text-border">·</span>
          <span className="truncate">{summary}</span>
        </div>
        {expanded && (
          <div className="border-t border-border px-3 py-2 space-y-2">
            {op.result_preview && (
              <p className="text-[11px] text-muted-foreground line-clamp-3 whitespace-pre-wrap">
                {op.result_preview}
              </p>
            )}
            {Object.keys(op.arguments_summary).length > 0 && (
              <div className="space-y-0.5">
                {Object.entries(op.arguments_summary).map(([key, value]) => (
                  <div key={key} className="flex gap-1.5 text-[10px]">
                    <span className="text-muted-foreground font-medium flex-shrink-0">{key}:</span>
                    <span className="text-foreground/80 truncate">{value}</span>
                  </div>
                ))}
              </div>
            )}
            {op.changes.length > 0 && (
              <div className="space-y-1">
                <div className="text-[10px] font-medium text-muted-foreground">文件变更</div>
                {op.changes.map((c, i) => (
                  <div key={i} className="flex items-center gap-1.5 text-[10px] text-foreground/70">
                    <ChangeTypeBadge type={c.change_type} />
                    <span className="truncate">{fileBaseName(c.path) || c.path}</span>
                    {(c.before_size != null || c.after_size != null) && (
                      <span className="text-muted-foreground flex-shrink-0">
                        {formatSize(c.before_size)} → {formatSize(c.after_size)}
                      </span>
                    )}
                  </div>
                ))}
              </div>
            )}
            {isSuccess && (
              <div className="flex justify-end pt-1">
                {op.undoable ? (
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      onUndo(op.approval_id);
                    }}
                    disabled={isUndoing}
                    className="flex items-center gap-1 px-2 py-1 rounded-lg text-[11px] font-medium text-orange-600 dark:text-orange-400 hover:bg-orange-50 dark:hover:bg-orange-900/20 transition-colors disabled:opacity-50"
                  >
                    {isUndoing ? <Loader2 className="h-3 w-3 animate-spin" /> : <Undo2 className="h-3 w-3" />}
                    撤销这一步
                  </button>
                ) : (
                  <span className="flex items-center gap-1 text-[10px] text-muted-foreground/60">
                    <AlertTriangle className="h-3 w-3" />
                    不可撤销
                  </span>
                )}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

export function OperationTimeline({ filePath = null }: { filePath?: string | null }) {
  const activeSessionId = useSessionStore((s) => s.activeSessionId);
  const { operations, operationsLoading, operationsLoaded, fetchOperationHistory, undoOperationById } =
    useExcelStore(
      useShallow((s) => ({
        operations: s.operations,
        operationsLoading: s.operationsLoading,
        operationsLoaded: s.operationsLoaded,
        fetchOperationHistory: s.fetchOperationHistory,
        undoOperationById: s.undoOperationById,
      })),
    );

  const [undoing, setUndoing] = useState<string | null>(null);
  const [onlyCurrentFile, setOnlyCurrentFile] = useState(Boolean(filePath));

  useEffect(() => {
    setOnlyCurrentFile(Boolean(filePath));
  }, [filePath]);

  useEffect(() => {
    if (activeSessionId && !operationsLoaded && !operationsLoading) {
      fetchOperationHistory(activeSessionId);
    }
  }, [activeSessionId, operationsLoaded, operationsLoading, fetchOperationHistory]);

  const handleUndo = useCallback(
    async (approvalId: string) => {
      if (!activeSessionId) return;
      setUndoing(approvalId);
      await undoOperationById(activeSessionId, approvalId);
      setUndoing(null);
    },
    [activeSessionId, undoOperationById],
  );

  const handleRefresh = useCallback(() => {
    if (activeSessionId) {
      fetchOperationHistory(activeSessionId);
    }
  }, [activeSessionId, fetchOperationHistory]);

  const visible = useMemo(() => {
    if (!onlyCurrentFile || !filePath) return operations;
    return operations.filter((op) => operationTouchesFile(op, filePath));
  }, [operations, onlyCurrentFile, filePath]);

  const grouped = useMemo(() => {
    const groups: { key: string; label: string; ops: OperationRecord[] }[] = [];
    let currentKey = "";
    for (const op of visible) {
      const turn = op.session_turn;
      const date = formatDate(op.applied_at_utc);
      const key = turn != null ? `turn-${turn}` : `date-${date}`;
      if (key !== currentKey) {
        currentKey = key;
        groups.push({
          key,
          label: turn != null ? `第 ${turn} 轮` : date || "更早",
          ops: [],
        });
      }
      groups[groups.length - 1].ops.push(op);
    }
    return groups;
  }, [visible]);

  if (operationsLoading && !operationsLoaded) {
    return (
      <div className="flex flex-col items-center justify-center py-12 text-muted-foreground">
        <Loader2 className="h-5 w-5 animate-spin mb-2" />
        <span className="text-xs">加载操作记录...</span>
      </div>
    );
  }

  return (
    <div className="flex flex-col h-full">
      <div className="flex items-center justify-between px-3 py-2 gap-2">
        <span className="text-[11px] text-muted-foreground">
          {visible.length === operations.length
            ? `共 ${operations.length} 步`
            : `${visible.length} / ${operations.length} 步`}
        </span>
        <div className="flex items-center gap-1">
          {filePath && (
            <button
              type="button"
              onClick={() => setOnlyCurrentFile((v) => !v)}
              className={`h-6 px-2 rounded-md text-[10px] font-medium transition-colors ${
                onlyCurrentFile
                  ? "bg-[var(--em-primary)]/12 text-[var(--em-primary)]"
                  : "text-muted-foreground hover:bg-muted"
              }`}
            >
              仅当前文件
            </button>
          )}
          <button
            onClick={handleRefresh}
            disabled={operationsLoading}
            className="p-1 rounded hover:bg-muted transition-colors text-muted-foreground hover:text-foreground disabled:opacity-50"
            title="刷新"
          >
            <Loader2 className={`h-3.5 w-3.5 ${operationsLoading ? "animate-spin" : ""}`} />
          </button>
        </div>
      </div>

      {visible.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-10 text-muted-foreground px-6 text-center">
          <Clock className="h-8 w-8 mb-2 opacity-30" />
          <span className="text-xs font-medium text-foreground/80">
            {operations.length === 0 ? "还没有操作记录" : "当前文件没有被改过"}
          </span>
          <p className="mt-1 text-[11px] leading-5">
            {operations.length === 0
              ? "AI 改文件或跑工具后，这里可以按步撤销。"
              : "关掉「仅当前文件」可看本会话全部操作。"}
          </p>
          <button
            onClick={handleRefresh}
            className="mt-2 text-[11px] text-[var(--em-primary)] hover:underline"
          >
            刷新
          </button>
        </div>
      ) : (
        <div className="flex-1 overflow-y-auto px-3 pb-3">
          {grouped.map((group) => (
            <div key={group.key}>
              <div className="text-[10px] font-medium text-muted-foreground mb-2 pl-6">
                {group.label}
              </div>
              {group.ops.map((op) => (
                <OperationTimelineItem
                  key={op.approval_id}
                  op={op}
                  onUndo={handleUndo}
                  undoing={undoing}
                />
              ))}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
