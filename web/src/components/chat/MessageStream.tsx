"use client";

import { useRef, useEffect, useLayoutEffect, useCallback, useState, useMemo } from "react";
import { useVirtualizer } from "@tanstack/react-virtual";
import { motion } from "framer-motion";
import { ScrollArea } from "@/components/ui/scroll-area";
import { UserMessage } from "./UserMessage";
import { AssistantMessage } from "./AssistantMessage";
import { RollbackConfirmDialog, getRollbackFilePreference } from "./RollbackConfirmDialog";
import { messageEnterVariants } from "@/lib/sidebar-motion";
import type { Message } from "@/lib/types";

interface MessageStreamProps {
  messages: Message[];
  isStreaming: boolean;
  onEditAndResend?: (messageId: string, newContent: string, rollbackFiles: boolean) => void;
}

const TIMESTAMP_GAP_MS = 5 * 60 * 1000; // 5 minutes

function formatTimestamp(ts: number): string {
  const now = Date.now();
  const diff = now - ts;
  const date = new Date(ts);
  const today = new Date();
  const isToday =
    date.getDate() === today.getDate() &&
    date.getMonth() === today.getMonth() &&
    date.getFullYear() === today.getFullYear();
  const time = date.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" });

  if (diff < 60_000) return "\u521a\u521a";
  if (diff < 3600_000) return `${Math.floor(diff / 60_000)} \u5206\u949f\u524d`;
  if (isToday) return time;
  const yesterday = new Date(today);
  yesterday.setDate(yesterday.getDate() - 1);
  const isYesterday =
    date.getDate() === yesterday.getDate() &&
    date.getMonth() === yesterday.getMonth() &&
    date.getFullYear() === yesterday.getFullYear();
  if (isYesterday) return `\u6628\u5929 ${time}`;
  return `${date.getMonth() + 1}/${date.getDate()} ${time}`;
}

/** Indices of messages that should show a timestamp separator above them. */
function computeTimestampIndices(messages: Message[]): Set<number> {
  const indices = new Set<number>();
  if (messages.length === 0) return indices;
  // Always show timestamp on the first message if it has one
  if (messages[0].timestamp) indices.add(0);
  for (let i = 1; i < messages.length; i++) {
    const prev = messages[i - 1].timestamp;
    const curr = messages[i].timestamp;
    if (prev && curr && curr - prev >= TIMESTAMP_GAP_MS) {
      indices.add(i);
    }
  }
  return indices;
}

