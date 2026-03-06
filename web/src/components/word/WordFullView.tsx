"use client";

import { useCallback, useMemo, useState } from "react";
import dynamic from "next/dynamic";
import { ArrowLeft, Download, RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useWordStore } from "@/stores/word-store";
import { useSessionStore } from "@/stores/session-store";
import { buildWordFileUrl, downloadFile } from "@/lib/api";

const UniverDoc = dynamic(
  () => import("./UniverDoc").then((m) => ({ default: m.UniverDoc })),
  {
    ssr: false,
    loading: () => (
      <div className="flex items-center justify-center h-full text-sm text-muted-foreground">
        加载文档引擎...
      </div>
    ),
  }
);

export function WordFullView() {
  const fullViewPath = useWordStore((s) => s.fullViewPath);
  const closeFullView = useWordStore((s) => s.closeFullView);
  const triggerRefresh = useWordStore((s) => s.triggerRefresh);
  const activeSessionId = useSessionStore((s) => s.activeSessionId);
  const [refreshKey, setRefreshKey] = useState(0);

  const fileUrl = useMemo(() => {
    if (!fullViewPath) return "";
    return buildWordFileUrl(fullViewPath, activeSessionId);
  }, [fullViewPath, activeSessionId]);

  const handleRefresh = useCallback(() => {
    triggerRefresh();
    setRefreshKey((k) => k + 1);
  }, [triggerRefresh]);

  const handleDownload = useCallback(() => {
    if (fullViewPath) {
      downloadFile(fullViewPath, activeSessionId ?? undefined);
    }
  }, [fullViewPath, activeSessionId]);

  if (!fullViewPath) return null;

  const fileName = fullViewPath.split("/").pop() || fullViewPath;

  return (
    <div className="fixed inset-0 z-50 bg-background flex flex-col">
      {/* Header */}
      <div className="flex items-center gap-3 px-4 py-2.5 border-b border-border shrink-0">
        <Button variant="ghost" size="sm" onClick={closeFullView}>
          <ArrowLeft className="w-4 h-4 mr-1" />
          返回
        </Button>
        <span className="text-sm font-medium truncate flex-1">{fileName}</span>
        <Button variant="ghost" size="icon" onClick={handleRefresh} title="刷新">
          <RefreshCw className="w-4 h-4" />
        </Button>
        <Button variant="ghost" size="icon" onClick={handleDownload} title="下载">
          <Download className="w-4 h-4" />
        </Button>
      </div>

      {/* Doc viewer */}
      <div className="flex-1 overflow-hidden">
        <UniverDoc key={`fullview-${fileUrl}-${refreshKey}`} fileUrl={fileUrl} />
      </div>
    </div>
  );
}
