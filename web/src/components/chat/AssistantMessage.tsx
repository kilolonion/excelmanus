"use client";

import { FileSpreadsheet } from "lucide-react";
import { RelatedFilesCard } from "./FileCapsule";
import { openWorkspaceFile } from "@/lib/open-workspace-file";
import { MessageActions } from "./MessageActions";
import { AssistantBlockRenderer } from "./assistant-blocks/AssistantBlockRenderer";
import { ActivityGroup, type ActivityToolItem } from "./ActivityGroup";
import { AssistantWaitingIndicator } from "./AssistantWaitingIndicator";
import { JevInlineRail } from "./JevTimeline";
import { useChatStore } from "@/stores/chat-store";
import { useSessionStore } from "@/stores/session-store";
import { useUIStore } from "@/stores/ui-store";
import { downloadFile } from "@/lib/api";
import { displayFileName, mergeAffectedFiles, toPublicFileIdentity } from "@/lib/file-identity";
import { getAssistantLeadingSurface, isHiddenAssistantChrome } from "@/lib/assistant-chrome";
import { shouldRenderFailureGuidance } from "@/lib/failure-recovery";
import { cn } from "@/lib/utils";
import type { AssistantBlock } from "@/lib/types";
import React, { useMemo } from "react";

const TAIL_BLOCK_TYPES = new Set<AssistantBlock["type"]>(["text", "token_stats"]);

function splitToolChain(blocks: AssistantBlock[]): {
  chainBlocks: { block: AssistantBlock; origIndex: number }[];
  tailBlocks: { block: AssistantBlock; origIndex: number }[];
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

  return { chainBlocks, tailBlocks };
}

function isGroupedTool(block: AssistantBlock): block is Extract<AssistantBlock, { type: "tool_call" }> {
  return block.type === "tool_call" && block.name !== "ask_user" && block.name !== "suggest_mode_switch";
}

type ChainSegment =
  | { kind: "tools"; tools: ActivityToolItem[] }
  | { kind: "block"; block: AssistantBlock; origIndex: number };

function segmentChain(
  chainBlocks: { block: AssistantBlock; origIndex: number }[],
): ChainSegment[] {
  const segments: ChainSegment[] = [];
  let tools: ActivityToolItem[] = [];
  const flush = () => {
    if (tools.length > 0) {
      segments.push({ kind: "tools", tools });
      tools = [];
    }
  };
  for (const item of chainBlocks) {
    if (isHiddenAssistantChrome(item.block)) continue;
    if (isGroupedTool(item.block)) {
      tools.push({ block: item.block, origIndex: item.origIndex });
    } else {
      flush();
      segments.push({ kind: "block", block: item.block, origIndex: item.origIndex });
    }
  }
  flush();
  return segments;
}

function toolsComplete(tools: ActivityToolItem[]): boolean {
  return tools.every((t) => t.block.status === "success" || t.block.status === "error");
}

interface AssistantMessageProps {
  messageId: string;
  blocks: AssistantBlock[];
  affectedFiles?: string[];
  isLastMessage?: boolean;
  timestamp?: number;
  onRetry?: () => void;
  onRetryWithModel?: (modelName: string) => void;
}

function formatClock(ts?: number): string | null {
  if (!ts) return null;
  return new Date(ts).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" });
}

