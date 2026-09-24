"use client";

import { useEffect, useState } from "react";
import { AlertCircle, CornerDownRight, ListOrdered, Loader2, Play, Trash2 } from "lucide-react";
import { apiGet, apiPost } from "@/lib/api";
import { sendContinuation } from "@/lib/chat-actions";
import { useDispatchStore, DISPATCH_LABELS, DISPATCH_STATUS } from "@/stores/dispatch-store";
import { useSessionStore } from "@/stores/session-store";
import { useChatStore } from "@/stores/chat-store";
import type { DispatchReceipt } from "@/lib/types";

export function DispatchQueue() {
  const sessionId = useSessionStore((s) => s.activeSessionId);
  const streaming = useChatStore((s) => s.isStreaming);
  const receipts = useDispatchStore((s) => sessionId ? s.sessions[sessionId] : undefined);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  useEffect(() => {
    if (!sessionId) return;
    const controller = new AbortController();
    void apiGet<{ dispatches: DispatchReceipt[] }>(`/chat/${encodeURIComponent(sessionId)}/dispatches`, { signal: controller.signal })
      .then((result) => {
        if (!controller.signal.aborted) for (const row of result.dispatches ?? []) useDispatchStore.getState().upsert(sessionId, row);
      }).catch(() => {});
    return () => controller.abort();
  }, [sessionId, streaming]);
  const pending = Object.values(receipts ?? {}).filter((r) => !r.hidden && (["queued", "interrupt_pending"].includes(r.status)
    || (["failed", "interrupted"].includes(r.status) && r.message_recorded === false)));
  if (!pending.length || !sessionId) return null;
  const cancel = async (row: DispatchReceipt) => {
    setBusy(row.dispatch_id);
    setError("");
    try {
      const receipt = await apiPost<DispatchReceipt>(`/chat/${encodeURIComponent(sessionId)}/dispatches/${encodeURIComponent(row.dispatch_id)}/cancel`, {});
      useDispatchStore.getState().upsert(sessionId, receipt);
    } catch (e) { setError(e instanceof Error ? e.message : "撤回失败"); }
    finally { setBusy(null); }
  };
  return <div className="mx-2 max-h-36 overflow-y-auto border-b border-border px-2 py-1" aria-label="待处理消息">
    {pending.map((row) => <div key={row.dispatch_id} className="flex min-h-9 items-center gap-2 text-xs">
      {["failed", "interrupted"].includes(row.status) ? <AlertCircle className="size-3.5 shrink-0 text-red-600" /> : row.status === "interrupt_pending" ? <Loader2 className="size-3.5 shrink-0 animate-spin" /> : row.mode === "queue" ? <ListOrdered className="size-3.5 shrink-0" /> : <CornerDownRight className="size-3.5 shrink-0" />}
      <span className="min-w-0 flex-1 truncate" title={row.content}>{row.content || "图片附件"}</span>
      <span className="shrink-0 text-muted-foreground" title={row.error}>{row.status === "queued" ? DISPATCH_LABELS[row.mode] : DISPATCH_STATUS[row.status]}</span>
      {["queued", "interrupt_pending"].includes(row.status) && <button type="button" className="flex size-8 shrink-0 items-center justify-center rounded hover:bg-muted" aria-label="撤回消息" title="撤回消息" disabled={busy !== null} onClick={() => void cancel(row)}><Trash2 className="size-3.5" /></button>}
    </div>)}
    {!streaming && pending.some((r) => ["queued", "interrupt_pending"].includes(r.status)) && <button type="button" className="flex items-center gap-1 py-1 text-xs" onClick={() => void sendContinuation("/resume-queue", sessionId)}><Play className="size-3.5" />继续队列</button>}
    {error && <div role="alert" className="py-1 text-xs text-red-600">{error}</div>}
  </div>;
}
