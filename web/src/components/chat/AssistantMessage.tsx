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
  Brain,
  Download,
  ChevronDown,
  ChevronUp,
} from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { ThinkingBlock } from "./ThinkingBlock";
import { MentionHighlighter } from "./MentionHighlighter";
import { ToolCallCard } from "./ToolCallCard";
import { SubagentBlock } from "./SubagentBlock";
import { TaskList } from "./TaskList";
import { UndoableCard } from "./UndoableCard";
import { PipelineStepper } from "./PipelineStepper";
import { CodeBlock } from "./CodeBlock";
import { MessageActions } from "./MessageActions";
import { VlmPipelineCard } from "./VlmPipelineCard";
import { useChatStore } from "@/stores/chat-store";
import { useExcelStore } from "@/stores/excel-store";
import { useSessionStore } from "@/stores/session-store";
import { useUIStore } from "@/stores/ui-store";
import { buildApiUrl, downloadFile, normalizeExcelPath } from "@/lib/api";
import type { AssistantBlock } from "@/lib/types";
import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { motion, AnimatePresence } from "framer-motion";

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

// ── Stable references for ReactMarkdown to avoid re-parsing on every render ──

const remarkPluginsStable = [remarkGfm];

// 识别为可下载工作区文件的扩展名
const DOWNLOADABLE_EXTENSIONS = /\.(xlsx|xls|csv|tsv|pdf|zip|tar|gz|docx|pptx|txt|json|xml|html|md)$/i;

function isWorkspaceFileLink(href: string): boolean {
  if (!href) return false;
  // 相对路径：./foo.xlsx、foo.xlsx、subdir/foo.xlsx
  if (href.startsWith("./") || href.startsWith("../") || !href.includes("://")) {
    return DOWNLOADABLE_EXTENSIONS.test(href);
  }
  return false;
}

function FileDownloadLink({ href, children }: { href: string; children: React.ReactNode }) {
  const activeSessionId = useSessionStore((s) => s.activeSessionId);
  const filename = href.split("/").pop() || href;
  const handleDownload = useCallback(
    (e: React.MouseEvent) => {
      e.preventDefault();
      downloadFile(href, filename, activeSessionId ?? undefined).catch(() => {});
    },
    [href, filename, activeSessionId],
  );
  return (
    <button
      type="button"
      onClick={handleDownload}
      className="inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-xs font-medium cursor-pointer transition-all border border-[var(--em-primary-alpha-15)] bg-[var(--em-primary-alpha-06)] hover:bg-[var(--em-primary-alpha-15)] hover:border-[var(--em-primary-alpha-20)] text-[var(--em-primary)]"
      title={`下载 ${filename}`}
    >
      <Download className="h-3 w-3 flex-shrink-0" />
      <span className="break-all">{children}</span>
    </button>
  );
}

const markdownComponents: React.ComponentProps<typeof ReactMarkdown>["components"] = {
  p({ children }) {
    return <p>{processChildren(children)}</p>;
  },
  li({ children }) {
    return <li>{processChildren(children)}</li>;
  },
  // 拦截链接：工作区文件链接 → 下载按钮，其他 → 普通 <a>
  a({ href, children }) {
    if (href && isWorkspaceFileLink(href)) {
      return <FileDownloadLink href={href}>{children}</FileDownloadLink>;
    }
    return (
      <a href={href} target="_blank" rel="noopener noreferrer" className="text-[var(--em-primary)] underline">
        {children}
      </a>
    );
  },
  // 围栏代码块 → 带语法高亮与复制按钮的 CodeBlock
  pre({ children }) {
    // react-markdown 将围栏代码包在 <pre><code>…</code></pre> 中，此处透传由 code 组件通过 CodeBlock 渲染
    return <>{children}</>;
  },
  code({ className, children, node, ...rest }) {
    const match = /language-(\w+)/.exec(className || "");
    const codeString = String(children).replace(/\n$/, "");
    // 围栏代码块（有语言类或为多行）
    if (match || (node?.position && codeString.includes("\n"))) {
      return <CodeBlock language={match?.[1]} code={codeString} />;
    }
    // 行内代码
    return (
      <code
        className="rounded px-1 py-0.5 text-[12.5px] font-mono bg-[var(--em-primary-alpha-06)] text-[var(--em-primary-dark)]"
        {...rest}
      >
        {children}
      </code>
    );
  },
};

