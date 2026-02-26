"use client";

import { useMemo, useState } from "react";
import {
  Archive,
  ArchiveRestore,
  MessageSquare,
  Trash2,
  Ellipsis,
} from "lucide-react";
import { motion, AnimatePresence } from "framer-motion";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { archiveSession, deleteSession, abortChat } from "@/lib/api";
import { stopGeneration } from "@/lib/chat-actions";
import { useSessionStore } from "@/stores/session-store";
import { useChatStore } from "@/stores/chat-store";
import { listItemVariants } from "@/lib/sidebar-motion";

type SessionView = "all" | "active" | "archived";

const filterViews: { key: SessionView; label: string }[] = [
  { key: "all", label: "全部" },
  { key: "active", label: "活跃" },
  { key: "archived", label: "归档" },
];

export function SessionList() {
  const sessions = useSessionStore((s) => s.sessions);
  const activeSessionId = useSessionStore((s) => s.activeSessionId);
  const setActiveSession = useSessionStore((s) => s.setActiveSession);
  const removeSession = useSessionStore((s) => s.removeSession);
  const updateSessionStatus = useSessionStore((s) => s.updateSessionStatus);
  const switchSession = useChatStore((s) => s.switchSession);
  const removeSessionCache = useChatStore((s) => s.removeSessionCache);
  const [sessionView, setSessionView] = useState<SessionView>("all");
  const [busySessionId, setBusySessionId] = useState<string | null>(null);

  const activeCount = useMemo(
    () =>
      sessions.filter(
        (session) => (session.status ?? "active") !== "archived"
      ).length,
    [sessions]
  );
  const archivedCount = sessions.length - activeCount;

  const filteredSessions = useMemo(
    () =>
      sessions.filter((session) => {
        const status = session.status ?? "active";
        if (sessionView === "active") return status !== "archived";
        if (sessionView === "archived") return status === "archived";
        return true;
      }),
    [sessionView, sessions]
  );

  const getCount = (view: SessionView) => {
    if (view === "all") return sessions.length;
    if (view === "active") return activeCount;
    return archivedCount;
  };

  const handleDelete = async (sessionId: string) => {
    if (busySessionId) return;
    setBusySessionId(sessionId);

    const chatState = useChatStore.getState();
    const isDeletingCurrent = chatState.currentSessionId === sessionId;

    // 若删除的是当前正在流式输出的会话，则同时中止前端 SSE
    if (isDeletingCurrent && chatState.abortController) {
      stopGeneration();
    } else {
      // 对非当前会话，仍通知后端取消任务
      abortChat(sessionId).catch(() => {});
    }

    try {
      await deleteSession(sessionId);
    } catch {
      // 后端 404 可接受，会话可能仅存在于本地
    }
    const isDeletingActive = sessionId === activeSessionId;
    removeSessionCache(sessionId);
    removeSession(sessionId);

    // 删除当前会话时自动切换到下一个可用会话，与 handleArchiveToggle 行为一致，避免 activeSessionId=null 导致模型配置暂时为空。
    if (isDeletingActive) {
      const nextActive = sessions.find(
        (s) => s.id !== sessionId && (s.status ?? "active") !== "archived"
      );
      if (nextActive) {
        setActiveSession(nextActive.id);
        switchSession(nextActive.id);
      }
    }
    setBusySessionId((cur) => (cur === sessionId ? null : cur));
  };

  const handleArchiveToggle = async (
    sessionId: string,
    currentlyArchived: boolean
  ) => {
    if (busySessionId) return;
    setBusySessionId(sessionId);
    const newArchived = !currentlyArchived;
    try {
      await archiveSession(sessionId, newArchived);
    } catch {
      // 后端 404 可接受，仍更新本地状态
    }
    const newStatus = newArchived ? "archived" : "active";
    updateSessionStatus(sessionId, newStatus);

    if (newArchived && sessionId === activeSessionId) {
      const nextActive = sessions.find(
        (s) => s.id !== sessionId && (s.status ?? "active") !== "archived"
      );
      if (nextActive) {
        setActiveSession(nextActive.id);
        switchSession(nextActive.id);
      } else {
        setActiveSession(null);
        switchSession(null);
      }
    }
    setBusySessionId((cur) => (cur === sessionId ? null : cur));
  };

  if (sessions.length === 0) {
    return (
      <div className="flex flex-col items-center gap-2 px-2 py-6 text-muted-foreground">
        <MessageSquare
          className="h-8 w-8"
          style={{ color: "var(--em-primary)" }}
        />
        <p className="text-xs">暂无对话</p>
      </div>
    );
  }

  return (
    <div className="space-y-1.5 py-1">
      {/* Filter tabs */}
      <div className="px-1 flex gap-1 flex-shrink-0">
        {filterViews.map(({ key, label }) => (
          <button
            key={key}
            className="relative h-6 min-h-8 px-2 text-xs rounded-md z-10 cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-offset-1 flex-shrink-0 whitespace-nowrap"
            style={{
              minWidth: key === "all" ? "3rem" : key === "active" ? "3rem" : "3rem",
              ...(sessionView === key ? { color: "white" } : {}),
              // @ts-expect-error CSS custom property
              "--tw-ring-color": "var(--em-primary)",
            }}
            onClick={() => setSessionView(key)}
          >
            {sessionView === key && (
              <motion.div
                layoutId="session-filter-indicator"
                className="absolute inset-0 rounded-md"
                style={{ backgroundColor: "var(--em-primary)" }}
                transition={{ duration: 0.15, ease: "easeOut" }}
              />
            )}
            <span className="relative z-10 flex items-center justify-center w-full">
              {label} {getCount(key)}
            </span>
          </button>
        ))}
      </div>

      {/* Empty state for current filter */}
      {filteredSessions.length === 0 ? (
        <div className="flex flex-col items-center gap-2 px-2 py-6 text-muted-foreground">
          <MessageSquare
            className="h-6 w-6"
            style={{ color: "var(--em-primary)" }}
          />
          <p className="text-xs">
            {sessionView === "archived" ? "暂无归档对话" : "暂无对话"}
          </p>
        </div>
      ) : (
        <AnimatePresence mode="popLayout">
          <div className="space-y-0.5">
            {filteredSessions.map((session) => {
              const isActive = session.id === activeSessionId;
              const isArchived =
                (session.status ?? "active") === "archived";

              return (
                <motion.div
                  key={session.id}
                  variants={listItemVariants}
                  initial="initial"
                  animate="animate"
                  exit="exit"
                  layout
                  className={`group relative flex items-center gap-2 rounded-lg px-2.5 py-2 min-h-[2.25rem] text-sm cursor-pointer transition-colors duration-150 ease-out ${
                    isActive
                      ? "bg-accent/60 ring-1 ring-[var(--em-primary)]/25"
                      : "hover:bg-accent/40"
                  }`}
                  style={isActive ? { borderLeft: "2.5px solid var(--em-primary)" } : undefined}
                  tabIndex={0}
                  role="button"
                  onClick={() => {
                    setActiveSession(session.id);
                  }}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault();
                      setActiveSession(session.id);
                    }
                  }}
                >
                  <MessageSquare
                    className="h-3.5 w-3.5 flex-shrink-0"
                    style={{
                      color: isActive
                        ? "var(--em-primary)"
                        : "var(--muted-foreground)",
                    }}
                  />
                  <span
                    className={`flex-1 min-w-0 truncate ${
                      isActive
                        ? "font-medium text-foreground"
                        : "text-foreground/80"
                    }`}
                  >
                    {session.title}
                  </span>

                  {/* Three-dot menu — visible on hover or when active */}
                  <DropdownMenu>
                    <DropdownMenuTrigger asChild>
                      <button
                        className={`flex-shrink-0 h-6 w-6 flex items-center justify-center rounded-md text-muted-foreground transition-opacity duration-150 hover:bg-accent hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--em-primary)] ${
                          isActive
                            ? "opacity-100"
                            : "opacity-0 group-hover:opacity-100 touch-show"
                        }`}
                        onClick={(e) => e.stopPropagation()}
                      >
                        <Ellipsis className="h-4 w-4" />
                      </button>
                    </DropdownMenuTrigger>
                    <DropdownMenuContent
                      side="right"
                      align="start"
                      className="w-40"
                    >
                      <DropdownMenuItem
                        onClick={(e) => {
                          e.stopPropagation();
                          void handleArchiveToggle(
                            session.id,
                            isArchived
                          );
                        }}
                      >
                        {isArchived ? (
                          <>
                            <ArchiveRestore className="h-4 w-4" />
                            取消归档
                          </>
                        ) : (
                          <>
                            <Archive className="h-4 w-4" />
                            归档
                          </>
                        )}
                      </DropdownMenuItem>
                      <DropdownMenuSeparator />
                      <DropdownMenuItem
                        variant="destructive"
                        onClick={(e) => {
                          e.stopPropagation();
                          void handleDelete(session.id);
                        }}
                      >
                        <Trash2 className="h-4 w-4" />
                        删除
                      </DropdownMenuItem>
                    </DropdownMenuContent>
                  </DropdownMenu>
                </motion.div>
              );
            })}
          </div>
        </AnimatePresence>
      )}
    </div>
  );
}
