"use client";

import { motion, AnimatePresence } from "framer-motion";
import { WelcomePage } from "@/components/welcome/WelcomePage";
import { ChatInput } from "@/components/chat/ChatInput";
import { MessageStream } from "@/components/chat/MessageStream";
import { QuestionPanel } from "@/components/modals/QuestionPanel";
import { CommandResultDialog, useCommandResult } from "@/components/modals/CommandResultDialog";
import { ExcelFullView } from "@/components/excel/ExcelFullView";
import { useChatStore } from "@/stores/chat-store";
import { useSessionStore } from "@/stores/session-store";
import { useExcelStore } from "@/stores/excel-store";
import { sendMessage, stopGeneration, rollbackAndResend, retryAssistantMessage } from "@/lib/chat-actions";
import { uuid } from "@/lib/utils";
import type { AttachedFile, FileAttachment } from "@/lib/types";

const viewTransition = { duration: 0.2, ease: "easeOut" as const };

export default function Home() {
  const messages = useChatStore((s) => s.messages);
  const isStreaming = useChatStore((s) => s.isStreaming);
  const isLoadingMessages = useChatStore((s) => s.isLoadingMessages);
  const currentSessionId = useChatStore((s) => s.currentSessionId);
  const pendingQuestion = useChatStore((s) => s.pendingQuestion);
  const activeSessionId = useSessionStore((s) => s.activeSessionId);
  const fullViewPath = useExcelStore((s) => s.fullViewPath);
  const addSession = useSessionStore((s) => s.addSession);
  const setActiveSession = useSessionStore((s) => s.setActiveSession);
  const cmdResult = useCommandResult();

  const handleSend = (text: string, files?: AttachedFile[]) => {
    if (!activeSessionId) {
      const id = uuid();
      addSession({
        id,
        title: text.slice(0, 60) || "新对话",
        messageCount: 0,
        inFlight: false,
      });
      setActiveSession(id);
    }
    sendMessage(text, files);
  };

  const hasMessages = messages.length > 0;
  // 会话恢复中：activeSessionId 已从 localStorage 恢复但消息尚未加载
  // 不展示 WelcomePage，避免闪烁
  const isRestoringSession = !!activeSessionId && !hasMessages
    && (currentSessionId !== activeSessionId || isLoadingMessages);

  return (
    <div className="flex flex-col h-full">
      <AnimatePresence mode="wait" initial={false}>
        {fullViewPath ? (
          <motion.div key="excel" className="flex-1 min-h-0" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} transition={viewTransition}>
            <ExcelFullView />
          </motion.div>
        ) : hasMessages ? (
          <motion.div key="chat" className="flex-1 min-h-0 flex flex-col" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} transition={viewTransition}>
            <MessageStream
              messages={messages}
              isStreaming={isStreaming}
              onEditAndResend={(messageId: string, newContent: string, rollbackFiles: boolean, files?: File[], retainedFiles?: FileAttachment[]) => {
                rollbackAndResend(messageId, newContent, rollbackFiles, activeSessionId, files, retainedFiles);
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
            <WelcomePage onSuggestionClick={handleSend} />
          </motion.div>
        )}
      </AnimatePresence>

      <div className="relative z-30 px-4 pb-4 pt-6 -mt-6 bg-gradient-to-t from-background from-70% to-transparent pointer-events-none flex-shrink-0" style={{ paddingBottom: "max(1rem, var(--sab, 0px))" }}>
        <div className="max-w-3xl mx-auto pointer-events-auto">
          {pendingQuestion && <QuestionPanel />}
          <ChatInput
            onSend={handleSend}
            onCommandResult={cmdResult.show}
            disabled={false}
            isStreaming={isStreaming}
            onStop={stopGeneration}
          />
        </div>
      </div>

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