const MAX_COLLAPSED_HEIGHT_ASSISTANT = 400; // px

const MemoizedMarkdown = React.memo(function MemoizedMarkdown({
  content,
  isStreamingText,
}: {
  content: string;
  isStreamingText?: boolean;
}) {
  const contentRef = useRef<HTMLDivElement>(null);
  const [expanded, setExpanded] = useState(false);
  const [needsExpand, setNeedsExpand] = useState(false);

  useEffect(() => {
    if (contentRef.current) {
      setNeedsExpand(contentRef.current.scrollHeight > MAX_COLLAPSED_HEIGHT_ASSISTANT);
    }
  }, [content]);

  return (
    <div className="relative">
      <div
        ref={contentRef}
        className={`prose prose-sm max-w-none text-foreground text-[13px] leading-relaxed overflow-hidden transition-[max-height] duration-300${isStreamingText ? " streaming-cursor" : ""}`}
        style={{
          maxHeight: needsExpand && !expanded && !isStreamingText ? `${MAX_COLLAPSED_HEIGHT_ASSISTANT}px` : undefined,
        }}
      >
        <ReactMarkdown
          remarkPlugins={remarkPluginsStable}
          components={markdownComponents}
        >
          {content}
        </ReactMarkdown>
      </div>
      {needsExpand && !expanded && !isStreamingText && (
        <div className="relative -mt-8 pt-8 bg-gradient-to-t from-background to-transparent">
          <button
            type="button"
            onClick={() => setExpanded(true)}
            className="flex items-center gap-1 text-[11px] text-[var(--em-primary)] hover:text-[var(--em-primary-dark)] transition-colors cursor-pointer"
          >
            <ChevronDown className="h-3 w-3" />
            展开全部
          </button>
        </div>
      )}
      {needsExpand && expanded && !isStreamingText && (
        <button
          type="button"
          onClick={() => setExpanded(false)}
          className="flex items-center gap-1 mt-1 text-[11px] text-[var(--em-primary)] hover:text-[var(--em-primary-dark)] transition-colors cursor-pointer"
        >
          <ChevronUp className="h-3 w-3" />
          收起
        </button>
      )}
    </div>
  );
});

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
  isLastMessage?: boolean;
  onRetry?: () => void;
  onRetryWithModel?: (modelName: string) => void;
}

