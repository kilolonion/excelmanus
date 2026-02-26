"use client";

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
import type { AttachedFile } from "@/lib/types";

export default function Home() {
  const messages = useChatStore((s) => s.messages);
  const isStreaming = useChatStore((s) => s.isStreaming);
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

  return (
    <div className="flex flex-col h-full">
      {fullViewPath ? (
        <ExcelFullView />
      ) : hasMessages ? (
        <MessageStream
          messages={messages}
          isStreaming={isStreaming}
          onEditAndResend={(messageId: string, newContent: string, rollbackFiles: boolean, files?: File[]) => {
            rollbackAndResend(messageId, newContent, rollbackFiles, activeSessionId, files);
          }}
          onRetry={(assistantMessageId: string) => {
            retryAssistantMessage(assistantMessageId, activeSessionId);
          }}
          onRetryWithModel={(assistantMessageId: string, modelName: string) => {
            retryAssistantMessage(assistantMessageId, activeSessionId, modelName);
          }}
        />
      ) : (
        <WelcomePage onSuggestionClick={handleSend} />
      )}

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
