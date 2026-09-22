"use client";

import { memo, useMemo, useRef, useEffect, useLayoutEffect, useCallback, useState } from "react";
import { useVirtualizer } from "@tanstack/react-virtual";
import { motion, AnimatePresence } from "framer-motion";
import { ArrowDown } from "lucide-react";
import { ScrollArea } from "@/components/ui/scroll-area";
import { ErrorBoundary } from "@/components/ui/ErrorBoundary";
import { UserMessage } from "./UserMessage";
import { AssistantMessage } from "./AssistantMessage";
import { RollbackConfirmDialog } from "./RollbackConfirmDialog";
import { messageEnterVariants } from "@/lib/sidebar-motion";
import { useChatStore } from "@/stores/chat-store";
import type { Message, FileAttachment } from "@/lib/types";
import { isHiddenAssistantChrome } from "@/lib/assistant-chrome";
import { stripInjectedUserPromptBlocks } from "@/lib/injected-user-prompt";

interface MessageStreamProps {
  isStreaming: boolean;
  onEditAndResend?: (messageId: string, newContent: string, files?: File[], retainedFiles?: FileAttachment[]) => void;
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
function computeTimestampIndices(
  messageOrder: string[],
  messagesById: Record<string, Message>,
): Set<number> {
  const indices = new Set<number>();
  if (messageOrder.length === 0) return indices;
  // 首条消息若有时间戳则始终显示
  const first = messagesById[messageOrder[0]];
  if (first?.timestamp) indices.add(0);
  for (let i = 1; i < messageOrder.length; i++) {
    const prev = messagesById[messageOrder[i - 1]]?.timestamp;
    const curr = messagesById[messageOrder[i]]?.timestamp;
    if (prev && curr && curr - prev >= TIMESTAMP_GAP_MS) {
      indices.add(i);
    }
  }
  return indices;
}

export function MessageStream({ isStreaming, onEditAndResend, onRetry, onRetryWithModel }: MessageStreamProps) {
  const viewportRef = useRef<HTMLDivElement>(null);
  const pinnedMeasureRef = useRef<HTMLDivElement>(null);
  const sizeCacheRef = useRef(new Map<string, number>());
  const [autoScroll, setAutoScroll] = useState(true);
  // state 用于渲染按钮，ref 才是流式更新与滚动事件之间的同步真值。
  const autoScrollRef = useRef(true);
  const lastScrollTopRef = useRef(0);
  const renderedIdsRef = useRef(new Set<string>());
  // 跟踪是否完成了初始加载的滚动定位（用于跳过入场动画）
  const initialScrollDoneRef = useRef(false);
  // 添加一个 ref 来跟踪上一次的消息数量
  const prevMessageCountRef = useRef(0);
  // 定位期间屏蔽 handleScroll，防止 scroll 事件触发 setState 风暴
  const positioningRef = useRef(false);
  const prependAnchorRef = useRef<{ top: number; height: number } | null>(null);

  const loadedSessionId = useChatStore((s) => s.loadedSessionId);
  const messageOrder = useChatStore((s) => s.messageOrder);
  const hasMoreMessages = useChatStore((s) => s.hasMoreMessages);
  const isLoadingOlderMessages = useChatStore((s) => s.isLoadingOlderMessages);
  const loadOlderMessages = useChatStore((s) => s.loadOlderMessages);
  // 最新一条消息放在虚拟列表外的文档流里。绝对定位 + 估高会把打字机增量裁掉，
  // 看起来就像刷新后才出现完整回复。
  const pinnedId = messageOrder.length > 0 ? messageOrder[messageOrder.length - 1] : null;
  const virtualCount = Math.max(0, messageOrder.length - 1);
  const streamTick = useChatStore((s) => {
    if (!isStreaming) return 0;
    const id = s.messageOrder[s.messageOrder.length - 1];
    const msg = id ? s.messagesById[id] : undefined;
    if (!msg || msg.role !== "assistant") return s.messageOrder.length;
    return s.messageOrder.length * 1_000_000 + messageContentTick(msg);
  });

  // 会话切换时清空动画去重集合，防止长期只增不减导致内存泄漏
  useEffect(() => {
    renderedIdsRef.current = new Set<string>();
    sizeCacheRef.current = new Map();
    prependAnchorRef.current = null;
    autoScrollRef.current = true;
    lastScrollTopRef.current = 0;
    setAutoScroll(true);
  }, [loadedSessionId]);

  const [rollbackDialog, setRollbackDialog] = useState<{
    open: boolean;
    mode: "edit" | "retry";
    messageId: string;
    newContent: string;
    turnIndex: number;
    files?: File[];
    retainedFiles?: FileAttachment[];
    switchToModel?: string;
  }>({ open: false, mode: "edit", messageId: "", newContent: "", turnIndex: 0 });

  const timestampIndices = useMemo(() => computeTimestampIndices(
    messageOrder,
    useChatStore.getState().messagesById,
  ), [messageOrder]);

  const virtualizer = useVirtualizer({
    count: virtualCount,
    getScrollElement: () => viewportRef.current,
    getItemKey: (index) => messageOrder[index] ?? index,
    estimateSize: (index) => {
      const id = messageOrder[index];
      const cached = id ? sizeCacheRef.current.get(id) : undefined;
      if (cached) return cached;
      const base = estimateMessageSize(
        id ? useChatStore.getState().messagesById[id] : undefined,
      );
      return timestampIndices.has(index) ? base + 28 : base;
    },
    overscan: 5,
    paddingStart: 24,
    paddingEnd: 0,
  });

  const scrollViewportToEnd = useCallback((behavior: ScrollBehavior = "auto") => {
    const viewport = viewportRef.current;
    if (!viewport) return;
    viewport.scrollTo({ top: viewport.scrollHeight, behavior });
    if (behavior === "auto") lastScrollTopRef.current = viewport.scrollTop;
  }, []);

  const scrollToBottom = useCallback((immediate = false) => {
    if (!autoScrollRef.current) return;

    if (immediate) {
      scrollViewportToEnd("auto");
    } else {
      requestAnimationFrame(() => {
        scrollViewportToEnd(isStreaming ? "auto" : "smooth");
      });
    }
  }, [isStreaming, scrollViewportToEnd]);

  const forceScrollToEnd = useCallback(() => {
    if (messageOrder.length === 0) return;
    const viewport = viewportRef.current;
    if (viewport) {
      viewport.scrollTop = viewport.scrollHeight;
      lastScrollTopRef.current = viewport.scrollTop;
    }
  }, [messageOrder.length]);

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
    const currentCount = messageOrder.length;
    const prevCount = prevMessageCountRef.current;

    if (currentCount === 0) {
      initialScrollDoneRef.current = false;
      positioningRef.current = false;
      prevMessageCountRef.current = 0;
      return;
    }

    if (prevCount === 0 && currentCount > 0) {
      // 初次加载（刷新恢复 / 会话切换后消息到达）
      for (const id of messageOrder) {
        renderedIdsRef.current.add(id);
      }

      positioningRef.current = true;
      initialScrollDoneRef.current = true;
      prevMessageCountRef.current = currentCount;

      // ① 原生 scrollTop 定位（layoutEffect 中唯一可靠的方式）
      // viewportRef 在 React commit 后已指向真实 DOM 元素
      const vp = viewportRef.current;
      if (vp) {
        vp.scrollTop = vp.scrollHeight;
        lastScrollTopRef.current = vp.scrollTop;
      }

      // ② rAF：virtualizer 已在 useEffect 中完成初始化，
      //    此时 scrollToIndex 可正常工作，做精确修正
      requestAnimationFrame(() => {
        const viewport = viewportRef.current;
        if (viewport) {
          viewport.scrollTop = viewport.scrollHeight;
          lastScrollTopRef.current = viewport.scrollTop;
        }
        positioningRef.current = false;
      });
    }
    // 非初次加载场景不更新 prevMessageCountRef —— 交给下面的 useEffect
  }, [messageOrder.length, virtualizer]);

  // ── 新消息追加时的滚动 ────────────────────────────────────────
  useEffect(() => {
    const currentMessageCount = messageOrder.length;
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
  }, [messageOrder.length, scrollToBottom]);

  useIsomorphicLayoutEffect(() => {
    if (!isStreaming || !autoScrollRef.current || messageOrder.length === 0) return;
    // Only the pinned streaming message grows. ResizeObserver already measures
    // history rows; invalidating all their heights on every delta causes jumps
    // and repeated synchronous layout of the entire virtual history.
    const viewport = viewportRef.current;
    if (viewport) {
      viewport.scrollTop = viewport.scrollHeight;
      lastScrollTopRef.current = viewport.scrollTop;
    }
  }, [streamTick, isStreaming, virtualizer, messageOrder.length]);

  useIsomorphicLayoutEffect(() => {
    const el = pinnedMeasureRef.current;
    if (!el || !pinnedId) return;
    const write = () => {
      const h = el.getBoundingClientRect().height;
      if (h > 0) sizeCacheRef.current.set(pinnedId, h);
    };
    write();
    const ro = new ResizeObserver(write);
    ro.observe(el);
    return () => ro.disconnect();
  }, [pinnedId]);

  useIsomorphicLayoutEffect(() => {
    virtualizer.measure();
  }, [pinnedId, virtualCount, virtualizer]);

  // 保持用户正在阅读的位置：向上加载历史会增加 scrollHeight，不能把
  // 当前视口突然推开。
  useIsomorphicLayoutEffect(() => {
    const anchor = prependAnchorRef.current;
    if (!anchor || isLoadingOlderMessages) return;
    const viewport = viewportRef.current;
    if (viewport) {
      viewport.scrollTop = anchor.top + (viewport.scrollHeight - anchor.height);
      lastScrollTopRef.current = viewport.scrollTop;
    }
    prependAnchorRef.current = null;
  }, [messageOrder.length, isLoadingOlderMessages]);

  const handleScroll = useCallback((event: React.UIEvent<HTMLDivElement>) => {
    // 定位期间屏蔽，防止 scroll 事件触发 setAutoScroll → re-render 风暴
    if (positioningRef.current) return;
    const container = event.currentTarget;
    const { scrollTop, scrollHeight, clientHeight } = container;
    const userMovedUp = scrollTop < lastScrollTopRef.current - 1;
    const isAtBottom = scrollHeight - scrollTop - clientHeight <= 24;
    // 保留向下执行中的平滑自动滚动；任何向上移动都立即把控制权交给用户。
    const shouldAutoScroll = !userMovedUp && (autoScrollRef.current || isAtBottom);
    lastScrollTopRef.current = scrollTop;
    autoScrollRef.current = shouldAutoScroll;
    setAutoScroll(shouldAutoScroll);
    if (
      scrollTop < 180
      && hasMoreMessages
      && !isLoadingOlderMessages
      && !prependAnchorRef.current
    ) {
      prependAnchorRef.current = { top: scrollTop, height: container.scrollHeight };
      void loadOlderMessages().catch(() => {
        prependAnchorRef.current = null;
      });
    }
  }, [hasMoreMessages, isLoadingOlderMessages, loadOlderMessages]);

  const handleEditAndResend = useCallback(
    (messageId: string, newContent: string, files?: File[], retainedFiles?: FileAttachment[]) => {
      if (!onEditAndResend) return;
      const latestMessages = useChatStore.getState().messages;

      const msgIndex = latestMessages.findIndex((m) => m.id === messageId);
      let hasFileChanges = false;
      if (msgIndex !== -1) {
        for (let i = msgIndex + 1; i < latestMessages.length; i++) {
          const m = latestMessages[i];
          if (m.role === "assistant" && m.blocks.some((b) => b.type === "tool_call")) {
            hasFileChanges = true;
            break;
          }
        }
      }

      if (!hasFileChanges) {
        onEditAndResend(messageId, newContent, files, retainedFiles);
        return;
      }

      // 计算 turnIndex（第几个 user 消息）
      let turnIdx = 0;
      for (let i = 0; i < msgIndex; i++) {
        if (latestMessages[i].role === "user") turnIdx++;
      }
      setRollbackDialog({ open: true, mode: "edit", messageId, newContent, turnIndex: turnIdx, files, retainedFiles });
    },
    [onEditAndResend]
  );

  const handleRetry = useCallback(
    (assistantMessageId: string) => {
      if (!onRetry) return;
      const latestMessages = useChatStore.getState().messages;

      const assistantIdx = latestMessages.findIndex((m) => m.id === assistantMessageId);
      if (assistantIdx === -1) { onRetry(assistantMessageId); return; }

      // 找到该 assistant 前面最近的 user 消息
      let userIdx = -1;
      for (let i = assistantIdx - 1; i >= 0; i--) {
        if (latestMessages[i].role === "user") { userIdx = i; break; }
      }
      if (userIdx === -1) { onRetry(assistantMessageId); return; }

      // 检查该 user 消息之后是否有工具调用（文件变更）
      let hasFileChanges = false;
      for (let i = userIdx + 1; i < latestMessages.length; i++) {
        const m = latestMessages[i];
        if (m.role === "assistant" && m.blocks.some((b) => b.type === "tool_call")) {
          hasFileChanges = true;
          break;
        }
      }

      if (!hasFileChanges) { onRetry(assistantMessageId); return; }

      let turnIdx = 0;
      for (let i = 0; i < userIdx; i++) {
        if (latestMessages[i].role === "user") turnIdx++;
      }
      setRollbackDialog({ open: true, mode: "retry", messageId: assistantMessageId, newContent: "", turnIndex: turnIdx });
    },
    [onRetry]
  );

  const handleRetryWithModel = useCallback(
    (assistantMessageId: string, modelName: string) => {
      if (!onRetryWithModel) return;
      const latestMessages = useChatStore.getState().messages;

      const assistantIdx = latestMessages.findIndex((m) => m.id === assistantMessageId);
      if (assistantIdx === -1) { onRetryWithModel(assistantMessageId, modelName); return; }

      let userIdx = -1;
      for (let i = assistantIdx - 1; i >= 0; i--) {
        if (latestMessages[i].role === "user") { userIdx = i; break; }
      }
      if (userIdx === -1) { onRetryWithModel(assistantMessageId, modelName); return; }

      let hasFileChanges = false;
      for (let i = userIdx + 1; i < latestMessages.length; i++) {
        const m = latestMessages[i];
        if (m.role === "assistant" && m.blocks.some((b) => b.type === "tool_call")) {
          hasFileChanges = true;
          break;
        }
      }

      if (!hasFileChanges) { onRetryWithModel(assistantMessageId, modelName); return; }

      let turnIdx = 0;
      for (let i = 0; i < userIdx; i++) {
        if (latestMessages[i].role === "user") turnIdx++;
      }
      setRollbackDialog({ open: true, mode: "retry", messageId: assistantMessageId, newContent: "", turnIndex: turnIdx, switchToModel: modelName });
    },
    [onRetryWithModel]
  );

  const handleRollbackConfirm = useCallback(
    () => {
      const { mode, messageId, newContent, files, retainedFiles, switchToModel } = rollbackDialog;
      setRollbackDialog({ open: false, mode: "edit", messageId: "", newContent: "", turnIndex: 0 });
      if (mode === "edit") {
        if (onEditAndResend) {
          onEditAndResend(messageId, newContent, files, retainedFiles);
        }
      } else if (switchToModel && onRetryWithModel) {
        onRetryWithModel(messageId, switchToModel);
      } else if (onRetry) {
        onRetry(messageId);
      }
    },
    [onEditAndResend, onRetry, onRetryWithModel, rollbackDialog]
  );

  const handleRollbackCancel = useCallback(() => {
    setRollbackDialog({ open: false, mode: "edit", messageId: "", newContent: "", turnIndex: 0 });
  }, []);

  const virtualItems = virtualizer.getVirtualItems();
  const pinnedIndex = pinnedId ? messageOrder.length - 1 : -1;
  const pinnedIsNew = Boolean(pinnedId && !renderedIdsRef.current.has(pinnedId));
  if (pinnedId && pinnedIsNew) renderedIdsRef.current.add(pinnedId);
  const pinnedTimestamp = pinnedId
    ? useChatStore.getState().messagesById[pinnedId]?.timestamp
    : undefined;

  return (
    <div className="relative flex-1 min-h-0 flex flex-col">
      <ScrollArea
        className="flex-1 min-h-0"
        viewportRef={viewportRef}
        onViewportScroll={handleScroll}
      >
        {(hasMoreMessages || isLoadingOlderMessages) && (
          <div
            className="pointer-events-none sticky top-1 z-10 flex h-0 justify-center overflow-visible"
            aria-live="polite"
          >
            <span className="rounded-full border border-border/60 bg-background/90 px-3 py-1 text-[11px] text-muted-foreground shadow-sm">
              {isLoadingOlderMessages ? "正在加载更早消息…" : "上滑加载更早消息"}
            </span>
          </div>
        )}
        <div
          style={{
            height: virtualizer.getTotalSize(),
            position: "relative",
            width: "100%",
          }}
        >
          {virtualItems.map((virtualRow) => {
            const messageId = messageOrder[virtualRow.index];
            if (!messageId) return null;
            const isNew = !renderedIdsRef.current.has(messageId);
            if (isNew) renderedIdsRef.current.add(messageId);
            const timestamp = useChatStore.getState().messagesById[messageId]?.timestamp;

            return (
              <div
                key={messageId}
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
                {timestamp && timestampIndices.has(virtualRow.index) && (
                  <TimestampSeparator ts={timestamp} isNew={isNew} />
                )}

                <motion.div
                  className="max-w-4xl mx-auto px-3 sm:px-4"
                  variants={messageEnterVariants}
                  initial={isNew ? "initial" : false}
                  animate="animate"
                >
                  <ErrorBoundary
                    resetKey={messageId}
                    fallback={(error, reset) => <MessageRenderFallback error={error} onReset={reset} />}
                  >
                    <MessageRowItem
                      messageId={messageId}
                      isStreaming={isStreaming}
                      isLast={false}
                      onEditAndResend={onEditAndResend ? handleEditAndResend : undefined}
                      onRetry={onRetry ? handleRetry : undefined}
                      onRetryWithModel={onRetryWithModel ? handleRetryWithModel : undefined}
                    />
                  </ErrorBoundary>
                </motion.div>
              </div>
            );
          })}
        </div>

        {pinnedId && (
          <div className="relative w-full pb-6">
            {pinnedTimestamp && timestampIndices.has(pinnedIndex) && (
              <TimestampSeparator ts={pinnedTimestamp} isNew={pinnedIsNew} />
            )}
            <div ref={pinnedMeasureRef} className="max-w-4xl mx-auto px-3 sm:px-4">
            <motion.div
              variants={messageEnterVariants}
              initial={pinnedIsNew ? "initial" : false}
              animate="animate"
            >
              <ErrorBoundary
                resetKey={pinnedId}
                fallback={(error, reset) => <MessageRenderFallback error={error} onReset={reset} />}
              >
                <MessageRowItem
                  messageId={pinnedId}
                  isStreaming={isStreaming}
                  isLast
                  onEditAndResend={onEditAndResend ? handleEditAndResend : undefined}
                  onRetry={onRetry ? handleRetry : undefined}
                  onRetryWithModel={onRetryWithModel ? handleRetryWithModel : undefined}
                />
              </ErrorBoundary>
            </motion.div>
            </div>
          </div>
        )}
      </ScrollArea>

      {/* Scroll-to-bottom FAB */}
      <AnimatePresence>
        {!autoScroll && messageOrder.length > 0 && (
          <motion.button
            key="scroll-fab"
            type="button"
            initial={{ opacity: 0, y: 10, scale: 0.9 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 10, scale: 0.9 }}
            transition={{ duration: 0.2, ease: "easeOut" }}
            onClick={() => {
              autoScrollRef.current = true;
              setAutoScroll(true);
              forceScrollToEnd();
            }}
            className="absolute bottom-14 left-1/2 -translate-x-1/2 z-20 flex items-center justify-center gap-1.5 rounded-full border border-border/60 bg-background/90 backdrop-blur-sm px-4 py-2 min-h-[44px] text-xs text-muted-foreground shadow-lg hover:text-foreground hover:border-[var(--em-primary-alpha-20)] hover:shadow-xl transition-[color,border-color,box-shadow] cursor-pointer"
          >
            <ArrowDown className="h-3 w-3" />
            <span>回到底部</span>
          </motion.button>
        )}
      </AnimatePresence>

      <RollbackConfirmDialog
        open={rollbackDialog.open}
        sessionId={loadedSessionId}
        turnIndex={rollbackDialog.turnIndex}
        onConfirm={handleRollbackConfirm}
        onCancel={handleRollbackCancel}
      />
    </div>
  );
}

