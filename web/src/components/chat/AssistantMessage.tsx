"use client";

import {
  FileSpreadsheet,
  Repeat,
  Download,
  ChevronsUpDown,
  CheckCircle2,
  XCircle,
  Wrench,
  Layers,
} from "lucide-react";
import { CodePreviewModal, isCodeFile } from "./CodePreviewModal";
import { MessageActions } from "./MessageActions";
import { AssistantBlockRenderer } from "./assistant-blocks/AssistantBlockRenderer";
import { useChatStore } from "@/stores/chat-store";
import { useExcelStore } from "@/stores/excel-store";
import { useSessionStore } from "@/stores/session-store";
import { downloadFile, normalizeExcelPath } from "@/lib/api";
import type { AssistantBlock } from "@/lib/types";
import React, { useCallback, useMemo, useState } from "react";
import { motion, AnimatePresence } from "framer-motion";

const TAIL_BLOCK_TYPES = new Set<AssistantBlock["type"]>(["text", "token_stats"]);

function splitToolChain(blocks: AssistantBlock[]): {
  chainBlocks: { block: AssistantBlock; origIndex: number }[];
  tailBlocks: { block: AssistantBlock; origIndex: number }[];
  hasChain: boolean;
} {
  let lastTailStart = blocks.length;
  for (let i = blocks.length - 1; i >= 0; i--) {
    if (TAIL_BLOCK_TYPES.has(blocks[i].type)) {
      lastTailStart = i;
    } else {
      break;
    }
  }
  const chainBlocks = blocks
    .slice(0, lastTailStart)
    .map((block, i) => ({ block, origIndex: i }));
  const tailBlocks = blocks
    .slice(lastTailStart)
    .map((block, i) => ({ block, origIndex: lastTailStart + i }));

  return { chainBlocks, tailBlocks, hasChain: chainBlocks.length > 0 };
}

interface ChainStats {
  totalTools: number;
  iterations: number;
  successCount: number;
  errorCount: number;
  toolNameCounts: { name: string; count: number }[];
  hasSubagent: boolean;
}

function getChainStats(chainBlocks: { block: AssistantBlock }[]): ChainStats {
  const toolBlocks = chainBlocks.filter((b) => b.block.type === "tool_call");
  const iterations = chainBlocks.filter((b) => b.block.type === "iteration").length;
  const hasSubagent = chainBlocks.some((b) => b.block.type === "subagent");

  let successCount = 0;
  let errorCount = 0;
  const nameMap = new Map<string, number>();
  for (const { block } of toolBlocks) {
    if (block.type !== "tool_call") continue;
    if (block.status === "success") successCount++;
    if (block.status === "error") errorCount++;
    nameMap.set(block.name, (nameMap.get(block.name) || 0) + 1);
  }

  const toolNameCounts = Array.from(nameMap.entries())
    .sort((a, b) => b[1] - a[1])
    .map(([name, count]) => ({ name, count }));

  return { totalTools: toolBlocks.length, iterations, successCount, errorCount, toolNameCounts, hasSubagent };
}