export function MessageStream({ messages, isStreaming, onEditAndResend }: MessageStreamProps) {
  const viewportRef = useRef<HTMLDivElement>(null);
  const [autoScroll, setAutoScroll] = useState(true);
  const renderedIdsRef = useRef(new Set<string>());
  // 跟踪是否完成了初始加载的滚动定位（用于跳过入场动画）
  const initialScrollDoneRef = useRef(false);
  // 添加一个 ref 来跟踪上一次的消息数量
  const prevMessageCountRef = useRef(0);
  // 标记初始加载是否已完成定位（用于移动端兜底）
  const [initialLoadReady, setInitialLoadReady] = useState(false);

  const [rollbackDialog, setRollbackDialog] = useState<{
    open: boolean;
    messageId: string;
    newContent: string;
  }>({ open: false, messageId: "", newContent: "" });

  const virtualizer = useVirtualizer({
    count: messages.length,
    getScrollElement: () => viewportRef.current,
    estimateSize: (index) => estimateMessageSize(messages[index]),
    overscan: 5,
    paddingStart: 24,
    paddingEnd: 24,
  });

  const scrollToBottom = useCallback((immediate = false) => {
    if (!autoScroll) return;

    if (immediate) {
      // 使用虚拟化器的 scrollToIndex 直接定位到最后一条消息
      // 这比手动设置 scrollTop 更可靠，因为虚拟化器知道精确的偏移量
      virtualizer.scrollToIndex(messages.length - 1, { align: "end" });
    } else {
      // 使用 requestAnimationFrame 确保在下一帧执行
      requestAnimationFrame(() => {
        const viewport = viewportRef.current;
        if (!viewport) return;
        viewport.scrollTo({
          top: viewport.scrollHeight,
          behavior: isStreaming ? "auto" : "smooth",
        });
      });
    }
  }, [autoScroll, isStreaming, virtualizer, messages.length]);

  // 强制滚动到底部（同时使用 scrollToIndex + 原生 scrollTop 双保险）
  const forceScrollToEnd = useCallback(() => {
    if (messages.length === 0) return;
    virtualizer.scrollToIndex(messages.length - 1, { align: "end" });
    // 同时用原生方式兜底（移动端 Safari 等场景下 scrollToIndex 可能不生效）
    const viewport = viewportRef.current;
    if (viewport) {
      viewport.scrollTop = viewport.scrollHeight;
    }
  }, [virtualizer, messages.length]);

  // SSR 安全的 useLayoutEffect
  const useIsomorphicLayoutEffect = typeof window !== "undefined" ? useLayoutEffect : useEffect;

  // 初次加载定位：在浏览器 paint 之前同步执行，用户永远看不到从顶部滚下来的过程
  useIsomorphicLayoutEffect(() => {
    const currentMessageCount = messages.length;
    const prevMessageCount = prevMessageCountRef.current;

    prevMessageCountRef.current = currentMessageCount;

    if (currentMessageCount === 0) {
      initialScrollDoneRef.current = false;
      setInitialLoadReady(false);
      return;
    }

    if (prevMessageCount === 0 && currentMessageCount > 0) {
      // 初次加载（刷新恢复 / 会话切换后消息到达）
      // 标记所有已有消息为"已渲染"，跳过入场动画
      for (const msg of messages) {
        renderedIdsRef.current.add(msg.id);
      }

      // 在 paint 之前同步定位到底部
      // useLayoutEffect 保证此处执行时 DOM 已更新但浏览器尚未绘制
      forceScrollToEnd();
      initialScrollDoneRef.current = true;
      setInitialLoadReady(true);

      // 一次 rAF 修正：虚拟化器首次渲染使用 estimateSize，
      // 实际 measureElement 回调后位置可能有微小偏差
      requestAnimationFrame(() => {
        forceScrollToEnd();
      });

      return;
    }
  }, [messages, forceScrollToEnd]);

  // 新消息追加时的滚动（独立 effect，仅处理增量场景）
  useEffect(() => {
    const currentMessageCount = messages.length;
    // 仅在非初次加载且消息增加时触发
    if (initialScrollDoneRef.current && currentMessageCount > prevMessageCountRef.current) {
      scrollToBottom(false);
    }
    // 注意：prevMessageCountRef 由上面的 layoutEffect 维护，这里不要重复更新
  }, [messages.length, scrollToBottom]);

  const handleScroll = useCallback((event: React.UIEvent<HTMLDivElement>) => {
    const container = event.currentTarget;
    const { scrollTop, scrollHeight, clientHeight } = container;
    const isAtBottom = scrollHeight - scrollTop - clientHeight < 100;
    setAutoScroll(isAtBottom);
  }, []);

  const handleEditAndResend = useCallback(
    (messageId: string, newContent: string) => {
      if (!onEditAndResend) return;

      const msgIndex = messages.findIndex((m) => m.id === messageId);
      let hasFileChanges = false;
      if (msgIndex !== -1) {
        for (let i = msgIndex + 1; i < messages.length; i++) {
          const m = messages[i];
          if (m.role === "assistant" && m.blocks.some((b) => b.type === "tool_call")) {
            hasFileChanges = true;
            break;
          }
        }
      }

      if (!hasFileChanges) {
        onEditAndResend(messageId, newContent, false);
        return;
      }

      const pref = getRollbackFilePreference();
      if (pref !== null) {
        onEditAndResend(messageId, newContent, pref === "always_rollback");
        return;
      }

      setRollbackDialog({ open: true, messageId, newContent });
    },
    [onEditAndResend, messages]
  );

  const handleRollbackConfirm = useCallback(
    (rollbackFiles: boolean) => {
      setRollbackDialog({ open: false, messageId: "", newContent: "" });
      if (onEditAndResend) {
        onEditAndResend(rollbackDialog.messageId, rollbackDialog.newContent, rollbackFiles);
      }
    },
    [onEditAndResend, rollbackDialog.messageId, rollbackDialog.newContent]
  );

  const handleRollbackCancel = useCallback(() => {
    setRollbackDialog({ open: false, messageId: "", newContent: "" });
  }, []);

  const timestampIndices = useMemo(
    () => computeTimestampIndices(messages),
    [messages],
  );

  const virtualItems = virtualizer.getVirtualItems();
  const lastMsgIndex = messages.length - 1;

  return (
    <>
      <ScrollArea
        className="flex-1 min-h-0"
        viewportRef={viewportRef}
        onViewportScroll={handleScroll}
      >
        <div
          style={{
            height: virtualizer.getTotalSize(),
            position: "relative",
            width: "100%",
            // useLayoutEffect 在 paint 前完成定位，通常不需要隐藏
            // 保留作为极端情况兜底（如 SSR hydration 延迟）
            opacity: (messages.length === 0 || initialLoadReady) ? 1 : 0,
          }}
        >
          {virtualItems.map((virtualRow) => {
            const message = messages[virtualRow.index];
            const isNew = !renderedIdsRef.current.has(message.id);
            if (isNew) renderedIdsRef.current.add(message.id);
            const isLast = virtualRow.index === lastMsgIndex;

            return (
              <div
                key={message.id}
                ref={virtualizer.measureElement}
                data-index={virtualRow.index}
                style={{
                  position: "absolute",
                  top: 0,
                  left: 0,
                  width: "100%",
                  transform: `translateY(${virtualRow.start}px)`,
                }}
              >
                {/* Smart timestamp separator */}
                {message.timestamp && timestampIndices.has(virtualRow.index) && (
                  <div className="flex items-center justify-center py-1 max-w-3xl mx-auto px-3 sm:px-4">
                    <span className="text-[10px] text-muted-foreground/60 select-none">
                      {formatTimestamp(message.timestamp)}
                    </span>
                  </div>
                )}

                <motion.div
                  className="max-w-3xl mx-auto px-3 sm:px-4"
                  variants={messageEnterVariants}
                  initial={isNew ? "initial" : false}
                  animate="animate"
                >
                  {message.role === "user" ? (
                    <UserMessage
                      content={message.content}
                      files={message.files}
                      isStreaming={isStreaming}
                      onEditAndResend={
                        onEditAndResend
                          ? (newContent: string) => handleEditAndResend(message.id, newContent)
                          : undefined
                      }
                    />
                  ) : (
                    <AssistantMessage
                      messageId={message.id}
                      blocks={message.blocks}
                      affectedFiles={message.affectedFiles}
                      isLastMessage={isLast}
                    />
                  )}
                </motion.div>
              </div>
            );
          })}
        </div>
      </ScrollArea>

      <RollbackConfirmDialog
        open={rollbackDialog.open}
        onConfirm={handleRollbackConfirm}
        onCancel={handleRollbackCancel}
      />
    </>
  );
}

