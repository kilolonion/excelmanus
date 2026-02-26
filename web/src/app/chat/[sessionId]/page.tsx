"use client";

import { useEffect } from "react";
import { useParams } from "next/navigation";
import { MessageStream } from "@/components/chat/MessageStream";
import { ChatInput } from "@/components/chat/ChatInput";
import { QuestionPanel } from "@/components/modals/QuestionPanel";
import { CommandResultDialog, useCommandResult } from "@/components/modals/CommandResultDialog";
import { ExcelFullView } from "@/components/excel/ExcelFullView";
import { useChatStore } from "@/stores/chat-store";
import { useSessionStore } from "@/stores/session-store";
import { useExcelStore } from "@/stores/excel-store";
import { sendMessage, stopGeneration, rollbackAndResend, retryAssistantMessage } from "@/lib/chat-actions";

function ChatPage() {
  const params = useParams();
  const sessionId = params.sessionId as string;
  const messages = useChatStore((s) => s.messages);
  const isStreaming = useChatStore((s) => s.isStreaming);
  const pendingQuestion = useChatStore((s) => s.pendingQuestion);
  const setActiveSession = useSessionStore((s) => s.setActiveSession);
  const fullViewPath = useExcelStore((s) => s.fullViewPath);
  const cmdResult = useCommandResult();

  useEffect(() => {
    setActiveSession(sessionId);
  }, [sessionId, setActiveSession]);

  const handleSend = (text: string, files?: File[]) => {
    sendMessage(text, files, sessionId);
  };

  return (
    <div className="flex flex-col h-full">
      {fullViewPath ? (
        <ExcelFullView />
      ) : (
        <MessageStream
          messages={messages}
          isStreaming={isStreaming}
          onEditAndResend={(messageId: string, newContent: string, rollbackFiles: boolean, files?: File[]) => {
            rollbackAndResend(messageId, newContent, rollbackFiles, sessionId, files);
          }}
          onRetry={(assistantMessageId: string) => {
            retryAssistantMessage(assistantMessageId, sessionId);
          }}
          onRetryWithModel={(assistantMessageId: string, modelName: string) => {
            retryAssistantMessage(assistantMessageId, sessionId, modelName);
          }}
          // 传递sessionId作为key，确保会话切换时重新创建组件
          key={sessionId}
        />
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

export default ChatPage;
