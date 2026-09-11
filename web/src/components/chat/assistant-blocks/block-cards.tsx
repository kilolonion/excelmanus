"use client";

import {
  FolderOpen,
  Copy,
  Brain,
  Download,
  ChevronDown,
  ChevronRight,
  Upload,
} from "lucide-react";
import { UndoableCard } from "../UndoableCard";
import { ApplyPanel } from "../ApplyPanel";
import { useChatStore } from "@/stores/chat-store";
import { useSessionStore } from "@/stores/session-store";
import { useUIStore } from "@/stores/ui-store";
import { buildApiUrl, downloadFile } from "@/lib/api";
import { useAuthConfigStore } from "@/stores/auth-config-store";
import type { AssistantBlock } from "@/lib/types";
import { useCallback, useState } from "react";
import { motion } from "framer-motion";

export function SaveResultCard({ path }: { path: string }) {
  const filename = path.split("/").pop() || path;
  const dir = path.substring(0, path.length - filename.length);
  const deployMode = useAuthConfigStore((s) => s.deployMode);
  const canReveal = deployMode === "standalone";

  const handleReveal = useCallback(() => {
    if (!canReveal) return;
    fetch(buildApiUrl("/files/reveal"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path }),
    }).catch(() => {});
  }, [path, canReveal]);

  const handleCopyPath = useCallback(() => {
    navigator.clipboard.writeText(path).catch(() => {});
  }, [path]);

  return (
    <div className="my-2">
      <p className="text-sm text-foreground mb-2">对话已保存至：</p>
      <button
        type="button"
        onClick={canReveal ? handleReveal : handleCopyPath}
        className="group flex items-start gap-2.5 w-full rounded-lg px-3 py-2.5 text-left cursor-pointer transition-all border border-[var(--em-primary-alpha-15)] bg-[var(--em-primary-alpha-06)] hover:bg-[var(--em-primary-alpha-15)] hover:border-[var(--em-primary-alpha-20)]"
        title={canReveal ? "点击在文件管理器中打开" : "点击复制路径"}
      >
        {canReveal ? (
          <FolderOpen className="h-4 w-4 mt-0.5 flex-shrink-0 text-[var(--em-primary)] group-hover:scale-110 transition-transform" />
        ) : (
          <Copy className="h-4 w-4 mt-0.5 flex-shrink-0 text-[var(--em-primary)] group-hover:scale-110 transition-transform" />
        )}
        <div className="min-w-0 flex-1">
          <span className="block text-sm font-medium text-[var(--em-primary)] break-all">
            {filename}
          </span>
          <span className="block text-xs text-muted-foreground break-all mt-0.5">
            {dir}
          </span>
        </div>
        <span className="text-[10px] text-muted-foreground self-center flex-shrink-0 opacity-0 group-hover:opacity-100 transition-opacity">
          {canReveal ? "打开文件夹" : "复制路径"}
        </span>
      </button>
    </div>
  );
}

export function ApprovalActionBlock({
  block,
  blockIndex,
  messageId,
}: {
  block: Extract<AssistantBlock, { type: "approval_action" }>;
  blockIndex: number;
  messageId: string;
}) {
  const updateAssistantMessage = useChatStore((s) => s.updateAssistantMessage);

  const handleUndone = (_approvalId: string, error?: string) => {
    updateAssistantMessage(messageId, (m) => {
      const blocks = [...m.blocks];
      blocks[blockIndex] = {
        ...blocks[blockIndex],
        undone: !error,
        undoError: error,
      } as AssistantBlock;
      return { ...m, blocks };
    });
  };

  return (
    <UndoableCard
      approvalId={block.approvalId}
      toolName={block.toolName}
      success={block.success}
      undoable={block.undoable}
      hasChanges={block.hasChanges}
      undone={block.undone}
      undoError={block.undoError}
      onUndone={handleUndone}
    />
  );
}

const TRIGGER_LABELS: Record<string, string> = {
  periodic: "周期提取",
  pre_compaction: "压缩前提取",
  session_end: "会话结束提取",
};

const CATEGORY_COLORS: Record<string, string> = {
  file_pattern: "bg-blue-500/15 text-blue-700 dark:text-blue-400",
  user_pref: "bg-purple-500/15 text-purple-700 dark:text-purple-400",
  error_solution: "bg-amber-500/15 text-amber-700 dark:text-amber-400",
  general: "bg-gray-500/15 text-gray-700 dark:text-gray-400",
};

export function MemoryExtractedBlock({
  block,
}: {
  block: Extract<AssistantBlock, { type: "memory_extracted" }>;
}) {
  const [expanded, setExpanded] = useState(false);
  const openSettings = useUIStore((s) => s.openSettings);

  const preview = block.entries.slice(0, 3);
  const hasMore = block.entries.length > 3;

  return (
    <motion.div
      className="my-2 rounded-lg border border-emerald-500/30 bg-emerald-500/5 overflow-hidden"
      initial={{ opacity: 0, x: -16 }}
      animate={{ opacity: 1, x: 0 }}
      transition={{ duration: 0.3, ease: [0.4, 0, 0.2, 1] }}
    >
      <div className="flex items-center gap-2 px-3 py-2">
        <Brain className="h-4 w-4 text-emerald-600 dark:text-emerald-400 flex-shrink-0" />
        <span className="text-sm font-medium text-emerald-700 dark:text-emerald-300">
          已提取 {block.count} 条记忆
        </span>
        <span className="text-[10px] text-emerald-600/60 dark:text-emerald-400/60">
          {TRIGGER_LABELS[block.trigger] || block.trigger}
        </span>
        <div className="ml-auto flex items-center gap-1.5">
          {hasMore && (
            <button
              onClick={() => setExpanded(!expanded)}
              className="text-[10px] text-emerald-600 dark:text-emerald-400 hover:underline"
            >
              {expanded ? "收起" : `展开全部 (${block.count})`}
            </button>
          )}
          <button
            onClick={() => openSettings("memory")}
            className="text-[10px] text-emerald-600 dark:text-emerald-400 hover:underline font-medium"
          >
            管理记忆
          </button>
        </div>
      </div>
      <div className="px-3 pb-2 space-y-1">
        {(expanded ? block.entries : preview).map((entry) => (
          <div
            key={entry.id}
            className="flex items-start gap-2 text-xs text-foreground/80"
          >
            <span
              className={`mt-0.5 flex-shrink-0 px-1.5 py-0.5 rounded text-[10px] font-medium ${
                CATEGORY_COLORS[entry.category] || CATEGORY_COLORS.general
              }`}
            >
              {entry.category}
            </span>
            <span className="line-clamp-2">{entry.content}</span>
          </div>
        ))}
      </div>
    </motion.div>
  );
}

