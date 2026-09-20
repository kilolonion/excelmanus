"use client";

import { CheckCircle2, ChevronsUpDown, CircleStop, Info, Repeat, Wrench, XCircle, Zap } from "lucide-react";
import { isHiddenAssistantChrome } from "@/lib/assistant-chrome";
import { ThinkingBlock } from "../ThinkingBlock";
import { ToolCallCard, ToolCallCancelButton } from "../ToolCallCard";
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

export interface AssistantBlockRendererProps {
  block: AssistantBlock;
  blockIndex: number;
  messageId: string;
  isThinkingActive?: boolean;
  isStreamingText?: boolean;
  showCollapseButton?: boolean;
  onCollapse?: () => void;
  defaultExpanded?: boolean;
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
          <><AskUserCard
            args={block.args}
            status={block.status}
            result={block.result}
          /><ToolCallCancelButton key={block.executionId} executionId={block.executionId} executionState={block.executionState} /></>
        );
      }
      return (
        <ToolCallCard
          toolCallId={block.toolCallId}
          executionId={block.executionId}
          executionState={block.executionState}
          name={block.name}
          args={block.args}
          status={block.status}
          result={block.result}
          error={block.error}
          parentCallId={block.parentCallId}
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
          stopReason={block.stopReason}
          diagnostic={block.diagnostic}
          background={block.background}
          runStatus={block.runStatus}
        />
      );
    case "task_list":
      return <TaskList items={block.items} />;
    case "iteration":
      return null;
    case "status": {
      const isStopped = block.label === "对话已停止" || block.label === "Conversation Stopped";
      if (isStopped) {
        return (
          <div className="my-1.5 flex items-center gap-2.5">
            <CircleStop className="h-4 w-4 flex-shrink-0 text-muted-foreground" />
            <span className="text-[13px] font-medium text-foreground">对话已停止</span>
            {block.detail && (
              <span className="text-[12px] text-muted-foreground">{block.detail === "Generation was manually stopped by the user." ? "已手动停止生成" : block.detail}</span>
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
          <div className="my-1.5 flex items-start gap-2.5">
            <Repeat className="h-4 w-4 flex-shrink-0 text-muted-foreground animate-spin mt-0.5" style={{ animationDuration: "2s" }} />
            <div className="min-w-0">
              <p className="text-[13px] font-medium text-foreground">
                正在重试模型请求
                <span className="ml-2 text-[11px] font-medium text-muted-foreground">
                  {block.retryAttempt}/{Math.max(block.retryMaxAttempts - 1, 1)}
                </span>
              </p>
              {block.retryErrorMessage && (
                <p className="text-[12px] text-muted-foreground mt-0.5 truncate">{block.retryErrorMessage}</p>
              )}
            </div>
          </div>
        );
      }
      if (block.retryStatus === "succeeded") {
        return (
          <div className="my-1.5 flex items-center gap-2.5">
            <CheckCircle2 className="h-4 w-4 flex-shrink-0 text-[var(--em-primary)]" />
            <span className="text-[13px] font-medium text-foreground">模型服务已恢复</span>
            <span className="text-[12px] text-muted-foreground">第 {block.retryAttempt} 次尝试成功</span>
          </div>
        );
      }
      if (block.retryStatus === "exhausted") {
        return (
          <div className="my-1.5 flex items-start gap-2.5">
            <XCircle className="h-4 w-4 flex-shrink-0 text-red-500 mt-0.5" />
            <div className="min-w-0">
              <p className="text-[13px] font-medium text-foreground">模型服务持续不可用</p>
              <p className="text-[12px] text-muted-foreground mt-0.5">
                已重试 {block.retryAttempt} 次
                {block.retryErrorMessage ? ` · ${block.retryErrorMessage}` : ""}
              </p>
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
