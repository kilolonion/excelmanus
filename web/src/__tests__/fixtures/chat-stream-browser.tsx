import React from "react";
import { createRoot } from "react-dom/client";
import { ChatInput } from "@/components/chat/ChatInput";
import { MessageStream } from "@/components/chat/MessageStream";
import { ExcelSidePanel } from "@/components/excel/ExcelSidePanel";
import { useChatStore } from "@/stores/chat-store";
import { useSessionStore } from "@/stores/session-store";
import { useUIStore } from "@/stores/ui-store";
import { useExcelStore } from "@/stores/excel-store";
import { sendMessage, stopGeneration } from "@/lib/chat-actions";
import { dispatchSSEEvent } from "@/lib/sse-event-handler";
import { openWorkspaceFile } from "@/lib/open-workspace-file";
import "@/app/globals.css";

useSessionStore.setState({ activeSessionId: "stream-browser", sessions: [
  { id: "stream-browser", workspaceId: "stream-ws", title: "流式回归", messageCount: 0, inFlight: false },
] });
useChatStore.setState({ loadedSessionId: "stream-browser", isLoadingMessages: false });
useUIStore.setState({ configReady: true, currentModel: "fixture", configError: null });
useExcelStore.setState({ activeWorkspaceKey: "id:stream-ws", panelOpen: false, fullViewPath: null });
Object.assign(window, { chatStore: useChatStore, excelStore: useExcelStore, dispatchSSEEvent, openWorkspaceFile });

function Harness() {
  const streaming = useChatStore((s) => s.isStreaming);
  return <div style={{ display: "flex", height: "100dvh", minWidth: 0, overflow: "hidden" }}>
    <main style={{ display: "flex", flexDirection: "column", flex: 1, minWidth: 0 }}>
      <MessageStream isStreaming={streaming} />
      <ChatInput onSend={(text, files) => sendMessage(text, files, "stream-browser")} isStreaming={streaming} onStop={stopGeneration} />
    </main>
    <ExcelSidePanel />
  </div>;
}
createRoot(document.getElementById("root")!).render(<Harness />);
