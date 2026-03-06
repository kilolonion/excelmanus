"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import dynamic from "next/dynamic";
import { motion } from "framer-motion";
import { Download, FileText, Maximize2, RefreshCw, X } from "lucide-react";
import { useShallow } from "zustand/react/shallow";
import { buildWordFileUrl, downloadFile } from "@/lib/api";
import { panelSlideVariants } from "@/lib/sidebar-motion";
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

export function WordSidePanel() {
  const { panelOpen, activeDocPath, closePanel, openFullView, triggerRefresh } = useWordStore(
    useShallow((state) => ({
      panelOpen: state.panelOpen,
      activeDocPath: state.activeDocPath,
      closePanel: state.closePanel,
      openFullView: state.openFullView,
      triggerRefresh: state.triggerRefresh,
    }))
  );
  const activeSessionId = useSessionStore((state) => state.activeSessionId);
  const [refreshKey, setRefreshKey] = useState(0);
  const [actionError, setActionError] = useState<string | null>(null);

  const fileUrl = useMemo(() => {
    if (!activeDocPath) return "";
    return buildWordFileUrl(activeDocPath, activeSessionId);
  }, [activeDocPath, activeSessionId]);

  const fileName = useMemo(() => {
    if (!activeDocPath) return "";
    return activeDocPath.split("/").pop() || activeDocPath;
  }, [activeDocPath]);

  useEffect(() => {
    setActionError(null);
  }, [activeDocPath, activeSessionId]);

  const handleRefresh = useCallback(() => {
    setActionError(null);
    triggerRefresh();
    setRefreshKey((value) => value + 1);
  }, [triggerRefresh]);

  const handleDownload = useCallback(() => {
    if (!activeDocPath) return;

    setActionError(null);
    void downloadFile(activeDocPath, fileName || undefined, activeSessionId ?? undefined).catch(
      (err: unknown) => {
        console.error("Error downloading Word file:", err);
        setActionError(err instanceof Error ? err.message : "下载失败，请重试");
      }
    );
  }, [activeDocPath, activeSessionId, fileName]);

  if (!panelOpen || !activeDocPath) return null;

  return (
    <motion.div
      key="word-side-panel"
      className="flex h-full flex-col border-l border-border bg-background"
      style={{ width: 480, minWidth: 360, maxWidth: "50vw" }}
      variants={panelSlideVariants}
      initial="hidden"
      animate="visible"
      exit="hidden"
    >
      <div className="flex items-center gap-2 border-b border-border px-3 py-2 shrink-0">
        <FileText className="h-4 w-4 shrink-0 text-blue-500" />
        <span className="flex-1 truncate text-sm font-medium" title={activeDocPath}>
          {fileName}
        </span>

        <button
          type="button"
          onClick={handleRefresh}
          className="rounded p-1 transition-colors hover:bg-muted"
          title="刷新"
        >
          <RefreshCw className="h-3.5 w-3.5" />
        </button>
        <button
          type="button"
          onClick={() => openFullView()}
          className="rounded p-1 transition-colors hover:bg-muted"
          title="全屏"
        >
          <Maximize2 className="h-3.5 w-3.5" />
        </button>
        <button
          type="button"
          onClick={handleDownload}
          className="rounded p-1 transition-colors hover:bg-muted"
          title="下载"
        >
          <Download className="h-3.5 w-3.5" />
        </button>
        <button
          type="button"
          onClick={closePanel}
          className="rounded p-1 transition-colors hover:bg-muted"
          title="关闭"
        >
          <X className="h-3.5 w-3.5" />
        </button>
      </div>

      <div className="border-b border-border bg-muted/30 px-3 py-2">
        <p className="text-[11px] leading-4 text-muted-foreground">
          预览模式：这里的改动不会自动保存到文档，刷新会重新加载当前快照。
        </p>
        {actionError && (
          <p className="mt-1 text-[11px] leading-4 text-destructive">{actionError}</p>
        )}
      </div>

      <div className="flex-1 overflow-hidden">
        <UniverDoc key={`${fileUrl}-${refreshKey}`} fileUrl={fileUrl} />
      </div>
    </motion.div>
  );
}
