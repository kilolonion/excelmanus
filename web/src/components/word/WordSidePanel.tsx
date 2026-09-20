"use client";

import { useCallback, useMemo, useState } from "react";
import dynamic from "next/dynamic";
import { motion } from "framer-motion";
import { Download, FileText, History, Maximize2, RefreshCw, X } from "lucide-react";
import { useShallow } from "zustand/react/shallow";
import { RevisionTimelinePanel } from "@/components/chat/CheckpointTimeline";
import { buildWordFileUrl, downloadFile } from "@/lib/api";
import { displayFileName } from "@/lib/file-identity";
import { panelSlideVariants } from "@/lib/sidebar-motion";
import { useSessionStore } from "@/stores/session-store";
import { useWordStore } from "@/stores/word-store";
import { useIsMobile } from "@/hooks/use-mobile";

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

export function WordSidePanel() {
  const isMobile = useIsMobile();
  const { panelOpen, panelTab, setPanelTab, activeDocPath, closePanel, openFullView, triggerRefresh } = useWordStore(
    useShallow((state) => ({
      panelOpen: state.panelOpen,
      panelTab: state.panelTab,
      setPanelTab: state.setPanelTab,
      activeDocPath: state.activeDocPath,
      closePanel: state.closePanel,
      openFullView: state.openFullView,
      triggerRefresh: state.triggerRefresh,
    }))
  );
  const activeSessionId = useSessionStore((state) => state.activeSessionId);
  const activeWorkspaceId = useSessionStore((state) => state.sessions.find((item) => item.id === state.activeSessionId)?.workspaceId ?? null);
  const [refreshKey, setRefreshKey] = useState(0);
  const [actionError, setActionError] = useState<{ scope: string; message: string } | null>(null);

  const fileUrl = useMemo(() => {
    if (!activeDocPath) return "";
    return buildWordFileUrl(activeDocPath, activeSessionId, activeWorkspaceId);
  }, [activeDocPath, activeSessionId, activeWorkspaceId]);

  const fileName = useMemo(() => {
    if (!activeDocPath) return "";
    return displayFileName(activeDocPath) || activeDocPath;
  }, [activeDocPath]);

  const viewScope = `${activeWorkspaceId ?? activeSessionId ?? ""}:${activeDocPath ?? ""}`;
  const visibleActionError = actionError?.scope === viewScope ? actionError.message : null;

  const handleRefresh = useCallback(() => {
    setActionError(null);
    triggerRefresh();
    setRefreshKey((value) => value + 1);
  }, [triggerRefresh]);

  const handleDownload = useCallback(() => {
    if (!activeDocPath) return;

    setActionError(null);
    void downloadFile(activeDocPath, fileName || undefined, activeSessionId, activeWorkspaceId).catch(
      (err: unknown) => {
        console.error("Error downloading Word file:", err);
        setActionError({
          scope: viewScope,
          message: err instanceof Error ? err.message : "下载失败，请重试",
        });
      }
    );
  }, [activeDocPath, activeSessionId, activeWorkspaceId, fileName, viewScope]);

  if (!panelOpen || !activeDocPath) return null;

  return (
    <motion.div
      key="word-side-panel"
      data-coach-id="coach-word-panel"
      className={`em-word-panel flex h-full flex-shrink-0 flex-col border-l border-border bg-background ${isMobile ? "fixed inset-0 z-50" : ""}`}
      style={isMobile ? undefined : { width: "min(480px, 50vw)", minWidth: 0 }}
      variants={isMobile ? { hidden: { y: "100%", opacity: 0 }, visible: { y: 0, opacity: 1 }, exit: { y: "100%", opacity: 0 } } : panelSlideVariants}
      initial="hidden"
      animate="visible"
      exit="hidden"
    >
      <div className="flex items-center gap-2 border-b border-[var(--em-line)] bg-[var(--em-panel-soft)] px-3 py-2.5 shrink-0">
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

      <div className="flex border-b border-[var(--em-line)] bg-[var(--em-panel)] px-1 shrink-0">
        <button
          type="button"
          onClick={() => setPanelTab("doc")}
          className={`flex items-center gap-1 px-3 py-1.5 text-xs font-medium border-b-2 transition-colors ${
            panelTab === "doc"
              ? "border-[var(--em-primary)] text-foreground"
              : "border-transparent text-muted-foreground hover:text-foreground"
          }`}
        >
          <FileText className="h-3 w-3" />
          文档
        </button>
        <button
          type="button"
          onClick={() => setPanelTab("history")}
          className={`flex items-center gap-1 px-3 py-1.5 text-xs font-medium border-b-2 transition-colors ${
            panelTab === "history"
              ? "border-[var(--em-primary)] text-foreground"
              : "border-transparent text-muted-foreground hover:text-foreground"
          }`}
        >
          <History className="h-3 w-3" />
          历史
        </button>
      </div>

      {panelTab === "doc" && (
        <div className="border-b border-border bg-muted/30 px-3 py-2">
          <p className="text-[11px] leading-4 text-muted-foreground">
            只读快照预览（正文 + 表格）：不会写回文档；内容由 Agent 写入，精调版式请下载后用 Word 打开。
          </p>
          {visibleActionError && (
            <p className="mt-1 text-[11px] leading-4 text-destructive">{visibleActionError}</p>
          )}
        </div>
      )}

      <div className={`flex-1 overflow-hidden ${panelTab === "doc" ? "" : "hidden"}`}>
        <WordSnapshotView key={`${fileUrl}-${refreshKey}`} fileUrl={fileUrl} />
      </div>
      <div className={`flex-1 min-h-0 overflow-hidden ${panelTab === "history" ? "" : "hidden"}`}>
        <div className="h-full flex flex-col">
          <div className="shrink-0 px-3 pt-3 pb-2 border-b border-border/70">
            <p className="text-[11px] leading-4 text-muted-foreground">
              整份文档的写入快照。恢复会覆盖当前文件。
            </p>
          </div>
          <div className="flex-1 min-h-0">
            <RevisionTimelinePanel filePath={activeDocPath} workspaceId={activeWorkspaceId} active={panelOpen && panelTab === "history"} />
          </div>
        </div>
      </div>
    </motion.div>
  );
}
