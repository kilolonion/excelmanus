import React, { useEffect } from "react";
import { createRoot } from "react-dom/client";
import { ChatInput } from "@/components/chat/ChatInput";
import { MessageStream } from "@/components/chat/MessageStream";
import { useChatStore, refreshSessionMessagesFromBackend } from "@/stores/chat-store";
import { useSessionStore } from "@/stores/session-store";
import { useUIStore } from "@/stores/ui-store";
import { useDispatchStore } from "@/stores/dispatch-store";
import { sendMessage, stopGeneration, subscribeToSession } from "@/lib/chat-actions";
import { fetchSessionDetail } from "@/lib/api";
import "@/app/globals.css";

const sid = "dispatch-browser";
useSessionStore.setState({ activeSessionId: sid, sessions: [{ id: sid, title: "消息调度验证", messageCount: 0, inFlight: false }] });
useChatStore.setState({ loadedSessionId: sid, isLoadingMessages: false });
useUIStore.setState({ configReady: true, configError: null });
Object.assign(window, { chatStore: useChatStore, dispatchStore: useDispatchStore, uiStore: useUIStore });

function Harness() {
  const streaming = useChatStore((s) => s.isStreaming);
  useEffect(() => {
    void (async () => {
      const detail = await fetchSessionDetail(sid);
      await refreshSessionMessagesFromBackend(sid);
      if (detail?.inFlight && detail.activeStreamId) {
        useChatStore.getState().setStreamState(detail.activeStreamId, detail.latestSeq);
        await subscribeToSession(sid);
      }
    })();
  }, []);
  return <main className="mx-auto flex h-dvh max-w-4xl flex-col overflow-hidden">
    <MessageStream isStreaming={streaming} />
    <ChatInput onSend={(text, files, sessionId, mode) => sendMessage(text, files, sessionId, undefined, undefined, mode)} isStreaming={streaming} onStop={stopGeneration} />
  </main>;
}
createRoot(document.getElementById("root")!).render(<Harness />);
