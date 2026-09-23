"use client";

import { useEffect, useState } from "react";
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
import { ChatHistoryStatus } from "@/components/chat/ChatHistoryStatus";
import { WelcomePage } from "@/components/welcome/WelcomePage";

function ChatPage() {
  const params = useParams();
  const routeSessionId = params.sessionId as string;
  // The URL selects the initial conversation. Subsequent sidebar selections
  // use the same source of truth as SessionSync and the home page.
  const sessionId = useSessionStore((s) => s.activeSessionId) ?? routeSessionId;
  const isStreaming = useChatStore((s) => s.isStreaming);
  const messageOrder = useChatStore((s) => s.messageOrder);
  const messageLoadError = useChatStore((s) => s.messageLoadError);
  const isLoadingMessages = useChatStore((s) => s.isLoadingMessages);
  const loadedSessionId = useChatStore((s) => s.loadedSessionId);
  const setActiveSession = useSessionStore((s) => s.setActiveSession);
  const compareMode = useExcelStore((s) => s.compareMode);
  const cmdResult = useCommandResult();
  const [composerDraft, setComposerDraft] = useState<{ seq: number; text: string; files: File[] } | null>(null);

  useEffect(() => {
    setActiveSession(routeSessionId);
  }, [routeSessionId, setActiveSession]);

  const handleSend = (text: string, files?: AttachedFile[], capturedSessionId?: string | null) => {
    if (capturedSessionId && capturedSessionId !== sessionId) return false;
    return sendMessage(text, files, sessionId);
  };

  return (
    <div className="flex flex-col h-full">
      <WorkspaceViewHost>
        {loadedSessionId !== sessionId || messageOrder.length === 0 ? (
          isLoadingMessages || messageLoadError || loadedSessionId !== sessionId
            ? <ChatHistoryStatus sessionId={sessionId} empty />
            : <WelcomePage onSuggestionClick={(text, files) => setComposerDraft({ seq: Date.now(), text, files: files ?? [] })} />
        ) : (
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
        )}
      </WorkspaceViewHost>

      {!compareMode && (
        <div className="em-composer-dock relative z-30 pt-6 -mt-6 pointer-events-none flex-shrink-0">
          <div className="mx-auto w-full max-w-4xl pointer-events-auto">
            <ChatInput
              onSend={handleSend}
              onCommandResult={cmdResult.show}
              disabled={loadedSessionId !== sessionId || isLoadingMessages}
              isStreaming={isStreaming}
              onStop={stopGeneration}
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

export default ChatPage;