export const AssistantMessage = React.memo(function AssistantMessage({
  messageId, blocks, affectedFiles, isLastMessage, timestamp, onRetry, onRetryWithModel,
}: AssistantMessageProps) {
  const isStreaming = useChatStore((s) => (isLastMessage ? s.isStreaming : false));

  const { chainBlocks, tailBlocks } = useMemo(
    () => splitToolChain(blocks),
    [blocks],
  );
  const segments = useMemo(() => segmentChain(chainBlocks), [chainBlocks]);

  const skipIndices = useMemo(() => {
    const skip = new Set<number>();
    for (let i = 1; i < blocks.length; i++) {
      const cur = blocks[i];
      const prev = blocks[i - 1];
      if (
        cur.type === "verification_report" &&
        prev.type === "subagent" &&
        prev.name === "verifier"
      ) {
        skip.add(i);
      }
    }
    return skip;
  }, [blocks]);

  const lastBlockIdx = blocks.length - 1;
  const lastBlock = lastBlockIdx >= 0 ? blocks[lastBlockIdx] : null;
  const isThinkingActive =
    isStreaming
    && lastBlock?.type === "thinking"
    && lastBlock.startedAt != null
    && lastBlock.duration == null;
  const clock = formatClock(timestamp);
  const leadingSurface = useMemo(
    () => getAssistantLeadingSurface(blocks, isStreaming),
    [blocks, isStreaming],
  );
  const showWaiting = leadingSurface === "waiting";
  const isBubbleLead = leadingSurface === "bubble";

  const hideFailure = !shouldRenderFailureGuidance(isLastMessage, blocks);
  const hasVisibleContent = blocks.some((block, i) => {
    if (skipIndices.has(i) || isHiddenAssistantChrome(block)) return false;
    if (hideFailure && block.type === "failure_guidance") return false;
    if (block.type === "text" && !block.content.trim()) return false;
    return true;
  });
  if (!hasVisibleContent && !showWaiting && !(affectedFiles && affectedFiles.length > 0) && !isLastMessage) {
    return null;
  }

  const renderBlock = (block: AssistantBlock, origIndex: number) => (
    <AssistantBlockRenderer
      key={origIndex}
      block={block}
      blockIndex={origIndex}
      messageId={messageId}
      isThinkingActive={block.type === "thinking" && origIndex === lastBlockIdx && isThinkingActive}
      isStreamingText={isStreaming && block.type === "text" && origIndex === lastBlockIdx}
      skipRender={skipIndices.has(origIndex) || (hideFailure && block.type === "failure_guidance")}
      onRetry={onRetry}
      onRetryWithModel={onRetryWithModel}
    />
  );

  return (
    <div className="group/msg flex gap-2 sm:gap-2.5 py-2.5">
      <div
        className={cn(
          "flex-shrink-0 h-6 w-6 rounded-full flex items-center justify-center text-white text-[10px]",
          isBubbleLead ? "mt-1.5" : showWaiting ? "mt-0" : "mt-0.5",
          showWaiting && "assistant-avatar-wait",
        )}
        style={{ backgroundColor: "var(--em-accent)" }}
      >
        <FileSpreadsheet className="h-3.5 w-3.5" />
      </div>
      <div className="flex-1 min-w-0">
        <div className="flex items-start gap-2">
          <div className="assistant-msg-body min-w-0 flex-1">
            {showWaiting && <AssistantWaitingIndicator />}
            {isLastMessage && <JevInlineRail />}
            {segments.map((seg, i) => {
              if (seg.kind === "tools") {
                return (
                  <ActivityGroup
                    key={`tools-${seg.tools[0]?.origIndex ?? i}`}
                    tools={seg.tools}
                    defaultCollapsed={!isLastMessage && toolsComplete(seg.tools)}
                  />
                );
              }
              return renderBlock(seg.block, seg.origIndex);
            })}
            {tailBlocks.map(({ block, origIndex }) => {
              if (block.type === "text" && !block.content.trim()) return null;
              return (
                <AssistantBlockRenderer
                  key={origIndex}
                  block={block}
                  blockIndex={origIndex}
                  messageId={messageId}
                  isThinkingActive={block.type === "thinking" && origIndex === lastBlockIdx && isThinkingActive}
                  isStreamingText={isStreaming && block.type === "text" && origIndex === lastBlockIdx}
                  defaultExpanded={block.type === "text"}
                  skipRender={skipIndices.has(origIndex) || (hideFailure && block.type === "failure_guidance")}
                  onRetry={onRetry}
                  onRetryWithModel={onRetryWithModel}
                />
              );
            })}
          </div>
          {clock && (
            <span
              className={cn(
                "text-[11px] text-muted-foreground/70 flex-shrink-0 tabular-nums leading-none min-w-[2.75rem] justify-end",
                isBubbleLead ? "flex h-9 items-center" : "flex h-6 items-center",
              )}
            >
              {clock}
            </span>
          )}
        </div>

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
  const setSidebarTab = useUIStore((s) => s.setSidebarTab);
  const activeSessionId = useSessionStore((s) => s.activeSessionId);

  const validFiles = useMemo(
    () => mergeAffectedFiles([], files).filter(isPlausibleFilePath),
    [files],
  );

  const items = useMemo(
    () =>
      validFiles.map((filePath) => {
        const identity = toPublicFileIdentity(filePath) || filePath;
        const filename = displayFileName(identity) || filePath.split("/").pop() || filePath;
        return {
          key: identity,
          filename,
          filePath: identity,
          onOpen: () => {
            openWorkspaceFile(identity);
          },
          onDownload: () => {
            downloadFile(filePath, filename, activeSessionId ?? undefined).catch(() => {});
          },
        };
      }),
    [validFiles, activeSessionId],
  );

  if (items.length === 0) return null;

  return (
    <RelatedFilesCard
      files={items}
      onReview={() => setSidebarTab("files")}
    />
  );
}
