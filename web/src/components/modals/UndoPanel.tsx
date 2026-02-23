"use client";

import { useEffect, useState } from "react";
import { Undo2, CheckCircle2, XCircle, Loader2, History, RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { fetchApprovals, undoApproval, type ApprovalRecord } from "@/lib/api";
import { motion, AnimatePresence } from "framer-motion";

interface UndoPanelProps {
  open: boolean;
  onClose: () => void;
}

export function UndoPanel({ open, onClose }: UndoPanelProps) {
  const [records, setRecords] = useState<ApprovalRecord[]>([]);
  const [loading, setLoading] = useState(false);
  const [undoing, setUndoing] = useState<string | null>(null);
  const [undoResults, setUndoResults] = useState<Record<string, { ok: boolean; msg: string }>>({});

  const loadRecords = async () => {
    setLoading(true);
    try {
      const data = await fetchApprovals({ limit: 20 });
      setRecords(data);
    } catch {
      // silently ignore
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (open) {
      loadRecords();
    }
  }, [open]);

  const handleUndo = async (id: string) => {
    setUndoing(id);
    try {
      const res = await undoApproval(id);
      const ok = res.status === "ok";
      setUndoResults((prev) => ({ ...prev, [id]: { ok, msg: res.message } }));
      if (ok) {
        // Refresh list
        await loadRecords();
      }
    } catch (err) {
      setUndoResults((prev) => ({
        ...prev,
        [id]: { ok: false, msg: (err as Error).message },
      }));
    } finally {
      setUndoing(null);
    }
  };

  if (!open) return null;

  return (
    <AnimatePresence>
      <motion.div
        initial={{ y: 100, opacity: 0 }}
        animate={{ y: 0, opacity: 1 }}
        exit={{ y: 100, opacity: 0 }}
        className="fixed bottom-24 left-1/2 -translate-x-1/2 z-50 w-[560px] max-w-[90vw]"
      >
        <div className="bg-card border border-border rounded-2xl shadow-lg">
          {/* Header */}
          <div className="flex items-center justify-between px-4 pt-4 pb-2">
            <div className="flex items-center gap-2">
              <History className="h-5 w-5" style={{ color: "var(--em-primary)" }} />
              <span className="font-semibold text-sm">操作历史</span>
            </div>
            <div className="flex items-center gap-1">
              <Button
                size="sm"
                variant="ghost"
                className="h-7 w-7 p-0"
                onClick={loadRecords}
                disabled={loading}
              >
                <RefreshCw className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`} />
              </Button>
              <Button
                size="sm"
                variant="ghost"
                className="h-7 w-7 p-0"
                onClick={onClose}
              >
                ✕
              </Button>
            </div>
          </div>

          {/* Body */}
          <div className="px-4 pb-4 max-h-[400px] overflow-y-auto">
            {loading && records.length === 0 ? (
              <div className="flex items-center justify-center py-8 text-sm text-muted-foreground">
                <Loader2 className="h-4 w-4 animate-spin mr-2" />
                加载中...
              </div>
            ) : records.length === 0 ? (
              <div className="text-center py-8 text-sm text-muted-foreground">
                没有操作记录
              </div>
            ) : (
              <div className="space-y-2">
                {records.map((rec) => {
                  const result = undoResults[rec.id];
                  const isUndoing = undoing === rec.id;
                  return (
                    <div
                      key={rec.id}
                      className="flex items-center gap-3 px-3 py-2 rounded-lg border border-border text-sm"
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
                      {rec.undoable && rec.execution_status === "success" && !result?.ok && (
                        <Button
                          size="sm"
                          variant="outline"
                          className="gap-1 h-7 text-xs flex-shrink-0"
                          disabled={isUndoing}
                          onClick={() => handleUndo(rec.id)}
                        >
                          {isUndoing ? (
                            <Loader2 className="h-3 w-3 animate-spin" />
                          ) : (
                            <Undo2 className="h-3 w-3" />
                          )}
                          撤销
                        </Button>
                      )}
                      {result?.ok && (
                        <span className="text-xs text-muted-foreground flex items-center gap-1 flex-shrink-0">
                          <Undo2 className="h-3 w-3" />
                          已撤销
                        </span>
                      )}
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        </div>
      </motion.div>
    </AnimatePresence>
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