function CollapsedChainCard({ stats, onExpand }: { stats: ChainStats; onExpand: () => void }) {
  const allSuccess = stats.errorCount === 0 && stats.totalTools > 0;
  const hasError = stats.errorCount > 0;

  return (
    <button
      type="button"
      onClick={onExpand}
      className="group/chain flex items-center gap-2.5 my-1.5 w-full text-left rounded-lg border px-3 py-2 transition-all duration-200 cursor-pointer hover:shadow-sm"
      style={{
        borderColor: hasError
          ? "var(--em-error-alpha-20, rgba(239,68,68,0.2))"
          : "var(--em-primary-alpha-15)",
        backgroundColor: hasError
          ? "rgba(239,68,68,0.03)"
          : "var(--em-primary-alpha-06, rgba(33,115,70,0.06))",
      }}
    >
      {/* 左侧强调条 */}
      <div
        className="self-stretch w-[3px] rounded-full flex-shrink-0"
        style={{
          backgroundColor: hasError ? "var(--em-error, #ef4444)" : "var(--em-primary)",
        }}
      />

      {/* 主内容 */}
      <div className="flex-1 min-w-0 flex flex-col gap-1.5">
        {/* 第一行：总概 */}
        <div className="flex items-center gap-2 text-xs">
          <Layers className="h-3 w-3 flex-shrink-0" style={{ color: "var(--em-primary)" }} />
          <span className="font-medium text-foreground">
            {stats.totalTools} <span className="hidden sm:inline">次工具</span><span className="sm:hidden">工具</span>调用
          </span>
          {stats.iterations > 0 && (
            <span className="flex items-center gap-1 text-muted-foreground">
              <Repeat className="h-2.5 w-2.5" />
              {stats.iterations + 1}<span className="hidden sm:inline"> 轮</span>
            </span>
          )}
          {/* 状态指示 */}
          {allSuccess && (
            <span className="flex items-center gap-0.5 text-[10px] font-medium" style={{ color: "var(--em-primary)" }}>
              <CheckCircle2 className="h-3 w-3" />
              <span className="hidden sm:inline">全部成功</span>
            </span>
          )}
          {hasError && (
            <span className="flex items-center gap-0.5 text-[10px] font-medium" style={{ color: "var(--em-error, #ef4444)" }}>
              <XCircle className="h-3 w-3" />
              {stats.errorCount}<span className="hidden sm:inline"> 失败</span>
            </span>
          )}
          {stats.hasSubagent && (
            <span className="inline-flex items-center rounded-full px-1.5 py-px text-[9px] font-medium bg-violet-500/10 text-violet-600 dark:text-violet-400">
              子代理
            </span>
          )}
        </div>

        {/* 第二行：工具名称徽章 */}
        {stats.toolNameCounts.length > 0 && (
          <div className="flex items-center gap-1 flex-wrap">
            {stats.toolNameCounts.map(({ name, count }) => (
              <span
                key={name}
                className="inline-flex items-center gap-1 rounded-md px-1.5 py-px text-[10px] font-mono bg-muted/50 text-muted-foreground border border-border/30"
              >
                <Wrench className="h-2 w-2 flex-shrink-0 opacity-50" />
                {name}
                {count > 1 && (
                  <span className="rounded-full bg-muted px-1 text-[9px] font-sans font-medium">
                    ×{count}
                  </span>
                )}
              </span>
            ))}
          </div>
        )}
      </div>

      {/* 展开箭头 */}
      <ChevronsUpDown className="h-3.5 w-3.5 flex-shrink-0 text-muted-foreground/40 group-hover/chain:text-muted-foreground transition-colors" />
    </button>
  );
}

interface AssistantMessageProps {
  messageId: string;
  blocks: AssistantBlock[];
  affectedFiles?: string[];
  isLastMessage?: boolean;
  onRetry?: () => void;
  onRetryWithModel?: (modelName: string) => void;
}

