"use client";

import { useCallback, useMemo, useState } from "react";
import dynamic from "next/dynamic";
import { motion } from "framer-motion";
import { X, RefreshCw, Maximize2, Download, FileText } from "lucide-react";
import { panelSlideVariants } from "@/lib/sidebar-motion";
import { useShallow } from "zustand/react/shallow";
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

export function WordSidePanel() {
  const {
    panelOpen,
    activeDocPath,
    closePanel,
    openFullView,
    recentFiles,
    openPanel,
    removeRecentFile,
    triggerRefresh,
  } = useWordStore(
    useShallow((s) => ({
      panelOpen: s.panelOpen,
      activeDocPath: s.activeDocPath,
      closePanel: s.closePanel,
      openFullView: s.openFullView,
      recentFiles: s.recentFiles,
      openPanel: s.openPanel,
      removeRecentFile: s.removeRecentFile,
      triggerRefresh: s.triggerRefresh,
    }))
  );

  const activeSessionId = useSessionStore((s) => s.activeSessionId);
  const [refreshKey, setRefreshKey] = useState(0);

  const fileUrl = useMemo(() => {
    if (!activeDocPath) return "";
    return buildWordFileUrl(activeDocPath, activeSessionId);
  }, [activeDocPath, activeSessionId]);

  const handleRefresh = useCallback(() => {
    triggerRefresh();
    setRefreshKey((k) => k + 1);
  }, [triggerRefresh]);

  const handleDownload = useCallback(() => {
    if (activeDocPath) {
      downloadFile(activeDocPath, activeSessionId ?? undefined);
    }
  }, [activeDocPath, activeSessionId]);

  if (!panelOpen || !activeDocPath) return null;

  const fileName = activeDocPath.split("/").pop() || activeDocPath;

  return (
    <motion.div
      key="word-side-panel"
      className="h-full flex flex-col bg-background border-l border-border"
      style={{ width: 480, minWidth: 360, maxWidth: "50vw" }}
      variants={panelSlideVariants}
      initial="hidden"
      animate="visible"
      exit="hidden"
    >
      {/* Header */}
      <div className="flex items-center gap-2 px-3 py-2 border-b border-border shrink-0">
        <FileText className="w-4 h-4 text-blue-500 shrink-0" />
        <span className="text-sm font-medium truncate flex-1" title={activeDocPath}>
          {fileName}
        </span>

        <button
          onClick={handleRefresh}
          className="p-1 rounded hover:bg-muted transition-colors"
          title="刷新"
        >
          <RefreshCw className="w-3.5 h-3.5" />
        </button>
        <button
          onClick={() => openFullView()}
          className="p-1 rounded hover:bg-muted transition-colors"
          title="全屏"
        >
          <Maximize2 className="w-3.5 h-3.5" />
        </button>
        <button
          onClick={handleDownload}
          className="p-1 rounded hover:bg-muted transition-colors"
          title="下载"
        >
          <Download className="w-3.5 h-3.5" />
        </button>
        <button
          onClick={closePanel}
          className="p-1 rounded hover:bg-muted transition-colors"
          title="关闭"
        >
          <X className="w-3.5 h-3.5" />
        </button>
      </div>

      {/* Doc viewer */}
      <div className="flex-1 overflow-hidden">
        <UniverDoc key={`${fileUrl}-${refreshKey}`} fileUrl={fileUrl} />
      </div>
    </motion.div>
  );
}
