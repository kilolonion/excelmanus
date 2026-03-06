"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import dynamic from "next/dynamic";
import { ArrowLeft, Download, RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { buildWordFileUrl, downloadFile } from "@/lib/api";
import { useSessionStore } from "@/stores/session-store";
import { useWordStore } from "@/stores/word-store";

const UniverDoc = dynamic(
  () => import("./UniverDoc").then((module) => ({ default: module.UniverDoc })),
  {
    ssr: false,
    loading: () => (
      <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
        加载文档引擎...
      </div>
    ),
  }
);

export function WordFullView() {
  const fullViewPath = useWordStore((state) => state.fullViewPath);
  const closeFullView = useWordStore((state) => state.closeFullView);
  const triggerRefresh = useWordStore((state) => state.triggerRefresh);
  const activeSessionId = useSessionStore((state) => state.activeSessionId);
  const [refreshKey, setRefreshKey] = useState(0);
  const [actionError, setActionError] = useState<string | null>(null);

  const fileUrl = useMemo(() => {
    if (!fullViewPath) return "";
    return buildWordFileUrl(fullViewPath, activeSessionId);
  }, [activeSessionId, fullViewPath]);

  const fileName = useMemo(() => {
    if (!fullViewPath) return "";
    return fullViewPath.split("/").pop() || fullViewPath;
  }, [fullViewPath]);

  useEffect(() => {
    setActionError(null);
  }, [activeSessionId, fullViewPath]);

  const handleRefresh = useCallback(() => {
    setActionError(null);
    triggerRefresh();
    setRefreshKey((value) => value + 1);
  }, [triggerRefresh]);

  const handleDownload = useCallback(() => {
    if (!fullViewPath) return;

    setActionError(null);
    void downloadFile(fullViewPath, fileName || undefined, activeSessionId ?? undefined).catch(
      (err: unknown) => {
        console.error("Error downloading Word file:", err);
        setActionError(err instanceof Error ? err.message : "下载失败，请重试");
      }
    );
  }, [activeSessionId, fileName, fullViewPath]);

  if (!fullViewPath) return null;

  return (
    <div className="fixed inset-0 z-50 flex flex-col bg-background">
      <div className="flex items-center gap-3 border-b border-border px-4 py-2.5 shrink-0">
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
      </div>

      <div className="border-b border-border bg-muted/30 px-4 py-2">
        <p className="text-xs text-muted-foreground">
          预览模式：这里的输入不会自动保存到文档，请以刷新后的后端结果为准。
        </p>
        {actionError && <p className="mt-1 text-xs text-destructive">{actionError}</p>}
      </div>

      <div className="flex-1 overflow-hidden">
        <UniverDoc key={`fullview-${fileUrl}-${refreshKey}`} fileUrl={fileUrl} />
      </div>
    </div>
  );
}
