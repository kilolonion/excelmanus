"use client";

import { useEffect, useState, useCallback } from "react";
import { Undo2, CheckCircle2, XCircle, Loader2, History, RefreshCw } from "lucide-react";
import { fetchApprovals, undoApproval, type ApprovalRecord } from "@/lib/api";
import { useSessionStore } from "@/stores/session-store";
import {
  OverlayCard,
  OverlayCardBody,
  OverlayCardHeader,
} from "@/components/ui/overlay-card";

interface UndoPanelProps {
  open: boolean;
  onClose: () => void;
}

export function UndoPanel({ open, onClose }: UndoPanelProps) {
  const activeSessionId = useSessionStore((s) => s.activeSessionId);
  const [records, setRecords] = useState<ApprovalRecord[]>([]);
  const [loading, setLoading] = useState(false);
  const [undoing, setUndoing] = useState<string | null>(null);
  const [optimisticUndoneIds, setOptimisticUndoneIds] = useState<Set<string>>(new Set());
  const [undoResults, setUndoResults] = useState<Record<string, { ok: boolean; msg: string }>>({});

  const loadRecords = useCallback(async () => {
    setLoading(true);
    try {
      const data = await fetchApprovals({
        limit: 20,
        sessionId: activeSessionId ?? undefined,
      });
      setRecords(data);
    } catch {
      // 静默忽略
    } finally {
      setLoading(false);
    }
  }, [activeSessionId]);

  useEffect(() => {
    if (open) {
      void loadRecords();
    }
  }, [open, loadRecords]);

  const handleUndo = async (id: string) => {
    if (!activeSessionId) {
      setUndoResults((prev) => ({
        ...prev,
        [id]: { ok: false, msg: "缺少会话上下文，无法撤销。" },
      }));
      return;
    }

    setUndoing(id);
    setOptimisticUndoneIds((prev) => {
      const next = new Set(prev);
      next.add(id);
      return next;
    });
    setUndoResults((prev) => {
      const next = { ...prev };
      delete next[id];
      return next;
    });

    try {
      const res = await undoApproval(id, activeSessionId);
      const ok = res.status === "ok";
      setUndoResults((prev) => ({ ...prev, [id]: { ok, msg: res.message } }));
      if (ok) {
        void loadRecords();
      } else {
        setOptimisticUndoneIds((prev) => {
          const next = new Set(prev);
          next.delete(id);
          return next;
        });
      }
    } catch (err) {
      setOptimisticUndoneIds((prev) => {
        const next = new Set(prev);
        next.delete(id);
        return next;
      });
      setUndoResults((prev) => ({
        ...prev,
        [id]: { ok: false, msg: (err as Error).message },
      }));
    } finally {
      setUndoing(null);
    }
  };

  return (
    <OverlayCard open={open} onOpenChange={(v) => !v && onClose()} size="lg" tone="primary">
      <OverlayCardHeader
        icon={<History className="h-5 w-5" />}
        title="操作历史"
        description="查看并撤销本会话已执行的工具操作"
        actions={
          <button
            type="button"
            onClick={() => void loadRecords()}
            disabled={loading}
            className="text-muted-foreground/50 hover:text-foreground transition-colors p-2 sm:p-1.5 rounded-xl hover:bg-muted/80 disabled:opacity-40"
            title="刷新"
          >
            <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />
          </button>
        }
        onClose={onClose}
      />

      <OverlayCardBody className="pb-5">
        {loading && records.length === 0 ? (
          <div className="flex items-center justify-center py-10 text-sm text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin mr-2" />
            加载中...
          </div>
        ) : records.length === 0 ? (
          <div className="text-center py-10 text-sm text-muted-foreground">
            没有操作记录
          </div>
        ) : (
          <div className="space-y-2">
            {records.map((rec) => {
              const result = undoResults[rec.id];
              const isUndoing = undoing === rec.id;
              const isOptimisticallyUndone = optimisticUndoneIds.has(rec.id);
              const isUndone = isOptimisticallyUndone || result?.ok === true;
              return (
                <div
                  key={rec.id}
                  className="flex items-center gap-3 px-3 py-2.5 rounded-2xl border border-border/60 text-sm bg-background/40"
                >
                  {rec.execution_status === "success" ? (
                    <CheckCircle2
                      className="h-4 w-4 flex-shrink-0"
                      style={{ color: "var(--em-primary)" }}
                    />
                  ) : (
                    <XCircle
                      className="h-4 w-4 flex-shrink-0"
                      style={{ color: "var(--em-error)" }}
                    />
                  )}
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="font-mono text-xs truncate">
                        {rec.tool_name}
                      </span>
                      <span className="text-[10px] text-muted-foreground">
                        {formatTime(rec.applied_at_utc)}
                      </span>
                    </div>
                    {rec.result_preview && (
                      <p className="text-xs text-muted-foreground truncate mt-0.5">
                        {rec.result_preview}
                      </p>
                    )}
                    {result && !result.ok && (
                      <p className="text-xs mt-0.5" style={{ color: "var(--em-error)" }}>
                        {result.msg}
                      </p>
                    )}
                  </div>
                  {rec.undoable && rec.execution_status === "success" && !isUndone && (
                    <button
                      type="button"
                      className="inline-flex items-center gap-1 h-8 px-2.5 rounded-xl text-xs font-medium border border-border/70 text-muted-foreground hover:text-foreground hover:bg-muted/50 transition-colors flex-shrink-0 disabled:opacity-40"
                      disabled={isUndoing}
                      onClick={() => handleUndo(rec.id)}
                    >
                      {isUndoing ? (
                        <Loader2 className="h-3 w-3 animate-spin" />
                      ) : (
                        <Undo2 className="h-3 w-3" />
                      )}
                      撤销
                    </button>
                  )}
                  {isUndone && (
                    <span className="text-xs text-muted-foreground flex items-center gap-1 flex-shrink-0">
                      <Undo2 className="h-3 w-3" />
                      {isUndoing ? "撤销中..." : "已撤销"}
                    </span>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </OverlayCardBody>
    </OverlayCard>
  );
}

function formatTime(utcStr: string): string {
  if (!utcStr) return "";
  try {
    const d = new Date(utcStr);
    return d.toLocaleTimeString(undefined, {
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return utcStr.slice(11, 16);
  }
}