function estimateMessageSize(msg: Message): number {
  if (msg.role === "user") {
    const lineCount = (msg.content.match(/\n/g) || []).length + 1;
    // 改进：考虑文件附件的高度
    const fileHeight = (msg.files?.length || 0) * 32;
    return Math.max(72, Math.min(lineCount * 24 + 56 + fileHeight, 400));
  }

  const blocks = msg.blocks;
  let estimate = 64; // 基础消息容器高度

  for (const b of blocks) {
    switch (b.type) {
      case "text":
        // 改进：更精确的文本高度估算
        const lines = (b.content.match(/\n/g) || []).length + 1;
        const avgCharsPerLine = 80; // 假设每行平均字符数
        const estimatedLines = Math.max(lines, Math.ceil(b.content.length / avgCharsPerLine));
        estimate += Math.max(40, Math.min(estimatedLines * 20 + 16, 600));
        break;
      case "thinking":
        // 思考块通常是折叠的
        estimate += b.content ? 80 : 60;
        break;
      case "tool_call":
        // 工具调用块高度相对固定
        estimate += 100;
        break;
      case "token_stats":
        estimate += 40;
        break;
      case "status":
        estimate += 48;
        break;
      case "task_list":
        // 任务列表根据项目数量估算
        const taskCount = Array.isArray(b.items) ? b.items.length : 0;
        estimate += 60 + taskCount * 28;
        break;
      default:
        estimate += 48;
    }
  }

  // 考虑受影响文件列表的高度
  if (msg.affectedFiles && msg.affectedFiles.length > 0) {
    estimate += 40 + msg.affectedFiles.length * 24;
  }

  return Math.min(estimate, 2000); // 设置最大高度限制
}
