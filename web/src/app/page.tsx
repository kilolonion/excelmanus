"use client";

import { useCallback, useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { WelcomePage } from "@/components/welcome/WelcomePage";
import { ChatInput } from "@/components/chat/ChatInput";
import { MessageStream } from "@/components/chat/MessageStream";
import { CommandResultDialog, useCommandResult } from "@/components/modals/CommandResultDialog";
import { ExcelFullView } from "@/components/excel/ExcelFullView";
import { ExcelCompareView } from "@/components/excel/ExcelCompareView";
import { WordFullView } from "@/components/word/WordFullView";
import { useChatStore } from "@/stores/chat-store";
import { useSessionStore } from "@/stores/session-store";
import { useExcelStore } from "@/stores/excel-store";
import { useWordStore } from "@/stores/word-store";
import { sendMessage, stopGeneration, rollbackAndResend, retryAssistantMessage } from "@/lib/chat-actions";
import { createOrReuseSession } from "@/lib/session-actions";
import type { AttachedFile, FileAttachment } from "@/lib/types";

const viewTransition = { duration: 0.2, ease: "easeOut" as const };

export default function Home() {
  const messageOrder = useChatStore((s) => s.messageOrder);
  const isStreaming = useChatStore((s) => s.isStreaming);
  const isLoadingMessages = useChatStore((s) => s.isLoadingMessages);
  const loadedSessionId = useChatStore((s) => s.loadedSessionId);
  const activeSessionId = useSessionStore((s) => s.activeSessionId);
  const fullViewPath = useExcelStore((s) => s.fullViewPath);
  const compareMode = useExcelStore((s) => s.compareMode);
  const wordFullViewPath = useWordStore((s) => s.fullViewPath);
  const cmdResult = useCommandResult();
  const [composerDraft, setComposerDraft] = useState<{ seq: number; text: string; files: File[] } | null>(null);

  const handleSend = async (text: string, files?: AttachedFile[]) => {
    setComposerDraft(null);
    let sid = useSessionStore.getState().activeSessionId;
    if (!sid) {
      try {
        const session = await createOrReuseSession();
        sid = session.id;
      } catch (err) {
        console.error("创建对话失败:", err);
        return;
      }
    }
    sendMessage(text, files, sid);
  };

  const handleSuggestionClick = useCallback((text: string, files?: File[]) => {
    setComposerDraft({ seq: Date.now(), text, files: files ?? [] });
  }, []);

  const handleStop = () => {
    stopGeneration();
  };

  const hasMessages = messageOrder.length > 0;
  // 会话恢复中：activeSessionId 已从 localStorage 恢复但消息尚未加载
  // 不展示 WelcomePage，避免闪烁
  const isRestoringSession = !!activeSessionId && !hasMessages
    && (loadedSessionId !== activeSessionId || isLoadingMessages);

  return (
    <div className="flex flex-col h-full">
      <AnimatePresence mode="wait" initial={false}>
        {wordFullViewPath ? (
          <motion.div key="word" className="flex-1 min-h-0" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} transition={viewTransition}>
            <WordFullView />
          </motion.div>
        ) : compareMode ? (
          <motion.div key="compare" className="flex-1 min-h-0" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} transition={viewTransition}>
            <ExcelCompareView />
          </motion.div>
        ) : fullViewPath ? (
          <motion.div key="excel" className="flex-1 min-h-0" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} transition={viewTransition}>
            <ExcelFullView />
          </motion.div>
        ) : hasMessages ? (
          <motion.div key="chat" className="relative flex-1 min-h-0 flex flex-col" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} transition={viewTransition}>
            <MessageStream
              isStreaming={isStreaming}
              onEditAndResend={(messageId: string, newContent: string, files?: File[], retainedFiles?: FileAttachment[]) => {
                rollbackAndResend(messageId, newContent, activeSessionId, files, retainedFiles);
              }}
              onRetry={(assistantMessageId: string) => {
                retryAssistantMessage(assistantMessageId, activeSessionId);
              }}
              onRetryWithModel={(assistantMessageId: string, modelName: string) => {
                retryAssistantMessage(assistantMessageId, activeSessionId, modelName);
              }}
            />
          </motion.div>
        ) : isRestoringSession ? (
          <div key="restoring" className="flex-1" />
        ) : (
          <motion.div key="welcome" className="flex-1 min-h-0 flex flex-col" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0, y: -10 }} transition={viewTransition}>
            <WelcomePage onSuggestionClick={handleSuggestionClick} />
          </motion.div>
        )}
      </AnimatePresence>

      {!compareMode && (
        <div className="relative z-30 px-4 pb-4 pt-6 -mt-6 bg-gradient-to-t from-background from-70% to-transparent pointer-events-none flex-shrink-0" style={{ paddingBottom: "max(1rem, var(--sab, 0px))" }}>
          <div className="max-w-3xl mx-auto pointer-events-auto">
            <ChatInput
              onSend={handleSend}
              onCommandResult={cmdResult.show}
              disabled={false}
              isStreaming={isStreaming}
              onStop={handleStop}
              composerDraft={composerDraft}
            />
          </div>
        </div>
      )}

      <CommandResultDialog
        open={cmdResult.state.open}
        onClose={cmdResult.close}
        command={cmdResult.state.command}
        result={cmdResult.state.result}
        format={cmdResult.state.format}
      />
    </div>
  );
}
