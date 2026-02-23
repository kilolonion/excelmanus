"use client";

import { useRef, useEffect, useCallback, useState } from "react";
import { ScrollArea } from "@/components/ui/scroll-area";
import { UserMessage } from "./UserMessage";
import { AssistantMessage } from "./AssistantMessage";
import { RollbackConfirmDialog, getRollbackFilePreference } from "./RollbackConfirmDialog";
import type { Message } from "@/lib/types";

interface MessageStreamProps {
  messages: Message[];
  isStreaming: boolean;
  onEditAndResend?: (messageId: string, newContent: string, rollbackFiles: boolean) => void;
}

export function MessageStream({ messages, isStreaming, onEditAndResend }: MessageStreamProps) {
  const viewportRef = useRef<HTMLDivElement>(null);
  const [autoScroll, setAutoScroll] = useState(true);

  // Rollback confirmation dialog state
  const [rollbackDialog, setRollbackDialog] = useState<{
    open: boolean;
    messageId: string;
    newContent: string;
  }>({ open: false, messageId: "", newContent: "" });

  const scrollToBottom = useCallback(() => {
    if (!autoScroll) return;
    const viewport = viewportRef.current;
    if (!viewport) return;
    viewport.scrollTo({
      top: viewport.scrollHeight,
      behavior: isStreaming ? "auto" : "smooth",
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

      // Check if there are any tool_call blocks after this message (indicates file modifications)
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

      // No file changes — skip dialog, send directly without rollback
      if (!hasFileChanges) {
        onEditAndResend(messageId, newContent, false);
        return;
      }

      const pref = getRollbackFilePreference();
      if (pref !== null) {
        // User has saved preference, skip dialog
        onEditAndResend(messageId, newContent, pref === "always_rollback");
        return;
      }

      // Show confirmation dialog
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

  return (
    <>
      <ScrollArea
        className="flex-1 min-h-0"
        viewportRef={viewportRef}
        onViewportScroll={handleScroll}
      >
        <div className="max-w-3xl mx-auto px-4 py-6">
          {messages.map((message) => {
            if (message.role === "user") {
              return (
                <UserMessage
                  key={message.id}
                  content={message.content}
                  files={message.files}
                  isStreaming={isStreaming}
                  onEditAndResend={
                    onEditAndResend
                      ? (newContent: string) => handleEditAndResend(message.id, newContent)
                      : undefined
                  }
                />
              );
            }
            return (
              <AssistantMessage
                key={message.id}
                messageId={message.id}
                blocks={message.blocks}
                affectedFiles={message.affectedFiles}
              />
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
