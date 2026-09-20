"use client";

import { useCallback, useMemo, useState } from "react";
import dynamic from "next/dynamic";
import { ArrowLeft, Download, History, RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { buildWordFileUrl, downloadFile } from "@/lib/api";
import { useSessionStore } from "@/stores/session-store";
import { useWordStore } from "@/stores/word-store";

const WordSnapshotView = dynamic(
  () => import("./WordSnapshotView").then((module) => ({ default: module.WordSnapshotView })),
  {
    ssr: false,
    loading: () => (
      <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
        加载文档快照...
      </div>
    ),
  }
);

export function WordFullView() {
  const fullViewPath = useWordStore((state) => state.fullViewPath);
  const closeFullView = useWordStore((state) => state.closeFullView);
  const openHistory = useWordStore((state) => state.openHistory);
  const triggerRefresh = useWordStore((state) => state.triggerRefresh);
  const activeSessionId = useSessionStore((state) => state.activeSessionId);
  const activeWorkspaceId = useSessionStore(
    (state) => state.sessions.find((item) => item.id === state.activeSessionId)?.workspaceId ?? null,
  );
  const [refreshKey, setRefreshKey] = useState(0);
  const [actionError, setActionError] = useState<{ scope: string; message: string } | null>(null);

  const fileUrl = useMemo(() => {
    if (!fullViewPath) return "";
    return buildWordFileUrl(fullViewPath, activeSessionId, activeWorkspaceId);
  }, [activeSessionId, activeWorkspaceId, fullViewPath]);

  const fileName = useMemo(() => {
    if (!fullViewPath) return "";
    return fullViewPath.split("/").pop() || fullViewPath;
  }, [fullViewPath]);

  const viewScope = `${activeWorkspaceId ?? activeSessionId ?? ""}:${fullViewPath ?? ""}`;
  const visibleActionError = actionError?.scope === viewScope ? actionError.message : null;

  const handleRefresh = useCallback(() => {
    setActionError(null);
    triggerRefresh();
    setRefreshKey((value) => value + 1);
  }, [triggerRefresh]);

  const handleDownload = useCallback(() => {
    if (!fullViewPath) return;

    setActionError(null);
    void downloadFile(fullViewPath, fileName || undefined, activeSessionId, activeWorkspaceId).catch(
      (err: unknown) => {
        console.error("Error downloading Word file:", err);
        setActionError({
          scope: viewScope,
          message: err instanceof Error ? err.message : "下载失败，请重试",
        });
      }
    );
  }, [activeSessionId, activeWorkspaceId, fileName, fullViewPath, viewScope]);

  const handleOpenHistory = useCallback(() => {
    if (!fullViewPath) return;
    openHistory(fullViewPath);
    closeFullView();
  }, [closeFullView, fullViewPath, openHistory]);

  if (!fullViewPath) return null;

  return (
    <div className="fixed inset-0 z-50 flex flex-col bg-background">
      <div className="em-surface-header flex items-center gap-3 border-b border-border shrink-0">
        <Button variant="ghost" size="sm" onClick={closeFullView}>
          <ArrowLeft className="mr-1 h-4 w-4" />
          返回
        </Button>
        <span className="flex-1 truncate text-sm font-medium">{fileName}</span>
        <Button variant="ghost" size="icon" onClick={handleRefresh} title="刷新">
          <RefreshCw className="h-4 w-4" />
        </Button>
        <Button variant="ghost" size="icon" onClick={handleDownload} title="下载">
          <Download className="h-4 w-4" />
        </Button>
        <Button variant="ghost" size="icon" onClick={handleOpenHistory} title="历史">
          <History className="h-4 w-4" />
        </Button>
      </div>

      <div className="em-surface-note border-b border-border px-4 py-2">
        <p className="text-xs text-muted-foreground">
          只读快照预览（正文 + 表格）：不会写回文档；内容由 Agent 写入，精调版式请下载后用 Word 打开。
        </p>
        {visibleActionError && <p className="mt-1 text-xs text-destructive">{visibleActionError}</p>}
      </div>

      <div className="flex-1 overflow-hidden">
        <WordSnapshotView key={`fullview-${fileUrl}-${refreshKey}`} fileUrl={fileUrl} />
      </div>
    </div>
  );
}
