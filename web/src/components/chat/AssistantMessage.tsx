"use client";

import {
  FileSpreadsheet,
  Route,
  Repeat,
  Info,
  Zap,
  CircleStop,
  FolderOpen,
  ChevronsUpDown,
  Loader2,
  Brain,
} from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { ThinkingBlock } from "./ThinkingBlock";
import { MentionHighlighter } from "./MentionHighlighter";
import { ToolCallCard } from "./ToolCallCard";
import { SubagentBlock } from "./SubagentBlock";
import { TaskList } from "./TaskList";
import { UndoableCard } from "./UndoableCard";
import { useChatStore } from "@/stores/chat-store";
import type { PipelineStatus } from "@/stores/chat-store";
import { useExcelStore } from "@/stores/excel-store";
import { useUIStore } from "@/stores/ui-store";
import { buildApiUrl } from "@/lib/api";
import type { AssistantBlock } from "@/lib/types";
import React, { useCallback, useEffect, useMemo, useState } from "react";

/**
 * Recursively process React children: replace plain string nodes
 * with MentionHighlighter so @mentions get blue-highlighted.
 */
function processChildren(children: React.ReactNode): React.ReactNode {
  return React.Children.map(children, (child) => {
    if (typeof child === "string") {
      return <MentionHighlighter text={child} />;
    }
    return child;
  });
}

const SAVE_PATH_RE = /对话已保存至[：:]\s*`(.+?)`/;

function SaveResultCard({ path }: { path: string }) {
  const filename = path.split("/").pop() || path;
  const dir = path.substring(0, path.length - filename.length);

  const handleReveal = useCallback(() => {
    fetch(buildApiUrl("/files/reveal"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path }),
    }).catch(() => {});
  }, [path]);

  return (
    <div className="my-2">
      <p className="text-sm text-foreground mb-2">对话已保存至：</p>
      <button
        type="button"
        onClick={handleReveal}
        className="group flex items-start gap-2.5 w-full rounded-lg px-3 py-2.5 text-left cursor-pointer transition-all border border-[var(--em-primary-alpha-15)] bg-[var(--em-primary-alpha-06)] hover:bg-[var(--em-primary-alpha-15)] hover:border-[var(--em-primary-alpha-20)]"
        title="点击在文件管理器中打开"
      >
        <FolderOpen className="h-4 w-4 mt-0.5 flex-shrink-0 text-[var(--em-primary)] group-hover:scale-110 transition-transform" />
        <div className="min-w-0 flex-1">
          <span className="block text-sm font-medium text-[var(--em-primary)] break-all">
            {filename}
          </span>
          <span className="block text-xs text-muted-foreground break-all mt-0.5">
            {dir}
          </span>
        </div>
        <span className="text-[10px] text-muted-foreground self-center flex-shrink-0 opacity-0 group-hover:opacity-100 transition-opacity">
          打开文件夹
        </span>
      </button>
    </div>
  );
}

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

function chainSummary(chainBlocks: { block: AssistantBlock }[]): string {
  const toolCalls = chainBlocks.filter((b) => b.block.type === "tool_call").length;
  const iterations = chainBlocks.filter((b) => b.block.type === "iteration").length;
  const parts: string[] = [];
  if (toolCalls > 0) parts.push(`${toolCalls} 次工具调用`);
  if (iterations > 0) parts.push(`${iterations} 轮迭代`);
  return parts.length > 0 ? parts.join(" · ") : "工具链";
}

interface AssistantMessageProps {
  messageId: string;
  blocks: AssistantBlock[];
  affectedFiles?: string[];
}

