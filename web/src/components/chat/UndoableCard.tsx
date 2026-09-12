"use client";

import { useState } from "react";
import { Undo2, CheckCircle2, XCircle, Loader2 } from "lucide-react";

import { undoApproval } from "@/lib/api";
import { useSessionStore } from "@/stores/session-store";
import { toolActionTitle } from "@/lib/tool-labels";

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

  const statusLabel = undone ? "已回滚" : success ? "已执行" : "已拒绝";
  const title = toolActionTitle(toolName);

  return (
    <div className="my-1.5 flex items-center gap-2.5 rounded-xl border border-[var(--em-hairline)] bg-background px-3 py-2 text-sm">
      {success && !undone ? (
        <CheckCircle2 className="h-4 w-4 text-[var(--em-primary)] flex-shrink-0" />
      ) : undone ? (
        <Undo2 className="h-4 w-4 text-muted-foreground flex-shrink-0" />
      ) : (
        <XCircle className="h-4 w-4 text-red-500 flex-shrink-0" />
      )}
      <div className="min-w-0 flex-1">
        <span className="text-[13px] font-medium">{title}</span>
        <span className="ml-2 text-[11px] text-muted-foreground">{statusLabel}</span>
        {undoError && (
          <span className="ml-2 text-[11px] text-red-500">回滚失败: {undoError}</span>
        )}
      </div>
      {undoable && !undone && success && hasChanges !== false && (
        <button
          type="button"
          className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-[12px] font-medium text-muted-foreground hover:text-foreground hover:bg-muted/50"
          disabled={loading}
          onClick={handleUndo}
        >
          {loading ? <Loader2 className="h-3 w-3 animate-spin" /> : <Undo2 className="h-3 w-3" />}
          撤销
        </button>
      )}
    </div>
  );
}
