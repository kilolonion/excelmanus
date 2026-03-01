"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
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

// ── 工具图标映射 ─────────────────────────────────────────

const TOOL_ICON_MAP: Record<string, React.ElementType> = {
  run_code: Terminal,
  run_shell: Terminal,
  write_text_file: FileText,
  write_cells: FileSpreadsheet,
  update_cells: FileSpreadsheet,
  create_excel: FileSpreadsheet,
  read_excel: FileSpreadsheet,
  create_chart: FileSpreadsheet,
  compare_excel: FileSpreadsheet,
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

// ── 工具名称友好化 ──────────────────────────────────────

function friendlyToolName(toolName: string): string {
  const map: Record<string, string> = {
    run_code: "运行代码",
    run_shell: "运行命令",
    write_text_file: "写入文件",
    write_cells: "写入单元格",
    update_cells: "更新单元格",
    create_excel: "创建表格",
    read_excel: "读取表格",
    create_chart: "创建图表",
    compare_excel: "对比表格",
  };
  return map[toolName] || toolName;
}

// ── 变更类型标签 ────────────────────────────────────────

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

// ── 格式化文件大小 ──────────────────────────────────────

function formatSize(bytes: number | null): string {
  if (bytes == null) return "-";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

// ── 格式化时间 ──────────────────────────────────────────

function formatTime(isoStr: string): string {
  try {
    const d = new Date(isoStr);
    return d.toLocaleTimeString("zh-CN", {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    });
  } catch {
    return isoStr;
  }
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

// ── OperationTimelineItem ───────────────────────────────

interface OperationTimelineItemProps {
  op: OperationRecord;
  onUndo: (approvalId: string) => void;
  undoing: string | null;
}

function OperationTimelineItem({ op, onUndo, undoing }: OperationTimelineItemProps) {
  const [expanded, setExpanded] = useState(false);
  const Icon = getToolIcon(op.tool_name);
  const isSuccess = op.execution_status === "success";
  const isUndoing = undoing === op.approval_id;

  const changeSummary = useMemo(() => {
    const added = op.changes.filter((c) => c.change_type === "added").length;
    const modified = op.changes.filter((c) => c.change_type === "modified").length;
    const deleted = op.changes.filter((c) => c.change_type === "deleted").length;
    const parts: string[] = [];
    if (modified > 0) parts.push(`${modified} 修改`);
    if (added > 0) parts.push(`${added} 新增`);
    if (deleted > 0) parts.push(`${deleted} 删除`);
    return parts.join(", ") || "无文件变更";
  }, [op.changes]);

  return (
    <div className="relative pl-6 pb-4 group">
      {/* 时间线竖线 */}
      <div className="absolute left-[9px] top-0 bottom-0 w-px bg-border group-last:hidden" />

      {/* 状态圆点 */}
      <div
        className={`absolute left-[4px] top-1 w-[11px] h-[11px] rounded-full border-2 ${
          !isSuccess
            ? "border-red-400 bg-red-100 dark:bg-red-900/40"
            : !op.undoable
              ? "border-muted-foreground/40 bg-muted"
              : "border-emerald-400 bg-emerald-100 dark:bg-emerald-900/40"
        }`}
      />

      {/* 内容 */}
      <div
        className="rounded-lg border border-border bg-card hover:bg-muted/30 transition-colors cursor-pointer"
        onClick={() => setExpanded(!expanded)}
      >
        {/* 头部行 */}
        <div className="flex items-center gap-2 px-3 py-2">
          <Icon className="h-3.5 w-3.5 text-muted-foreground flex-shrink-0" />
          <span className="text-xs font-medium truncate flex-1">
            {friendlyToolName(op.tool_name)}
          </span>
          {op.session_turn != null && (
            <span className="text-[10px] text-muted-foreground flex-shrink-0">
              Turn {op.session_turn}
            </span>
          )}
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

        {/* 摘要行 */}
        <div className="px-3 pb-2 flex items-center gap-2 text-[11px] text-muted-foreground">
          <Clock className="h-3 w-3 flex-shrink-0" />
          <span>{formatTime(op.applied_at_utc)}</span>
          <span className="text-border">·</span>
          <span>{changeSummary}</span>
        </div>

        {/* 展开详情 */}
        {expanded && (
          <div className="border-t border-border px-3 py-2 space-y-2">
            {/* 结果预览 */}
            {op.result_preview && (
              <p className="text-[11px] text-muted-foreground line-clamp-3 whitespace-pre-wrap">
                {op.result_preview}
              </p>
            )}

            {/* 参数摘要 */}
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

            {/* 文件变更列表 */}
            {op.changes.length > 0 && (
              <div className="space-y-1">
                <div className="text-[10px] font-medium text-muted-foreground">文件变更</div>
                {op.changes.map((c, i) => (
                  <div
                    key={i}
                    className="flex items-center gap-1.5 text-[10px] text-foreground/70"
                  >
                    <ChangeTypeBadge type={c.change_type} />
                    <span className="truncate font-mono">{c.path}</span>
                    {(c.before_size != null || c.after_size != null) && (
                      <span className="text-muted-foreground flex-shrink-0">
                        {formatSize(c.before_size)} → {formatSize(c.after_size)}
                      </span>
                    )}
                  </div>
                ))}
              </div>
            )}

            {/* 回滚按钮 */}
            {isSuccess && (
              <div className="flex justify-end pt-1">
                {op.undoable ? (
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      onUndo(op.approval_id);
                    }}
                    disabled={isUndoing}
                    className="flex items-center gap-1 px-2 py-1 rounded text-[11px] font-medium text-orange-600 dark:text-orange-400 hover:bg-orange-50 dark:hover:bg-orange-900/20 transition-colors disabled:opacity-50"
                  >
                    {isUndoing ? (
                      <Loader2 className="h-3 w-3 animate-spin" />
                    ) : (
                      <Undo2 className="h-3 w-3" />
                    )}
                    回滚
                  </button>
                ) : (
                  <span className="flex items-center gap-1 text-[10px] text-muted-foreground/60">
                    <AlertTriangle className="h-3 w-3" />
                    不可回滚
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

// ── OperationTimeline ──────────────────────────────────

export function OperationTimeline() {
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

  // 首次加载
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

  // 按日期分组
  const grouped = useMemo(() => {
    const groups: { date: string; ops: OperationRecord[] }[] = [];
    let currentDate = "";
    for (const op of operations) {
      const d = formatDate(op.applied_at_utc);
      if (d !== currentDate) {
        currentDate = d;
        groups.push({ date: d, ops: [] });
      }
      groups[groups.length - 1].ops.push(op);
    }
    return groups;
  }, [operations]);

  if (operationsLoading && !operationsLoaded) {
    return (
      <div className="flex flex-col items-center justify-center py-12 text-muted-foreground">
        <Loader2 className="h-5 w-5 animate-spin mb-2" />
        <span className="text-xs">加载操作历史...</span>
      </div>
    );
  }

  if (operations.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-12 text-muted-foreground">
        <Clock className="h-8 w-8 mb-2 opacity-30" />
        <span className="text-xs">暂无操作历史</span>
        <button
          onClick={handleRefresh}
          className="mt-2 text-[11px] text-[var(--em-primary)] hover:underline"
        >
          刷新
        </button>
      </div>
    );
  }

  return (
    <div className="flex flex-col h-full">
      {/* 头部 */}
      <div className="flex items-center justify-between px-3 py-2 border-b border-border">
        <span className="text-xs font-medium text-muted-foreground">
          共 {operations.length} 条操作
        </span>
        <button
          onClick={handleRefresh}
          disabled={operationsLoading}
          className="p-1 rounded hover:bg-muted transition-colors text-muted-foreground hover:text-foreground disabled:opacity-50"
          title="刷新"
        >
          <Loader2
            className={`h-3.5 w-3.5 ${operationsLoading ? "animate-spin" : ""}`}
          />
        </button>
      </div>

      {/* 时间线 */}
      <div className="flex-1 overflow-y-auto px-3 py-3">
        {grouped.map((group) => (
          <div key={group.date}>
            <div className="text-[10px] font-medium text-muted-foreground mb-2 pl-6">
              {group.date}
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
    </div>
  );
}
