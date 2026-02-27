"use client";

import { useRef, useEffect, useLayoutEffect, useCallback, useState, useMemo } from "react";
import { useVirtualizer } from "@tanstack/react-virtual";
import { motion } from "framer-motion";
import { ScrollArea } from "@/components/ui/scroll-area";
import { UserMessage } from "./UserMessage";
import { AssistantMessage } from "./AssistantMessage";
import { RollbackConfirmDialog, getRollbackFilePreference } from "./RollbackConfirmDialog";
import { messageEnterVariants } from "@/lib/sidebar-motion";
import { useChatStore } from "@/stores/chat-store";
import type { Message, FileAttachment } from "@/lib/types";

interface MessageStreamProps {
  messages: Message[];
  isStreaming: boolean;
  onEditAndResend?: (messageId: string, newContent: string, rollbackFiles: boolean, files?: File[], retainedFiles?: FileAttachment[]) => void;
  onRetry?: (assistantMessageId: string) => void;
  onRetryWithModel?: (assistantMessageId: string, modelName: string) => void;
}

const TIMESTAMP_GAP_MS = 5 * 60 * 1000; // 5 分钟

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
  // 首条消息若有时间戳则始终显示
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

export function MessageStream({ messages, isStreaming, onEditAndResend, onRetry, onRetryWithModel }: MessageStreamProps) {
  const viewportRef = useRef<HTMLDivElement>(null);
  const [autoScroll, setAutoScroll] = useState(true);
  const renderedIdsRef = useRef(new Set<string>());
  // 跟踪是否完成了初始加载的滚动定位（用于跳过入场动画）
  const initialScrollDoneRef = useRef(false);
  // 添加一个 ref 来跟踪上一次的消息数量
  const prevMessageCountRef = useRef(0);
  // 定位期间屏蔽 handleScroll，防止 scroll 事件触发 setState 风暴
  const positioningRef = useRef(false);

  const currentSessionId = useChatStore((s) => s.currentSessionId);

  const [rollbackDialog, setRollbackDialog] = useState<{
    open: boolean;
    messageId: string;
    newContent: string;
    turnIndex: number;
    files?: File[];
    retainedFiles?: FileAttachment[];
  }>({ open: false, messageId: "", newContent: "", turnIndex: 0 });

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

  // ── 初次加载定位 ──────────────────────────────────────────────
  // 关键发现：virtualizer.scrollToIndex() 在 layoutEffect 中是 **no-op**，
  // 因为 virtualizer 内部的 scrollElement 在 render 阶段缓存了 null
  // （viewportRef 在 commit 后才设置），要到 useEffect 才重新读取。
  //
  // 因此 layoutEffect 中只能用 **原生 scrollTop**（viewportRef commit 后可用）。
  // 首帧 paint：viewport 在底部，virtualizer 渲染的顶部 items 被滚出可视区。
  // useEffect 帧：virtualizer 初始化 → 检测到 scrollTop 在底部 → 渲染底部 items。
  // rAF 帧：精确修正 + 退出定位模式。
  //
  // positioningRef 屏蔽 handleScroll，避免 setState 风暴。
  useIsomorphicLayoutEffect(() => {
    const currentCount = messages.length;
    const prevCount = prevMessageCountRef.current;

    if (currentCount === 0) {
      initialScrollDoneRef.current = false;
      positioningRef.current = false;
      prevMessageCountRef.current = 0;
      return;
    }

    if (prevCount === 0 && currentCount > 0) {
      // 初次加载（刷新恢复 / 会话切换后消息到达）
      for (const msg of messages) {
        renderedIdsRef.current.add(msg.id);
      }

      positioningRef.current = true;
      initialScrollDoneRef.current = true;
      prevMessageCountRef.current = currentCount;

      // ① 原生 scrollTop 定位（layoutEffect 中唯一可靠的方式）
      // viewportRef 在 React commit 后已指向真实 DOM 元素
      const vp = viewportRef.current;
      if (vp) {
        vp.scrollTop = vp.scrollHeight;
      }

      // ② rAF：virtualizer 已在 useEffect 中完成初始化，
      //    此时 scrollToIndex 可正常工作，做精确修正
      requestAnimationFrame(() => {
        virtualizer.scrollToIndex(currentCount - 1, { align: "end" });
        const viewport = viewportRef.current;
        if (viewport) viewport.scrollTop = viewport.scrollHeight;
        positioningRef.current = false;
      });
    }
    // 非初次加载场景不更新 prevMessageCountRef —— 交给下面的 useEffect
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [messages.length, virtualizer]);

  // ── 新消息追加时的滚动 ────────────────────────────────────────
  useEffect(() => {
    const currentMessageCount = messages.length;
    const prevCount = prevMessageCountRef.current;

    if (currentMessageCount === 0) {
      prevMessageCountRef.current = 0;
      return;
    }

    // 初始定位由 layoutEffect 处理，这里跳过
    if (!initialScrollDoneRef.current) return;

    if (currentMessageCount > prevCount) {
      scrollToBottom(false);
    }
    prevMessageCountRef.current = currentMessageCount;
  }, [messages.length, scrollToBottom]);

  const handleScroll = useCallback((event: React.UIEvent<HTMLDivElement>) => {
    // 定位期间屏蔽，防止 scroll 事件触发 setAutoScroll → re-render 风暴
    if (positioningRef.current) return;
    const container = event.currentTarget;
    const { scrollTop, scrollHeight, clientHeight } = container;
    const isAtBottom = scrollHeight - scrollTop - clientHeight < 100;
    setAutoScroll(isAtBottom);
  }, []);

  const handleEditAndResend = useCallback(
    (messageId: string, newContent: string, files?: File[], retainedFiles?: FileAttachment[]) => {
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
        onEditAndResend(messageId, newContent, false, files, retainedFiles);
        return;
      }

      const pref = getRollbackFilePreference();
      if (pref !== null) {
        onEditAndResend(messageId, newContent, pref === "always_rollback", files, retainedFiles);
        return;
      }

      // 计算 turnIndex（第几个 user 消息）
      let turnIdx = 0;
      for (let i = 0; i < msgIndex; i++) {
        if (messages[i].role === "user") turnIdx++;
      }
      setRollbackDialog({ open: true, messageId, newContent, turnIndex: turnIdx, files, retainedFiles });
    },
    [onEditAndResend, messages]
  );

  const handleRollbackConfirm = useCallback(
    (rollbackFiles: boolean) => {
      const pendingFiles = rollbackDialog.files;
      const pendingRetained = rollbackDialog.retainedFiles;
      setRollbackDialog({ open: false, messageId: "", newContent: "", turnIndex: 0 });
      if (onEditAndResend) {
        onEditAndResend(rollbackDialog.messageId, rollbackDialog.newContent, rollbackFiles, pendingFiles, pendingRetained);
      }
    },
    [onEditAndResend, rollbackDialog.messageId, rollbackDialog.newContent, rollbackDialog.files, rollbackDialog.retainedFiles]
  );

  const handleRollbackCancel = useCallback(() => {
    setRollbackDialog({ open: false, messageId: "", newContent: "", turnIndex: 0 });
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
                          ? (newContent: string, files?: File[], retainedFiles?: FileAttachment[]) => handleEditAndResend(message.id, newContent, files, retainedFiles)
                          : undefined
                      }
                    />
                  ) : (
                    <AssistantMessage
                      messageId={message.id}
                      blocks={message.blocks}
                      affectedFiles={message.affectedFiles}
                      isLastMessage={isLast}
                      onRetry={onRetry ? () => onRetry(message.id) : undefined}
                      onRetryWithModel={onRetryWithModel ? (model: string) => onRetryWithModel(message.id, model) : undefined}
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
        sessionId={currentSessionId}
        turnIndex={rollbackDialog.turnIndex}
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
