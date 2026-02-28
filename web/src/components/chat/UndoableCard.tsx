"use client";

import { useState } from "react";
import { Undo2, CheckCircle2, XCircle, Loader2, ChevronRight } from "lucide-react";

import { undoApproval } from "@/lib/api";
import { useSessionStore } from "@/stores/session-store";

interface UndoableCardProps {
  approvalId: string;
  toolName: string;
  success: boolean;
  undoable: boolean;
  hasChanges?: boolean;
  sessionId?: string;
  undone?: boolean;
  undoError?: string;
  onUndone?: (approvalId: string, error?: string) => void;
}

export function UndoableCard({
  approvalId,
  toolName,
  success,
  undoable,
  hasChanges,
  sessionId,
  undone,
  undoError,
  onUndone,
}: UndoableCardProps) {
  const [loading, setLoading] = useState(false);

  const storeSessionId = useSessionStore((s) => s.activeSessionId);
  const effectiveSessionId = sessionId || storeSessionId;

  const handleUndo = async () => {
    if (loading || undone || !effectiveSessionId) return;
    setLoading(true);
    try {
      const res = await undoApproval(approvalId, effectiveSessionId);
      onUndone?.(approvalId, res.status === "ok" ? undefined : res.message);
    } catch (err) {
      onUndone?.(approvalId, (err as Error).message);
    } finally {
      setLoading(false);
    }
  };

  const barColor = undone
    ? "var(--em-muted)"
    : success
      ? "var(--em-primary)"
      : "var(--em-error)";

  const borderCls = undone
    ? "border-border"
    : success
      ? "border-emerald-300/40 dark:border-emerald-500/20"
      : "border-red-300/50 dark:border-red-500/25";

  const bgCls = undone
    ? "bg-muted/30"
    : success
      ? "bg-emerald-500/[0.02] dark:bg-emerald-500/[0.03] hover:bg-emerald-500/[0.05]"
      : "bg-red-500/[0.03] hover:bg-red-500/[0.06]";

  const iconBg = undone
    ? "bg-muted/50"
    : success
      ? "bg-emerald-500/10 dark:bg-emerald-400/15"
      : "bg-red-500/10 dark:bg-red-400/15";

  const statusLabel = undone
    ? "已回滚"
    : success
      ? "已执行"
      : "已拒绝";

  return (
    <div
      className={`my-1 flex items-center gap-0 rounded-lg border transition-all duration-200 text-sm overflow-hidden hover:shadow-sm ${borderCls} ${bgCls}`}
    >
      {/* 左侧强调条 */}
      <div
        className="self-stretch w-[3px] flex-shrink-0 rounded-l-lg"
        style={{ backgroundColor: barColor }}
      />

      <div className="flex items-center gap-2 flex-1 min-w-0 px-2.5 py-1.5">
        {/* 圆形图标徽章 */}
        <span className={`flex items-center justify-center h-5 w-5 rounded-full flex-shrink-0 ${iconBg}`}>
          {success ? (
            <CheckCircle2
              className="h-3 w-3"
              style={{ color: undone ? "var(--em-muted)" : "var(--em-primary)" }}
            />
          ) : (
            <XCircle className="h-3 w-3" style={{ color: "var(--em-error)" }} />
          )}
        </span>

        {/* 工具名胶囊 */}
        <span className={`inline-flex items-center rounded-md px-1.5 py-px text-[11px] font-medium font-mono flex-shrink-0 ${
          undone ? "bg-muted/40 text-muted-foreground" : success ? "bg-emerald-500/8 dark:bg-emerald-400/10 text-emerald-700 dark:text-emerald-300" : "bg-red-500/8 dark:bg-red-400/10 text-red-700 dark:text-red-300"
        }`}>
          {toolName}
        </span>

        {/* 状态标签 */}
        <span className="text-[10px] text-muted-foreground/70">
          {statusLabel}
        </span>

        {undoError && (
          <span className="text-[10px] truncate min-w-0" style={{ color: "var(--em-error)" }}>
            回滚失败: {undoError}
          </span>
        )}

        {/* 右侧 */}
        <span className="ml-auto flex items-center gap-1.5 flex-shrink-0 pl-2">
          {undoable && !undone && success && (hasChanges !== false) && (
            <button
              type="button"
              className="inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[10px] font-medium text-muted-foreground hover:text-foreground hover:bg-muted/50 transition-colors cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed"
              disabled={loading}
              onClick={handleUndo}
            >
              {loading ? (
                <Loader2 className="h-3 w-3 animate-spin" />
              ) : (
                <Undo2 className="h-2.5 w-2.5" />
              )}
              撤销
            </button>
          )}
          {undone && (
            <span className="text-[10px] text-muted-foreground flex items-center gap-1">
              <Undo2 className="h-3 w-3" />
              已撤销
            </span>
          )}
          <ChevronRight className="h-3 w-3 text-muted-foreground/50" />
        </span>
      </div>
    </div>
  );
}