export const AssistantMessage = React.memo(function AssistantMessage({ messageId, blocks, affectedFiles, isLastMessage, onRetry, onRetryWithModel }: AssistantMessageProps) {
  const [collapsed, setCollapsed] = useState(false);
  // 仅最后一条消息需要订阅流式相关状态
  const pipelineStatus = useChatStore((s) => isLastMessage ? s.pipelineStatus : null);
  const isStreaming = useChatStore((s) => isLastMessage ? s.isStreaming : false);

  const { chainBlocks, tailBlocks, hasChain } = useMemo(
    () => splitToolChain(blocks),
    [blocks],
  );

  // 流式时始终显示 pipeline 进度，不限于 blocks 为空时。保证多轮阶段（准备上下文、调用 LLM 等）在首个 thinking/text 块到达后仍可见。
  const showPipeline = isStreaming && (blocks.length === 0 || pipelineStatus !== null);

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
      <div className="flex-1 min-w-0 border-l-[1.5px] pl-3 relative" style={{ borderColor: "var(--em-primary)" }}>

        <AnimatePresence mode="wait" initial={false}>
          {collapsed && hasChain ? (
            <motion.div
              key="collapsed"
              initial={{ opacity: 0, height: 0 }}
              animate={{ opacity: 1, height: "auto" }}
              exit={{ opacity: 0, height: 0 }}
              transition={{ duration: 0.2, ease: "easeOut" }}
            >
              <button
                type="button"
                onClick={() => setCollapsed(false)}
                className="flex items-center gap-2 my-1.5 px-2.5 py-1.5 rounded-md border border-border/60 bg-muted/20 text-xs text-muted-foreground hover:bg-muted/40 hover:text-foreground transition-colors cursor-pointer w-full text-left"
              >
                <ChevronsUpDown className="h-3 w-3 flex-shrink-0" />
                <span>{chainSummary(chainBlocks)}</span>
              </button>
              {/* Even when collapsed, show text and thinking blocks from the chain
                  so the user can still see the agent's analysis and reasoning. */}
              {chainBlocks
                .filter(({ block }) => block.type === "text" || block.type === "thinking")
                .map(({ block, origIndex }) => (
                  <AssistantBlockRenderer
                    key={origIndex}
                    block={block}
                    blockIndex={origIndex}
                    messageId={messageId}
                    isThinkingActive={block.type === "thinking" && origIndex === lastBlockIdx && isThinkingActive}
                    isStreamingText={isStreaming && block.type === "text" && origIndex === lastBlockIdx}
                  />
                ))}
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
                  showCollapseButton={origIndex === 0 && hasChain && tailBlocks.length > 0}
                  onCollapse={() => setCollapsed(true)}
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
          />
        ))}

        {showPipeline && (
          <PipelineStepper status={pipelineStatus} />
        )}
        {affectedFiles && affectedFiles.length > 0 && (
          <AffectedFilesBadges files={affectedFiles} />
        )}

        <MessageActions
          blocks={blocks}
          onRetry={onRetry}
          onRetryWithModel={onRetryWithModel}
          isStreaming={isStreaming}
        />
      </div>
    </div>
  );
});

const MAX_FILE_PATH_LENGTH = 260;

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

  const handleClick = useCallback(
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

  return (
    <div className="flex flex-wrap items-center gap-1.5 mt-3 pt-2 border-t border-border/30">
      <FileSpreadsheet
        className="h-3 w-3 text-muted-foreground flex-shrink-0"
      />
      <span className="text-[10px] text-muted-foreground mr-0.5">涉及文件</span>
      {validFiles.map((filePath) => {
        const filename = filePath.split("/").pop() || filePath;
        return (
          <span
            key={filePath}
            className="inline-flex items-center gap-1 rounded-full text-xs font-medium pl-2.5 pr-1 py-0.5 bg-[var(--em-primary-alpha-10)] text-[var(--em-primary)]"
          >
            <button
              type="button"
              onClick={() => handleClick(filePath)}
              className="hover:underline cursor-pointer transition-colors"
            >
              {filename}
            </button>
            <button
              type="button"
              onClick={() =>
                downloadFile(
                  filePath,
                  filename,
                  activeSessionId ?? undefined,
                ).catch(() => {})
              }
              className="rounded p-0.5 hover:bg-[var(--em-primary-alpha-20)] transition-colors cursor-pointer"
              title="下载"
            >
              <Download className="h-3 w-3" />
            </button>
          </span>
        );
      })}
    </div>
  );
}

function IterationDivider({ iteration }: { iteration: number; isActive?: boolean }) {
  return (
    <div className="flex items-center gap-2 my-3 text-xs">
      <div className="flex items-center gap-1.5 px-2 py-1 rounded-md bg-[var(--em-primary-alpha-06)] border border-[var(--em-primary-alpha-15)]">
        <Repeat className="h-3 w-3" style={{ color: "var(--em-primary)" }} />
        <span className="font-medium" style={{ color: "var(--em-primary)" }}>
          第 {iteration} 轮迭代
        </span>
      </div>
      <div className="flex-1 border-t border-[var(--em-primary-alpha-15)]" />
    </div>
  );
}

