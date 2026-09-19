"use client";

import { useEffect } from "react";
import { useParams } from "next/navigation";
import { MessageStream } from "@/components/chat/MessageStream";
import { ChatInput } from "@/components/chat/ChatInput";
import { CommandResultDialog, useCommandResult } from "@/components/modals/CommandResultDialog";
import { WorkspaceViewHost } from "@/components/workspace/WorkspaceViewHost";
import { useChatStore } from "@/stores/chat-store";
import { useSessionStore } from "@/stores/session-store";
import { useExcelStore } from "@/stores/excel-store";
import { sendMessage, stopGeneration, rollbackAndResend, retryAssistantMessage } from "@/lib/chat-actions";
import type { AttachedFile, FileAttachment } from "@/lib/types";

function ChatPage() {
  const params = useParams();
  const sessionId = params.sessionId as string;
  const isStreaming = useChatStore((s) => s.isStreaming);
  const setActiveSession = useSessionStore((s) => s.setActiveSession);
  const compareMode = useExcelStore((s) => s.compareMode);
  const cmdResult = useCommandResult();

  useEffect(() => {
    setActiveSession(sessionId);
  }, [sessionId, setActiveSession]);

  const handleSend = (text: string, files?: AttachedFile[]) => {
    sendMessage(text, files, sessionId);
  };

  return (
    <div className="flex flex-col h-full">
      <WorkspaceViewHost>
        <MessageStream
          isStreaming={isStreaming}
          onEditAndResend={(messageId: string, newContent: string, files?: File[], retainedFiles?: FileAttachment[]) => {
            rollbackAndResend(messageId, newContent, sessionId, files, retainedFiles);
          }}
          onRetry={(assistantMessageId: string) => {
            retryAssistantMessage(assistantMessageId, sessionId);
          }}
          onRetryWithModel={(assistantMessageId: string, modelName: string) => {
            retryAssistantMessage(assistantMessageId, sessionId, modelName);
          }}
        />
      </WorkspaceViewHost>

      {!compareMode && (
        <div className="relative z-30 px-3 sm:px-4 pb-4 pt-6 -mt-6 bg-gradient-to-t from-background from-70% to-transparent pointer-events-none flex-shrink-0" style={{ paddingBottom: "max(1rem, var(--sab, 0px))" }}>
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

export default ChatPage;
