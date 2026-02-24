"use client";

import { useRef, useEffect, useCallback, useState } from "react";
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

function estimateMessageSize(msg: Message): number {
  if (msg.role === "user") {
    const lineCount = (msg.content.match(/\n/g) || []).length + 1;
    return Math.max(72, Math.min(lineCount * 24 + 56, 300));
  }
  const blocks = msg.blocks;
  let estimate = 64;
  for (const b of blocks) {
    switch (b.type) {
      case "text":
        estimate += Math.max(40, Math.min(b.content.length * 0.4, 800));
        break;
      case "thinking":
        estimate += 60;
        break;
      case "tool_call":
        estimate += 80;
        break;
      case "token_stats":
        estimate += 40;
        break;
      default:
        estimate += 48;
    }
  }
  return Math.min(estimate, 2000);
}

export function MessageStream({ messages, isStreaming, onEditAndResend }: MessageStreamProps) {
  const viewportRef = useRef<HTMLDivElement>(null);
  const [autoScroll, setAutoScroll] = useState(true);
  const renderedIdsRef = useRef(new Set<string>());

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

  const scrollToBottom = useCallback(() => {
    if (!autoScroll) return;
    requestAnimationFrame(() => {
      const viewport = viewportRef.current;
      if (!viewport) return;
      viewport.scrollTo({
        top: viewport.scrollHeight,
        behavior: isStreaming ? "auto" : "smooth",
      });
    });
  }, [autoScroll, isStreaming]);

  useEffect(() => {
    scrollToBottom();
  }, [messages, scrollToBottom]);

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