const AssistantBlockRenderer = React.memo(function AssistantBlockRenderer({ 
  block, 
  blockIndex, 
  messageId, 
  isThinkingActive, 
  isStreamingText,
  showCollapseButton,
  onCollapse
}: { 
  block: AssistantBlock; 
  blockIndex: number; 
  messageId: string; 
  isThinkingActive?: boolean; 
  isStreamingText?: boolean;
  showCollapseButton?: boolean;
  onCollapse?: () => void;
}) {
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
      return <MemoizedMarkdown content={block.content} isStreamingText={isStreamingText} />;
    }
    case "tool_call": {
      const isVlmExtract = block.name === "extract_table_spec";
      const imagePath = isVlmExtract
        ? (block.args?.file_path as string | undefined)
        : undefined;
      return (
        <>
          <ToolCallCard
            toolCallId={block.toolCallId}
            name={block.name}
            args={block.args}
            status={block.status}
            result={block.result}
            error={block.error}
          />
          {isVlmExtract && <VlmPipelineCard imagePath={imagePath} />}
        </>
      );
    }
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
        <IterationDivider
          iteration={block.iteration}
          isActive={isStreamingText !== undefined && blockIndex === (isStreamingText ? -1 : blockIndex)}
        />
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
        <div className="flex items-center justify-between gap-2 my-1.5 text-xs text-muted-foreground">
          <div className="flex items-center gap-2">
            <Icon className="h-3 w-3 flex-shrink-0" />
            <span className="flex-shrink-0">{block.label}</span>
            {block.detail && (
              <span className="text-muted-foreground/60 flex-shrink-0">{block.detail}</span>
            )}
          </div>
          {showCollapseButton && onCollapse && (
            <button
              type="button"
              onClick={onCollapse}
              className="flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[10px] text-muted-foreground hover:bg-muted/50 hover:text-foreground transition-colors cursor-pointer"
              title="折叠工具链"
            >
              <ChevronsUpDown className="h-3 w-3" />
              <span>折叠</span>
            </button>
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
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 mt-3 pt-2 border-t border-border/30 text-[10px] text-muted-foreground">
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
    case "file_download":
      return <FileDownloadCard block={block} />;
    default:
      return null;
  }
});

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

function FileDownloadCard({ block }: { block: Extract<AssistantBlock, { type: "file_download" }> }) {
  const activeSessionId = useSessionStore((s) => s.activeSessionId);
  const handleDownload = useCallback(() => {
    downloadFile(block.filePath, block.filename, activeSessionId ?? undefined).catch(() => {});
  }, [block.filePath, block.filename, activeSessionId]);

  return (
    <button
      type="button"
      onClick={handleDownload}
      className="group flex items-center gap-3 w-full my-2 rounded-lg px-3 py-2.5 text-left cursor-pointer transition-all border border-[var(--em-primary-alpha-15)] bg-[var(--em-primary-alpha-06)] hover:bg-[var(--em-primary-alpha-15)] hover:border-[var(--em-primary-alpha-20)]"
      title={`下载 ${block.filename}`}
    >
      <div className="flex-shrink-0 h-8 w-8 rounded-md flex items-center justify-center bg-[var(--em-primary-alpha-15)] group-hover:bg-[var(--em-primary-alpha-20)] transition-colors">
        <Download className="h-4 w-4 text-[var(--em-primary)]" />
      </div>
      <div className="min-w-0 flex-1">
        <span className="block text-sm font-medium text-[var(--em-primary)] break-all">
          {block.filename}
        </span>
        {block.description && (
          <span className="block text-xs text-muted-foreground mt-0.5 line-clamp-1">
            {block.description}
          </span>
        )}
      </div>
      <span className="text-[10px] text-muted-foreground flex-shrink-0 opacity-0 group-hover:opacity-100 transition-opacity">
        点击下载
      </span>
    </button>
  );
}
