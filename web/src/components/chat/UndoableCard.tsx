"use client";

import { useState } from "react";
import { Undo2, CheckCircle2, XCircle, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
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

  const borderColor = undone
    ? "var(--em-muted)"
    : success
      ? "var(--em-primary)"
      : "var(--em-error)";

  return (
    <div
      className="my-2 flex items-center gap-3 px-3 py-2 rounded-lg border text-sm"
      style={{ borderColor }}
    >
      {success ? (
        <CheckCircle2
          className="h-4 w-4 flex-shrink-0"
          style={{ color: undone ? "var(--em-muted)" : "var(--em-primary)" }}
        />
      ) : (
        <XCircle
          className="h-4 w-4 flex-shrink-0"
          style={{ color: "var(--em-error)" }}
        />
      )}

      <div className="flex-1 min-w-0">
        <span className="font-mono text-xs">{toolName}</span>
        <span className="text-muted-foreground text-xs ml-2">
          {undone
            ? "已回滚"
            : success
              ? "已执行"
              : "已拒绝"}
        </span>
        {undoError && (
          <span className="text-xs ml-2" style={{ color: "var(--em-error)" }}>
            回滚失败: {undoError}
          </span>
        )}
      </div>

      {undoable && !undone && success && (hasChanges !== false) && (
        <Button
          size="sm"
          variant="outline"
          className="gap-1 h-7 text-xs"
          disabled={loading}
          onClick={handleUndo}
        >
          {loading ? (
            <Loader2 className="h-3 w-3 animate-spin" />
          ) : (
            <Undo2 className="h-3 w-3" />
          )}
          撤销
        </Button>
      )}

      {undone && (
        <span className="text-xs text-muted-foreground flex items-center gap-1">
          <Undo2 className="h-3 w-3" />
          已撤销
        </span>
      )}
    </div>
  );
}
