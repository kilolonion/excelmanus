"use client";

import { LoaderCircle, RefreshCw } from "lucide-react";
import { refreshSessionMessagesFromBackend, useChatStore } from "@/stores/chat-store";

/** Visible state for history restore/revalidation instead of a silent blank chat. */
export function ChatHistoryStatus({ sessionId, empty = false }: { sessionId: string | null; empty?: boolean }) {
  const loading = useChatStore((state) => state.loadedSessionId === sessionId && state.isLoadingMessages);
  const error = useChatStore((state) => state.loadedSessionId === sessionId ? state.messageLoadError : null);
  if (!loading && !error) return null;

  const retry = () => {
    if (sessionId) void refreshSessionMessagesFromBackend(sessionId);
  };

  if (empty) {
    return (
      <div className="flex min-h-0 flex-1 items-center justify-center px-6 text-center">
        <div className="max-w-sm space-y-3 text-sm text-muted-foreground" role={error ? "alert" : "status"}>
          {loading ? (
            <div className="flex items-center justify-center gap-2">
              <LoaderCircle className="h-4 w-4 animate-spin" aria-hidden="true" />
              <span>正在加载历史消息…</span>
            </div>
          ) : null}
          {error ? (
            <>
              <p>{error}</p>
              <button type="button" onClick={retry} className="inline-flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-xs text-foreground hover:bg-muted">
                <RefreshCw className="h-3.5 w-3.5" aria-hidden="true" />重试
              </button>
            </>
          ) : null}
        </div>
      </div>
    );
  }

  return (
    <div className="flex shrink-0 items-center justify-center gap-2 px-3 py-1.5 text-[11px] text-muted-foreground" role={error ? "alert" : "status"}>
      {loading ? <LoaderCircle className="h-3.5 w-3.5 animate-spin" aria-hidden="true" /> : null}
      <span>{error ?? "正在同步历史消息…"}</span>
      {error ? <button type="button" onClick={retry} className="underline underline-offset-2 hover:text-foreground">重试</button> : null}
    </div>
  );
}