function TimestampSeparator({ ts, isNew }: { ts: number; isNew: boolean }) {
  return (
    <motion.div
      className="flex items-center justify-center py-1 max-w-4xl mx-auto px-3 sm:px-4"
      initial={isNew ? { opacity: 0, scale: 0.95 } : false}
      animate={{ opacity: 1, scale: 1 }}
      transition={{ duration: 0.2, ease: "easeOut" }}
    >
      <span className="text-[10px] text-muted-foreground/60 select-none">
        {formatTimestamp(ts)}
      </span>
    </motion.div>
  );
}

function MessageRenderFallback({ error, onReset }: { error: Error; onReset: () => void }) {
  return (
    <div className="rounded-lg border border-destructive/40 bg-destructive/5 px-3 py-2 text-xs text-muted-foreground">
      该消息渲染失败：{error.message}
      <button
        type="button"
        onClick={onReset}
        className="ml-2 underline underline-offset-2 hover:text-foreground"
      >
        重试
      </button>
    </div>
  );
}

function messageContentTick(msg: Message): number {
  if (msg.role === "user") return msg.content.length;
  let tick = msg.blocks.length * 10_000;
  for (const block of msg.blocks) {
    if (block.type === "text" || block.type === "thinking") {
      tick += block.content.length;
    } else if (block.type === "tool_call") {
      tick += (block.status?.length || 0) + (block.result?.length || 0) + (block.name?.length || 0);
    } else if (block.type === "subagent") {
      tick += (block.tools?.length || 0) * 100 + (block.status?.length || 0);
    }
  }
  return tick;
}

