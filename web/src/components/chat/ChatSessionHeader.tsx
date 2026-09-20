"use client";

import { useSessionStore } from "@/stores/session-store";
import { useChatStore } from "@/stores/chat-store";

export function ChatSessionHeader() {
  const activeSessionId = useSessionStore((s) => s.activeSessionId);
  const sessions = useSessionStore((s) => s.sessions);
  const title = sessions.find((s) => s.id === activeSessionId)?.title;
  const messageCount = useChatStore((s) => s.messageOrder.length);

  if (!activeSessionId || (!title && messageCount === 0)) return null;

  return (
    <div className="flex items-center gap-2 min-w-0 flex-1 mr-2">
      {title && (
        <h1
          className="em-session-title"
          title={title}
        >
          {title}
        </h1>
      )}
    </div>
  );
}