export const AssistantMessage = React.memo(function AssistantMessage({ messageId, blocks, affectedFiles, isLastMessage, onRetry, onRetryWithModel }: AssistantMessageProps) {
  const [collapsed, setCollapsed] = useState(false);
  // 仅最后一条消息需要订阅流式相关状态
  const isStreaming = useChatStore((s) => isLastMessage ? s.isStreaming : false);

  const { chainBlocks, tailBlocks, hasChain } = useMemo(
    () => splitToolChain(blocks),
    [blocks],
  );

  // 获取工具调用统计
  const stats = useMemo(() => getChainStats(chainBlocks), [chainBlocks]);
  const totalTools = stats.totalTools;
  const iterations = stats.iterations;

  // 历史 verification_report 内联到紧邻的前一个 verifier subagent block（只读，不再有新事件）
  const { verificationMap, skipIndices } = useMemo(() => {
    const vMap = new Map<number, { verdict: "pass" | "fail" | "unknown"; confidence: "high" | "medium" | "low"; checks: string[]; issues: string[]; mode: "advisory" | "blocking" }>();
    const skip = new Set<number>();
    for (let i = 1; i < blocks.length; i++) {
      const cur = blocks[i];
      const prev = blocks[i - 1];
      if (
        cur.type === "verification_report" &&
        prev.type === "subagent" &&
        prev.name === "verifier"
      ) {
        vMap.set(i - 1, { verdict: cur.verdict, confidence: cur.confidence, checks: cur.checks, issues: cur.issues, mode: cur.mode });
        skip.add(i);
      }
    }
    return { verificationMap: vMap, skipIndices: skip };
  }, [blocks]);

  const lastBlockIdx = blocks.length - 1;
  const lastBlock = lastBlockIdx >= 0 ? blocks[lastBlockIdx] : null;
  const isThinkingActive =
    isStreaming
    && lastBlock?.type === "thinking"
    && lastBlock.startedAt != null
    && lastBlock.duration == null;

  return (
    <div className="group/msg flex gap-2 sm:gap-2.5 py-2.5">
      <div
        className="flex-shrink-0 h-6 w-6 rounded-full flex items-center justify-center text-white text-[10px]"
        style={{ backgroundColor: "var(--em-accent)" }}
      >
        <FileSpreadsheet className="h-3.5 w-3.5" />
      </div>
      <div className="flex-1 min-w-0 border-l-[1.5px] pl-3 relative assistant-border-gradient">

        <AnimatePresence mode="wait" initial={false}>
          {collapsed && ((totalTools > 0 && hasChain) || (totalTools === 0 && tailBlocks.length > 0)) ? (
            <motion.div
              key="collapsed"
              initial={{ opacity: 0, height: 0 }}
              animate={{ opacity: 1, height: "auto" }}
              exit={{ opacity: 0, height: 0 }}
              transition={{ duration: 0.2, ease: "easeOut" }}
            >
              <CollapsedChainCard stats={getChainStats(chainBlocks)} onExpand={() => setCollapsed(false)} />
            </motion.div>
          ) : (
            <motion.div
              key="expanded"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              transition={{ duration: 0.2, ease: "easeOut" }}
            >
              {chainBlocks.map(({ block, origIndex }) => (
                <AssistantBlockRenderer
                  key={origIndex}
                  block={block}
                  blockIndex={origIndex}
                  messageId={messageId}
                  isThinkingActive={block.type === "thinking" && origIndex === lastBlockIdx && isThinkingActive}
                  isStreamingText={isStreaming && block.type === "text" && origIndex === lastBlockIdx}
                  showCollapseButton={origIndex === 0 && totalTools > 0 && hasChain}
                  onCollapse={() => setCollapsed(true)}
                  verificationReport={verificationMap.get(origIndex)}
                  skipRender={skipIndices.has(origIndex)}
                  onRetry={onRetry}
                  onRetryWithModel={onRetryWithModel}
                />
              ))}
            </motion.div>
          )}
        </AnimatePresence>

        {tailBlocks.map(({ block, origIndex }) => (
          <AssistantBlockRenderer
            key={origIndex}
            block={block}
            blockIndex={origIndex}
            messageId={messageId}
            isThinkingActive={block.type === "thinking" && origIndex === lastBlockIdx && isThinkingActive}
            isStreamingText={isStreaming && block.type === "text" && origIndex === lastBlockIdx}
            showCollapseButton={false}
            onCollapse={undefined}
            defaultExpanded={block.type === "text"}
            verificationReport={verificationMap.get(origIndex)}
            skipRender={skipIndices.has(origIndex)}
            onRetry={onRetry}
            onRetryWithModel={onRetryWithModel}
          />
        ))}

        {affectedFiles && affectedFiles.length > 0 && (
          <AffectedFilesBadges files={affectedFiles} />
        )}

        <MessageActions
          blocks={blocks}
          onRetry={onRetry}
          onRetryWithModel={onRetryWithModel}
          isStreaming={isStreaming}
          isLastMessage={isLastMessage}
        />
      </div>
    </div>
  );
});