export function FileDownloadCard({ block }: { block: Extract<AssistantBlock, { type: "file_download" }> }) {
  const activeSessionId = useSessionStore((s) => s.activeSessionId);
  const handleDownload = useCallback(() => {
    downloadFile(block.filePath, block.filename, activeSessionId ?? undefined).catch(() => {});
  }, [block.filePath, block.filename, activeSessionId]);

  return (
    <button
      type="button"
      onClick={handleDownload}
      className="group/card flex items-center gap-0 w-full my-1 rounded-lg text-left cursor-pointer transition-all duration-200 border border-[var(--em-primary-alpha-15)] bg-[var(--em-primary-alpha-06)] hover:bg-[var(--em-primary-alpha-15)] hover:shadow-sm overflow-hidden"
      title={`下载 ${block.filename}`}
    >
      {/* 左侧强调条 */}
      <div className="self-stretch w-[3px] flex-shrink-0 rounded-l-lg" style={{ backgroundColor: "var(--em-primary)" }} />

      <div className="flex items-center gap-2 flex-1 min-w-0 px-2.5 py-1.5">
        {/* 圆形图标徽章 */}
        <span className="flex items-center justify-center h-5 w-5 rounded-full flex-shrink-0 bg-[var(--em-primary-alpha-15)]">
          <Download className="h-3 w-3 text-[var(--em-primary)]" />
        </span>

        {/* 文件名胶囊 */}
        <span className="inline-flex items-center rounded-md px-1.5 py-px text-[11px] font-medium flex-shrink-0 bg-[var(--em-primary-alpha-06)] text-[var(--em-primary)]">
          {block.filename}
        </span>

        {/* 描述预览 */}
        {block.description && (
          <span className="text-[10px] text-muted-foreground/70 truncate min-w-0">
            {block.description}
          </span>
        )}

        {/* 右侧 */}
        <span className="ml-auto flex items-center gap-1.5 flex-shrink-0 pl-2">
          <span className="text-[10px] text-muted-foreground opacity-0 group-hover/card:opacity-100 transition-opacity">
            点击下载
          </span>
          <ChevronDown className="h-3 w-3 text-muted-foreground/50 group-hover/card:text-muted-foreground/70 transition-colors" />
        </span>
      </div>
    </button>
  );
}

export function StagingHintCard({ pendingCount, files }: { pendingCount: number; files: string[] }) {
  const [panelOpen, setPanelOpen] = useState(false);
  const fileName = (p: string) => p.split("/").pop() || p;

  return (
    <>
      <button
        onClick={() => setPanelOpen(true)}
        className="group/card flex items-center gap-0 w-full my-1 rounded-lg border border-blue-200 dark:border-blue-800 bg-blue-50/50 dark:bg-blue-950/20 text-sm hover:bg-blue-100/60 dark:hover:bg-blue-900/30 hover:shadow-sm transition-all duration-200 overflow-hidden cursor-pointer"
      >
        {/* 左侧强调条 */}
        <div className="self-stretch w-[3px] flex-shrink-0 rounded-l-lg bg-blue-500" />

        <div className="flex items-center gap-2 flex-1 min-w-0 px-2.5 py-1.5">
          {/* 圆形图标徽章 */}
          <span className="flex items-center justify-center h-5 w-5 rounded-full flex-shrink-0 bg-blue-500/10 dark:bg-blue-400/15">
            <Upload className="h-3 w-3 text-blue-600 dark:text-blue-400" />
          </span>

          {/* 数量胶囊 */}
          <span className="inline-flex items-center rounded-md px-1.5 py-px text-[11px] font-medium flex-shrink-0 bg-blue-500/8 dark:bg-blue-400/10 text-blue-700 dark:text-blue-300">
            {pendingCount} 个文件待应用
          </span>

          {/* 文件名预览 */}
          <span className="text-[10px] text-blue-600/60 dark:text-blue-400/50 truncate min-w-0">
            {files.slice(0, 2).map(fileName).join("、")}
            {files.length > 2 && ` 等${files.length}个`}
          </span>

          {/* 右侧 */}
          <span className="ml-auto flex items-center gap-1.5 flex-shrink-0 pl-2">
            <span className="text-[11px] font-medium text-blue-600 dark:text-blue-400">
              查看
            </span>
            <ChevronRight className="h-3 w-3 text-blue-400/50 group-hover/card:translate-x-0.5 transition-transform" />
          </span>
        </div>
      </button>
      <ApplyPanel open={panelOpen} onOpenChange={setPanelOpen} />
    </>
  );
}