export function AssistantMessage({ messageId, blocks, affectedFiles }: AssistantMessageProps) {
  const [collapsed, setCollapsed] = useState(false);
  const pipelineStatus = useChatStore((s) => s.pipelineStatus);
  const isStreaming = useChatStore((s) => s.isStreaming);

  const { chainBlocks, tailBlocks, hasChain } = useMemo(
    () => splitToolChain(blocks),
    [blocks],
  );

  const showPipeline = blocks.length === 0 && isStreaming;

  const lastBlockIdx = blocks.length - 1;
  const lastBlock = lastBlockIdx >= 0 ? blocks[lastBlockIdx] : null;
  const isThinkingActive =
    isStreaming
    && lastBlock?.type === "thinking"
    && lastBlock.startedAt != null
    && lastBlock.duration == null;

  return (
    <div className="flex gap-3 py-4">
      <div
        className="flex-shrink-0 h-7 w-7 rounded-full flex items-center justify-center text-white text-xs"
        style={{ backgroundColor: "var(--em-accent)" }}
      >
        <FileSpreadsheet className="h-4 w-4" />
      </div>
      <div className="flex-1 min-w-0 border-l-2 pl-4 relative" style={{ borderColor: "var(--em-primary)" }}>
        {hasChain && tailBlocks.length > 0 && !collapsed && (
          <button
            type="button"
            onClick={() => setCollapsed(true)}
            className="absolute -top-1 right-0 z-10 flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[10px] text-muted-foreground hover:bg-muted/50 hover:text-foreground transition-colors cursor-pointer"
            title="折叠工具链"
          >
            <ChevronsUpDown className="h-3 w-3" />
            <span>折叠</span>
          </button>
        )}

        {collapsed && hasChain ? (
          <button
            type="button"
            onClick={() => setCollapsed(false)}
            className="flex items-center gap-2 my-1.5 px-2.5 py-1.5 rounded-md border border-border/60 bg-muted/20 text-xs text-muted-foreground hover:bg-muted/40 hover:text-foreground transition-colors cursor-pointer w-full text-left"
          >
            <ChevronsUpDown className="h-3 w-3 flex-shrink-0" />
            <span>{chainSummary(chainBlocks)}</span>
          </button>
        ) : (
          chainBlocks.map(({ block, origIndex }) => (
            <AssistantBlockRenderer
              key={origIndex}
              block={block}
              blockIndex={origIndex}
              messageId={messageId}
              isThinkingActive={block.type === "thinking" && origIndex === lastBlockIdx && isThinkingActive}
            />
          ))
        )}

        {tailBlocks.map(({ block, origIndex }) => (
          <AssistantBlockRenderer
            key={origIndex}
            block={block}
            blockIndex={origIndex}
            messageId={messageId}
            isThinkingActive={block.type === "thinking" && origIndex === lastBlockIdx && isThinkingActive}
          />
        ))}

        {showPipeline && (
          <PipelineIndicator status={pipelineStatus} />
        )}
        {affectedFiles && affectedFiles.length > 0 && (
          <AffectedFilesBadges files={affectedFiles} />
        )}
      </div>
    </div>
  );
}

function AffectedFilesBadges({ files }: { files: string[] }) {
  const openPanel = useExcelStore((s) => s.openPanel);
  const addRecentFile = useExcelStore((s) => s.addRecentFile);

  const handleClick = useCallback(
    (filePath: string) => {
      const filename = filePath.split("/").pop() || filePath;
      addRecentFile({ path: filePath, filename });
      openPanel(filePath);
    },
    [openPanel, addRecentFile],
  );

  return (
    <div className="flex flex-wrap items-center gap-1.5 mt-3 pt-2 border-t border-border/30">
      <FileSpreadsheet
        className="h-3 w-3 text-muted-foreground flex-shrink-0"
      />
      <span className="text-[10px] text-muted-foreground mr-0.5">涉及文件</span>
      {files.map((filePath) => {
        const filename = filePath.split("/").pop() || filePath;
        return (
          <button
            key={filePath}
            type="button"
            onClick={() => handleClick(filePath)}
            className="inline-flex items-center gap-1 rounded-full text-xs font-medium pl-2.5 pr-2.5 py-0.5 transition-colors cursor-pointer bg-[var(--em-primary-alpha-10)] text-[var(--em-primary)] hover:bg-[var(--em-primary-alpha-20)]"
          >
            {filename}
          </button>
        );
      })}
    </div>
  );
}

function PipelineIndicator({ status }: { status: PipelineStatus | null }) {
  const [elapsed, setElapsed] = useState(0);

  useEffect(() => {
    if (!status) {
      setElapsed(0);
      return;
    }
    setElapsed(Math.round((Date.now() - status.startedAt) / 1000));
    const timer = setInterval(() => {
      setElapsed(Math.round((Date.now() - status.startedAt) / 1000));
    }, 1000);
    return () => clearInterval(timer);
  }, [status]);

  return (
    <div className="flex items-center gap-2 h-7 text-sm text-muted-foreground">
      <Loader2 className="h-3.5 w-3.5 animate-spin flex-shrink-0" style={{ color: "var(--em-primary)" }} />
      <span>{status?.message || "正在准备..."}</span>
      {status && elapsed > 0 && (
        <span className="text-xs opacity-50">{elapsed}s</span>
      )}
    </div>
  );
}

