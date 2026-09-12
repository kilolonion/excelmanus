"use client";

import {
  Info,
  Zap,
  CircleStop,
  ChevronsUpDown,
  CheckCircle2,
  XCircle,
  Wrench,
} from "lucide-react";
import { isHiddenAssistantChrome } from "@/lib/assistant-chrome";
import { ThinkingBlock } from "../ThinkingBlock";
import { ToolCallCard } from "../ToolCallCard";
import { AskUserCard } from "../AskUserCard";
import { SubagentBlock } from "../SubagentBlock";
import { TaskList } from "../TaskList";
import VerificationCard from "../VerificationCard";
import { ConfigErrorCard } from "../ConfigErrorCard";
import { FailureGuidanceCard } from "../FailureGuidanceCard";
import type { AssistantBlock } from "@/lib/types";
import React from "react";
import { MemoizedMarkdown } from "./MemoizedMarkdown";
import {
  ApprovalActionBlock,
  FileDownloadCard,
  MemoryExtractedBlock,
  SaveResultCard,
  StagingHintCard,
} from "./block-cards";

const SAVE_PATH_RE = /对话已保存至[：:]\s*`(.+?)`/;

export type VerificationReportInline = {
  verdict: "pass" | "fail" | "unknown";
  confidence: "high" | "medium" | "low";
  checks: string[];
  issues: string[];
  mode: "advisory" | "blocking";
};

export interface AssistantBlockRendererProps {
  block: AssistantBlock;
  blockIndex: number;
  messageId: string;
  isThinkingActive?: boolean;
  isStreamingText?: boolean;
  showCollapseButton?: boolean;
  onCollapse?: () => void;
  defaultExpanded?: boolean;
  verificationReport?: VerificationReportInline;
  skipRender?: boolean;
  onRetry?: () => void;
  onRetryWithModel?: (modelName: string) => void;
}

export const AssistantBlockRenderer = React.memo(function AssistantBlockRenderer({
  block,
  blockIndex,
  messageId,
  isThinkingActive,
  isStreamingText,
  showCollapseButton,
  onCollapse,
  defaultExpanded,
  verificationReport,
  skipRender,
  onRetry,
  onRetryWithModel,
}: AssistantBlockRendererProps) {
  if (skipRender || isHiddenAssistantChrome(block)) return null;
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
      return <MemoizedMarkdown content={block.content} isStreamingText={isStreamingText} defaultExpanded={defaultExpanded} />;
    }
    case "tool_call": {
      if (block.name === "ask_user" || block.name === "suggest_mode_switch") {
        return (
          <AskUserCard
            args={block.args}
            status={block.status}
            result={block.result}
          />
        );
      }
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
          success={block.success}
          tools={block.tools}
          verificationReport={verificationReport}
        />
      );
    case "task_list":
      return <TaskList items={block.items} />;
    case "iteration":
      return null;
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
      const Icon = Info;
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
        <div className="flex items-center gap-x-3 mt-3 pt-2 border-t border-border/30 text-[10px] text-muted-foreground whitespace-nowrap overflow-x-auto">
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
    case "verification_report":
      // 仅渲染历史数据；自动验收已删除，新会话不会再追加此块
      return <VerificationCard verdict={block.verdict} confidence={block.confidence} checks={block.checks} issues={block.issues} mode={block.mode} />;
    case "config_error":
      return <ConfigErrorCard items={block.items} />;
    case "staging_hint":
      return <StagingHintCard pendingCount={block.pendingCount} files={block.files} />;
    case "failure_guidance":
      return (
        <FailureGuidanceCard
          category={block.category}
          code={block.code}
          title={block.title}
          message={block.message}
          stage={block.stage}
          retryable={block.retryable}
          diagnosticId={block.diagnosticId}
          actions={block.actions}
          provider={block.provider}
          model={block.model}
          onRetry={onRetry}
          onRetryWithModel={onRetryWithModel}
        />
      );
    case "llm_retry": {
      if (block.retryStatus === "retrying") {
        return (
          <div className="flex items-center gap-2 my-2 px-3 py-2 rounded-lg border border-amber-500/30 bg-amber-500/5 text-sm text-amber-700 dark:text-amber-400 animate-pulse">
            <Repeat className="h-4 w-4 flex-shrink-0 animate-spin" style={{ animationDuration: "2s" }} />
            <div className="flex flex-col gap-0.5">
              <span className="font-medium">
                模型服务暂时不可用，正在第 {block.retryAttempt}/{block.retryMaxAttempts - 1} 次重试...
              </span>
              {block.retryErrorMessage && (
                <span className="text-xs text-amber-600/70 dark:text-amber-500/70 truncate max-w-md">
                  {block.retryErrorMessage}
                </span>
              )}
            </div>
          </div>
        );
      }
      if (block.retryStatus === "succeeded") {
        return (
          <div className="flex items-center gap-2 my-2 px-3 py-1.5 rounded-lg border border-emerald-500/30 bg-emerald-500/5 text-xs text-emerald-700 dark:text-emerald-400">
            <CheckCircle2 className="h-3.5 w-3.5 flex-shrink-0" />
            <span>模型服务已恢复，第 {block.retryAttempt} 次尝试成功</span>
          </div>
        );
      }
      if (block.retryStatus === "exhausted") {
        return (
          <div className="flex items-center gap-2 my-2 px-3 py-2 rounded-lg border border-red-500/30 bg-red-500/5 text-sm text-red-700 dark:text-red-400">
            <XCircle className="h-4 w-4 flex-shrink-0" />
            <div className="flex flex-col gap-0.5">
              <span className="font-medium">模型服务持续不可用，已重试 {block.retryAttempt} 次</span>
              {block.retryErrorMessage && (
                <span className="text-xs text-red-600/70 dark:text-red-500/70 truncate max-w-md">
                  {block.retryErrorMessage}
                </span>
              )}
            </div>
          </div>
        );
      }
      return null;
    }
    case "tool_notice":
      return (
        <div className="flex items-center gap-2 my-1 px-1 py-1 text-xs text-muted-foreground">
          <Wrench className="h-3.5 w-3.5 flex-shrink-0" />
          <span className="truncate" title={block.argsSummary}>{block.argsSummary}</span>
        </div>
      );
    case "reasoning_notice":
      return <ThinkingBlock content={block.content} title="推理过程" />;
    default:
      return null;
  }
});