const MAX_FILE_PATH_LENGTH = 4096;

function isPlausibleFilePath(p: string): boolean {
  if (!p || p.length > MAX_FILE_PATH_LENGTH) return false;
  if (/[\n\r\t]/.test(p)) return false;
  if (/\s{2,}/.test(p)) return false;
  return true;
}

function AffectedFilesBadges({ files }: { files: string[] }) {
  const openPanel = useExcelStore((s) => s.openPanel);
  const addRecentFile = useExcelStore((s) => s.addRecentFile);
  const activeSessionId = useSessionStore((s) => s.activeSessionId);

  const validFiles = useMemo(
    () => files.filter(isPlausibleFilePath),
    [files],
  );

  const handleExcelClick = useCallback(
    (filePath: string) => {
      const normalized = normalizeExcelPath(filePath);
      const filename = normalized.split("/").pop() || normalized;

      const recentFiles = useExcelStore.getState().recentFiles;
      const existing = recentFiles.find(
        (f) => normalizeExcelPath(f.path) === normalized,
      );
      const resolvedPath = existing ? existing.path : normalized;

      addRecentFile({ path: resolvedPath, filename });
      openPanel(resolvedPath);
    },
    [openPanel, addRecentFile],
  );

  if (validFiles.length === 0) return null;

  const EXCEL_EXTS = new Set([".xlsx", ".xls", ".xlsm", ".xlsb", ".csv", ".tsv"]);
  const isExcel = (name: string) => {
    const dot = name.lastIndexOf(".");
    return dot >= 0 && EXCEL_EXTS.has(name.slice(dot).toLowerCase());
  };

  return (
    <motion.div
      className="flex flex-wrap items-center gap-1 mt-2 pt-1.5 border-t border-border/25"
      initial="hidden"
      animate="show"
      variants={{ hidden: {}, show: { transition: { staggerChildren: 0.05, delayChildren: 0.1 } } }}
    >
      <FileSpreadsheet
        className="h-3 w-3 text-muted-foreground flex-shrink-0"
      />
      <span className="text-[10px] text-muted-foreground mr-0.5">涉及文件</span>
      {validFiles.map((filePath) => {
        const filename = filePath.split("/").pop() || filePath;
        const excel = isExcel(filePath);
        const previewable = !excel && isCodeFile(filePath);
        return (
          <motion.span
            key={filePath}
            className="inline-flex items-center gap-0.5 rounded-full text-[11px] leading-4 font-medium pl-2 pr-0.5 py-0 bg-[var(--em-primary-alpha-10)] text-[var(--em-primary)]"
            variants={{ hidden: { opacity: 0, scale: 0.8 }, show: { opacity: 1, scale: 1, transition: { duration: 0.2, ease: "easeOut" } } }}
          >
            {previewable ? (
              <CodePreviewModal
                filePath={filePath}
                filename={filename}
                trigger={
                  <button
                    type="button"
                    className="touch-compact hover:underline cursor-pointer transition-colors leading-4"
                  >
                    {filename}
                  </button>
                }
              />
            ) : (
              <button
                type="button"
                onClick={() => excel ? handleExcelClick(filePath) : downloadFile(filePath, filename, activeSessionId ?? undefined).catch(() => {})}
                className="touch-compact hover:underline cursor-pointer transition-colors leading-4"
              >
                {filename}
              </button>
            )}
            <button
              type="button"
              onClick={() =>
                downloadFile(
                  filePath,
                  filename,
                  activeSessionId ?? undefined,
                ).catch(() => {})
              }
              className="touch-compact rounded p-1 sm:p-0.5 hover:bg-[var(--em-primary-alpha-20)] transition-colors cursor-pointer"
              title="下载"
            >
              <Download className="h-3 w-3" />
            </button>
          </motion.span>
        );
      })}
    </motion.div>
  );
}
