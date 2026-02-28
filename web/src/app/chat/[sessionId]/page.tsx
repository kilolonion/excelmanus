"use client";

import { useEffect } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { useParams } from "next/navigation";
import { MessageStream } from "@/components/chat/MessageStream";
import { ChatInput } from "@/components/chat/ChatInput";
import { CommandResultDialog, useCommandResult } from "@/components/modals/CommandResultDialog";
import { ExcelFullView } from "@/components/excel/ExcelFullView";
import { useChatStore } from "@/stores/chat-store";
import { useSessionStore } from "@/stores/session-store";
import { useExcelStore } from "@/stores/excel-store";
import { sendMessage, stopGeneration, rollbackAndResend, retryAssistantMessage } from "@/lib/chat-actions";
import type { AttachedFile, FileAttachment } from "@/lib/types";

const viewTransition = { duration: 0.2, ease: "easeOut" as const };

function ChatPage() {
  const params = useParams();
  const sessionId = params.sessionId as string;
  const messages = useChatStore((s) => s.messages);
  const isStreaming = useChatStore((s) => s.isStreaming);
  const setActiveSession = useSessionStore((s) => s.setActiveSession);
  const fullViewPath = useExcelStore((s) => s.fullViewPath);
  const cmdResult = useCommandResult();

  useEffect(() => {
    setActiveSession(sessionId);
  }, [sessionId, setActiveSession]);

  const handleSend = (text: string, files?: AttachedFile[]) => {
    sendMessage(text, files, sessionId);
  };

  return (
    <div className="flex flex-col h-full">
      <AnimatePresence mode="wait" initial={false}>
        {fullViewPath ? (
          <motion.div key="excel" className="flex-1 min-h-0" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} transition={viewTransition}>
            <ExcelFullView />
          </motion.div>
        ) : (
          <motion.div key={`chat-${sessionId}`} className="flex-1 min-h-0 flex flex-col" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} transition={viewTransition}>
            <MessageStream
              messages={messages}
              isStreaming={isStreaming}
              onEditAndResend={(messageId: string, newContent: string, rollbackFiles: boolean, files?: File[], retainedFiles?: FileAttachment[]) => {
                rollbackAndResend(messageId, newContent, rollbackFiles, sessionId, files, retainedFiles);
              }}
              onRetry={(assistantMessageId: string, rollbackFiles?: boolean) => {
                retryAssistantMessage(assistantMessageId, sessionId, undefined, rollbackFiles);
              }}
              onRetryWithModel={(assistantMessageId: string, modelName: string, rollbackFiles?: boolean) => {
                retryAssistantMessage(assistantMessageId, sessionId, modelName, rollbackFiles);
              }}
            />
          </motion.div>
        )}
      </AnimatePresence>

      <div className="relative z-30 px-4 pb-4 pt-6 -mt-6 bg-gradient-to-t from-background from-70% to-transparent pointer-events-none flex-shrink-0" style={{ paddingBottom: "max(1rem, var(--sab, 0px))" }}>
        <div className="max-w-3xl mx-auto pointer-events-auto">
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

export default ChatPage;
