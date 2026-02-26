"use client";

import { useMemo, useState, useCallback } from "react";
import {
  Archive,
  ArchiveRestore,
  MessageSquare,
  Trash2,
  Ellipsis,
  Search,
  X,
  Layers,
  Zap,
  MessageSquarePlus,
  type LucideIcon,
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
import { uuid } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { useSessionStore } from "@/stores/session-store";
import { useChatStore } from "@/stores/chat-store";
import { listItemVariants } from "@/lib/sidebar-motion";

type SessionView = "all" | "active" | "archived";

/** Simple fuzzy match: checks if all characters in query appear in target in order */
function fuzzyMatch(target: string, query: string): boolean {
  let qi = 0;
  for (let ti = 0; ti < target.length && qi < query.length; ti++) {
    if (target[ti] === query[qi]) qi++;
  }
  return qi === query.length;
}

const filterViews: { key: SessionView; label: string; icon: LucideIcon }[] = [
  { key: "all", label: "全部", icon: Layers },
  { key: "active", label: "活跃", icon: Zap },
  { key: "archived", label: "归档", icon: Archive },
];

export function SessionList() {
  const sessions = useSessionStore((s) => s.sessions);
  const activeSessionId = useSessionStore((s) => s.activeSessionId);
  const addSession = useSessionStore((s) => s.addSession);
  const setActiveSession = useSessionStore((s) => s.setActiveSession);
  const removeSession = useSessionStore((s) => s.removeSession);
  const updateSessionStatus = useSessionStore((s) => s.updateSessionStatus);
  const switchSession = useChatStore((s) => s.switchSession);
  const removeSessionCache = useChatStore((s) => s.removeSessionCache);

  const handleNewChat = useCallback(() => {
    const id = uuid();
    addSession({ id, title: "新对话", messageCount: 0, inFlight: false });
    setActiveSession(id);
    switchSession(id);
  }, [addSession, setActiveSession, switchSession]);
  const [sessionView, setSessionView] = useState<SessionView>("all");
  const [busySessionId, setBusySessionId] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState("");

  const activeCount = useMemo(
    () =>
      sessions.filter(
        (session) => (session.status ?? "active") !== "archived"
      ).length,
    [sessions]
  );
  const archivedCount = sessions.length - activeCount;

  const filteredSessions = useMemo(
    () => {
      const q = searchQuery.trim().toLowerCase();
      return sessions.filter((session) => {
        const status = session.status ?? "active";
        if (sessionView === "active" && status === "archived") return false;
        if (sessionView === "archived" && status !== "archived") return false;
        if (q && !fuzzyMatch(session.title.toLowerCase(), q)) return false;
        return true;
      });
    },
    [sessionView, sessions, searchQuery]
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
      {/* New Chat Button */}
      <div className="px-1">
        <Button
          className="w-full justify-start gap-2 text-white hover:scale-[1.02] transition-transform duration-150 ease-out"
          size="sm"
          style={{ backgroundColor: "var(--em-primary)" }}
          onMouseEnter={(e) => (e.currentTarget.style.backgroundColor = "var(--em-primary-light)")}
          onMouseLeave={(e) => (e.currentTarget.style.backgroundColor = "var(--em-primary)")}
          onClick={handleNewChat}
        >
          <MessageSquarePlus className="h-4 w-4" />
          新建对话
        </Button>
      </div>

      {/* Search input */}
      <div className="px-1 relative">
        <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-muted-foreground/50 pointer-events-none" />
        <input
          type="text"
          placeholder="搜索对话…"
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
          className="w-full h-8 pl-8 pr-8 text-[13px] rounded-lg border border-border/60 bg-background/80 outline-none placeholder:text-muted-foreground/50 focus:border-[var(--em-primary)] focus:ring-2 focus:ring-[var(--em-primary-alpha-15)] transition-all duration-200"
        />
        {searchQuery && (
          <button
            onClick={() => setSearchQuery("")}
            className="absolute right-2.5 top-1/2 -translate-y-1/2 h-4 w-4 flex items-center justify-center rounded-full bg-muted text-muted-foreground hover:bg-muted-foreground/20 hover:text-foreground transition-colors touch-compact"
          >
            <X className="h-3 w-3" />
          </button>
        )}
      </div>

      {/* Filter tabs */}
      <div className="px-1 flex gap-0.5 flex-shrink-0 p-0.5 rounded-lg" style={{ backgroundColor: "var(--em-primary-alpha-06)" }}>
        {filterViews.map(({ key, label, icon: Icon }) => {
          const isActive = sessionView === key;
          const count = getCount(key);
          return (
            <button
              key={key}
              className="relative flex-1 flex items-center justify-center gap-1 py-1 text-[11px] font-medium rounded-md cursor-pointer transition-colors duration-150 select-none focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--em-primary)]"
              style={isActive ? { color: "white" } : { color: "var(--muted-foreground)" }}
              onClick={() => setSessionView(key)}
            >
              {isActive && (
                <motion.div
                  layoutId="session-filter-indicator"
                  className="absolute inset-0 rounded-md shadow-sm"
                  style={{ backgroundColor: "var(--em-primary)" }}
                  transition={{ type: "spring", stiffness: 400, damping: 30 }}
                />
              )}
              <Icon className="h-3 w-3 relative z-10" />
              <span className="relative z-10">{label}</span>
              {count > 0 && (
                <span
                  className="relative z-10 min-w-[16px] h-4 px-1 flex items-center justify-center rounded-full text-[10px] leading-none font-semibold"
                  style={
                    isActive
                      ? { backgroundColor: "rgba(255,255,255,0.25)", color: "white" }
                      : { backgroundColor: "var(--em-primary-alpha-10)", color: "var(--em-primary)" }
                  }
                >
                  {count}
                </span>
              )}
            </button>
          );
        })}
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
                  className={`group relative flex items-center gap-2 rounded-lg px-2.5 py-1 md:py-2 min-h-[1.75rem] md:min-h-[2.25rem] text-sm cursor-pointer transition-colors duration-150 ease-out ${
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