const MessageRowItem = memo(function MessageRowItem({
  messageId,
  isStreaming,
  isLast,
  onEditAndResend,
  onRetry,
  onRetryWithModel,
}: {
  messageId: string;
  isStreaming: boolean;
  isLast: boolean;
  onEditAndResend?: (messageId: string, newContent: string, files?: File[], retainedFiles?: FileAttachment[]) => void;
  onRetry?: (assistantMessageId: string) => void;
  onRetryWithModel?: (assistantMessageId: string, modelName: string) => void;
}) {
  const message = useChatStore((s) => s.messagesById[messageId]);
  useChatStore((s) => {
    const msg = s.messagesById[messageId];
    return msg ? messageContentTick(msg) : 0;
  });
  if (!message) return null;
  if (message.role === "user") {
    const visibleContent = stripInjectedUserPromptBlocks(message.content);
    if (!visibleContent && (!message.files || message.files.length === 0)) {
      return null;
    }
    return (
      <UserMessage
        content={visibleContent}
        files={message.files}
        isStreaming={isStreaming}
        timestamp={message.timestamp}
        onEditAndResend={
          onEditAndResend
            ? (newContent: string, files?: File[], retainedFiles?: FileAttachment[]) =>
                onEditAndResend(message.id, newContent, files, retainedFiles)
            : undefined
        }
      />
    );
  }
  return (
    <AssistantMessage
      messageId={message.id}
      blocks={message.blocks}
      affectedFiles={message.affectedFiles}
      isLastMessage={isLast}
      timestamp={message.timestamp}
      onRetry={onRetry ? () => onRetry(message.id) : undefined}
      onRetryWithModel={onRetryWithModel ? (model: string) => onRetryWithModel(message.id, model) : undefined}
    />
  );
});

function estimateMessageSize(msg: Message | undefined): number {
  if (!msg) return 96;
  if (msg.role === "user") {
    const visible = stripInjectedUserPromptBlocks(msg.content);
    if (!visible && (!msg.files || msg.files.length === 0)) return 0;
    const lineCount = (visible.match(/\n/g) || []).length + 1;
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
        estimate += Math.max(40, estimatedLines * 22 + 16);
        break;
      case "thinking":
        estimate += 56;
        break;
      case "tool_call":
        estimate += 100;
        break;
      case "token_stats":
        estimate += 40;
        break;
      case "status":
        if (isHiddenAssistantChrome(b)) break;
        estimate += 48;
        break;
      case "iteration":
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
    estimate += 40 + Math.min(msg.affectedFiles.length, 4) * 36;
  }

  return estimate;
}
