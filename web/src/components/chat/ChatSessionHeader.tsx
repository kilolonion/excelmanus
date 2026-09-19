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
          className="min-w-0 max-w-full truncate rounded-full border border-[var(--em-line)] bg-card/80 px-3 py-1 text-[13px] font-semibold text-[var(--em-ink)] shadow-sm sm:text-sm"
          title={title}
        >
          {title}
        </h1>
      )}
    </div>
  );
}