function AssistantBlockRenderer({ block, blockIndex, messageId, isThinkingActive }: { block: AssistantBlock; blockIndex: number; messageId: string; isThinkingActive?: boolean }) {
  switch (block.type) {
    case "thinking":
      return (
        <ThinkingBlock
          content={block.content}
          duration={block.duration}
          startedAt={block.startedAt}
          isActive={isThinkingActive}
        />
      );
    case "text": {
      const saveMatch = block.content.match(SAVE_PATH_RE);
      if (saveMatch) {
        return <SaveResultCard path={saveMatch[1]} />;
      }
      return (
        <div className="prose prose-sm max-w-none text-foreground">
          <ReactMarkdown
            remarkPlugins={[remarkGfm]}
            components={{
              p({ children }) {
                return <p>{processChildren(children)}</p>;
              },
              li({ children }) {
                return <li>{processChildren(children)}</li>;
              },
            }}
          >
            {block.content}
          </ReactMarkdown>
        </div>
      );
    }
    case "tool_call":
      return (
        <ToolCallCard
          toolCallId={block.toolCallId}
          name={block.name}
          args={block.args}
          status={block.status}
          result={block.result}
          error={block.error}
        />
      );
    case "subagent":
      return (
        <SubagentBlock
          name={block.name}
          reason={block.reason}
          iterations={block.iterations}
          toolCalls={block.toolCalls}
          status={block.status}
          summary={block.summary}
        />
      );
    case "task_list":
      return <TaskList items={block.items} />;
    case "iteration":
      return (
        <div className="flex items-center gap-2 my-2 text-xs text-muted-foreground">
          <Repeat className="h-3 w-3" />
          <span>迭代 {block.iteration}</span>
          <div className="flex-1 border-t border-border/50" />
        </div>
      );
    case "status": {
      const isStopped = block.label === "对话已停止";
      if (isStopped) {
        return (
          <div className="flex items-center gap-2 my-3 px-3 py-2 rounded-lg border border-amber-500/30 bg-amber-500/5 text-sm text-amber-700 dark:text-amber-400">
            <CircleStop className="h-4 w-4 flex-shrink-0" />
            <span className="font-medium">{block.label}</span>
            {block.detail && (
              <span className="text-amber-600/70 dark:text-amber-500/70 text-xs">{block.detail}</span>
            )}
          </div>
        );
      }
      const Icon = block.variant === "route" ? Route : Info;
      return (
        <div className="flex items-center gap-2 my-1.5 text-xs text-muted-foreground">
          <Icon className="h-3 w-3 flex-shrink-0" />
          <span>{block.label}</span>
          {block.detail && (
            <span className="text-muted-foreground/60">{block.detail}</span>
          )}
        </div>
      );
    }
    case "approval_action":
      return (
        <ApprovalActionBlock
          block={block}
          blockIndex={blockIndex}
          messageId={messageId}
        />
      );
    case "token_stats":
      return (
        <div className="flex items-center gap-3 mt-3 pt-2 border-t border-border/30 text-[10px] text-muted-foreground">
          <Zap className="h-3 w-3" />
          <span>{block.iterations} 轮迭代</span>
          <span>·</span>
          <span>输入 {block.promptTokens.toLocaleString()}</span>
          <span>·</span>
          <span>输出 {block.completionTokens.toLocaleString()}</span>
          <span>·</span>
          <span className="font-medium">合计 {block.totalTokens.toLocaleString()} tokens</span>
        </div>
      );
    case "memory_extracted":
      return <MemoryExtractedBlock block={block} />;
    default:
      return null;
  }
}

function ApprovalActionBlock({
  block,
  blockIndex,
  messageId,
}: {
  block: Extract<AssistantBlock, { type: "approval_action" }>;
  blockIndex: number;
  messageId: string;
}) {
  const setMessages = useChatStore((s) => s.setMessages);
  const messages = useChatStore((s) => s.messages);

  const handleUndone = (_approvalId: string, error?: string) => {
    const updated = messages.map((m) => {
      if (m.id !== messageId || m.role !== "assistant") return m;
      const blocks = [...m.blocks];
      blocks[blockIndex] = {
        ...blocks[blockIndex],
        undone: !error,
        undoError: error,
      } as AssistantBlock;
      return { ...m, blocks };
    });
    setMessages(updated);
  };

  return (
    <UndoableCard
      approvalId={block.approvalId}
      toolName={block.toolName}
      success={block.success}
      undoable={block.undoable}
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

function MemoryExtractedBlock({
  block,
}: {
  block: Extract<AssistantBlock, { type: "memory_extracted" }>;
}) {
  const [expanded, setExpanded] = useState(false);
  const openSettings = useUIStore((s) => s.openSettings);

  const preview = block.entries.slice(0, 3);
  const hasMore = block.entries.length > 3;

  return (
    <div className="my-2 rounded-lg border border-emerald-500/30 bg-emerald-500/5 overflow-hidden">
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
    </div>
  );
}
