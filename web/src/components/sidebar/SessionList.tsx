"use client";

import { useMemo, useState, useCallback, useRef, useEffect } from "react";
import {
  MessageSquare,
  Trash2,
  Ellipsis,
  Search,
  X,
  Pencil,
  Check,
  Download,
  FileText,
  FileJson,
  FolderPlus,
  Folder,
  ChevronDown,
  Plus,
} from "lucide-react";
import { motion, AnimatePresence } from "framer-motion";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { deleteSession, abortChat, updateSessionTitle, exportSession, type ExportFormat, fetchWorkspaces, deleteWorkspaceFolder } from "@/lib/api";
import { createOrReuseSession } from "@/lib/session-actions";
import type { WorkspaceFolder } from "@/lib/types";
import { stopGeneration } from "@/lib/chat-actions";
import { useSessionStore } from "@/stores/session-store";
import { useChatStore } from "@/stores/chat-store";
import { listItemVariants } from "@/lib/sidebar-motion";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { cn } from "@/lib/utils";
import { AddWorkspaceDialog } from "@/components/sidebar/AddWorkspaceDialog";

const sidebarMenuPanelClass =
  "min-w-[13.5rem] overflow-hidden rounded-2xl border-[var(--em-hairline)] bg-[color-mix(in_srgb,var(--background)_90%,var(--em-fill))] p-1.5 text-foreground shadow-[0_12px_36px_rgba(32,40,35,0.12)] backdrop-blur-xl dark:bg-popover/95 dark:shadow-[0_16px_40px_rgba(0,0,0,0.45)]";

const sidebarMenuItemClass =
  "cursor-pointer rounded-xl px-2.5 py-2 text-[13px] font-medium gap-2.5 text-foreground/90 focus:bg-[var(--em-primary-alpha-08)] focus:text-foreground [&_svg]:text-[var(--em-primary-light)]";

const sidebarMenuDangerItemClass =
  "cursor-pointer rounded-xl px-2.5 py-2 text-[13px] font-medium gap-2.5 focus:bg-red-500/[0.08] focus:text-destructive";

/** Simple fuzzy match: checks if all characters in query appear in target in order */
function fuzzyMatch(target: string, query: string): boolean {
  let qi = 0;
  for (let ti = 0; ti < target.length && qi < query.length; ti++) {
    if (target[ti] === query[qi]) qi++;
  }
  return qi === query.length;
}

function isNotFoundError(err: unknown): boolean {
  const msg = err instanceof Error ? err.message : String(err ?? "");
  return /404|not found|不存在/i.test(msg);
}

type SessionStatusTone = "running" | "approval" | "question";

function SessionStatusDot({ tone, label }: { tone: SessionStatusTone; label: string }) {
  return (
    <span
      className={cn("session-status-dot", `session-status-dot--${tone}`)}
      title={label}
      aria-label={label}
    />
  );
}

export function SessionList() {
  const sessions = useSessionStore((s) => s.sessions);
  const activeSessionId = useSessionStore((s) => s.activeSessionId);
  const addSession = useSessionStore((s) => s.addSession);
  const setActiveSession = useSessionStore((s) => s.setActiveSession);
  const removeSession = useSessionStore((s) => s.removeSession);
  const switchSession = useChatStore((s) => s.switchSession);
  const removeSessionCache = useChatStore((s) => s.removeSessionCache);
  const isStreaming = useChatStore((s) => s.isStreaming);
  const chatPendingApproval = useChatStore((s) => s.pendingApproval);
  const chatPendingQuestion = useChatStore((s) => s.pendingQuestion);

  const [busySessionId, setBusySessionId] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState("");
  const [editingSessionId, setEditingSessionId] = useState<string | null>(null);
  const [editValue, setEditValue] = useState("");
  const editInputRef = useRef<HTMLInputElement>(null);
  const [creating, setCreating] = useState(false);
  const [workspaces, setWorkspaces] = useState<WorkspaceFolder[]>([]);
  const [collapsedGroups, setCollapsedGroups] = useState<Set<string>>(new Set());
  const [addFolderOpen, setAddFolderOpen] = useState(false);
  const [editingWorkspace, setEditingWorkspace] = useState<WorkspaceFolder | null>(null);
  const [workspaceMenuKey, setWorkspaceMenuKey] = useState<string | null>(null);

  useEffect(() => {
    if (editingSessionId && editInputRef.current) {
      editInputRef.current.focus();
      editInputRef.current.select();
    }
  }, [editingSessionId]);

  const handleStartEdit = useCallback((sessionId: string, currentTitle: string) => {
    setEditingSessionId(sessionId);
    setEditValue(currentTitle);
  }, []);

  const handleFinishEdit = useCallback(async (sessionId: string) => {
    const trimmed = editValue.trim();
    const currentTitle = sessions.find((s) => s.id === sessionId)?.title;
    if (trimmed && currentTitle && trimmed !== currentTitle) {
      useSessionStore.getState().updateSessionTitle(sessionId, trimmed);
      try {
        await updateSessionTitle(sessionId, trimmed);
      } catch (err) {
        if (!isNotFoundError(err)) {
          useSessionStore.getState().updateSessionTitle(sessionId, currentTitle);
        }
      }
    }
    setEditingSessionId(null);
  }, [editValue, sessions]);

  const handleCancelEdit = useCallback(() => {
    setEditingSessionId(null);
  }, []);

  const handleExport = useCallback(async (sessionId: string, format: ExportFormat) => {
    try {
      await exportSession(sessionId, format);
    } catch (err) {
      console.error("导出失败:", err);
    }
  }, []);

  const handleNewSession = useCallback(async (workspaceId?: string | null, workspacePath?: string | null) => {
    if (creating) return;
    setCreating(true);
    try {
      await createOrReuseSession({ workspaceId, workspacePath });
    } catch (err) {
      console.error("新建对话失败:", err);
    } finally {
      setCreating(false);
    }
  }, [creating]);

  const reloadWorkspaces = useCallback(async () => {
    try {
      setWorkspaces(await fetchWorkspaces());
    } catch {
      setWorkspaces([]);
    }
  }, []);

  useEffect(() => {
    void reloadWorkspaces();
  }, [reloadWorkspaces]);

  const handleDeleteWorkspace = useCallback(async (workspaceId: string) => {
    try {
      await deleteWorkspaceFolder(workspaceId);
      await reloadWorkspaces();
    } catch (err) {
      console.error("删除工作区失败:", err);
    }
  }, [reloadWorkspaces]);

  const filteredSessions = useMemo(
    () => {
      const q = searchQuery.trim().toLowerCase();
      return sessions.filter(
        (session) => !q || fuzzyMatch(session.title.toLowerCase(), q)
      );
    },
    [sessions, searchQuery]
  );

  const groupedSessions = useMemo(() => {
    const assigned = new Set<string>();
    const groups: {
      key: string;
      title: string;
      workspaceId?: string | null;
      path?: string;
      canCreate: boolean;
      canManage: boolean;
      isDefault: boolean;
      sessions: typeof filteredSessions;
    }[] = [];
    for (const ws of workspaces) {
      const items = filteredSessions.filter(
        (s) => (ws.id && s.workspaceId === ws.id) || (ws.path && s.workspacePath === ws.path),
      );
      items.forEach((s) => assigned.add(s.id));
      groups.push({
        key: ws.id || ws.path,
        title: ws.title || ws.path.split(/[\\/]/).pop() || ws.path,
        workspaceId: ws.id,
        path: ws.path,
        canCreate: true,
        canManage: Boolean(ws.id),
        isDefault: Boolean(ws.is_default),
        sessions: items,
      });
    }
    const rest = filteredSessions.filter((s) => !assigned.has(s.id));
    groups.push({
      key: "__ungrouped__",
      title: "未分组",
      canCreate: true,
      canManage: false,
      isDefault: false,
      sessions: rest,
    });
    return groups;
  }, [filteredSessions, workspaces]);

  const toggleGroup = useCallback((key: string) => {
    setCollapsedGroups((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }, []);

  const handleDelete = async (sessionId: string) => {
    if (busySessionId) return;
    setBusySessionId(sessionId);

    const chatState = useChatStore.getState();
    const sessionStore = useSessionStore.getState();
    const prevSessions = sessionStore.sessions;
    const prevActiveSessionId = sessionStore.activeSessionId;
    const isDeletingCurrent = sessionId === prevActiveSessionId;
    const isDeletingActive = isDeletingCurrent;
    const sessionSnapshot = prevSessions.find((s) => s.id === sessionId) ?? null;
    const nextActive = prevSessions.find((s) => s.id !== sessionId);

    // 若删除的是当前正在流式输出的会话，则同时中止前端 SSE
    if (isDeletingCurrent && chatState.abortController) {
      stopGeneration();
    } else {
      // 对非当前会话，仍通知后端取消任务
      abortChat(sessionId).catch(() => {});
    }

    // 乐观移除
    removeSession(sessionId);
    if (isDeletingActive) {
      if (nextActive) {
        setActiveSession(nextActive.id);
        switchSession(nextActive.id);
      } else {
        setActiveSession(null);
        switchSession(null);
      }
    }

    let shouldFinalize = false;
    try {
      await deleteSession(sessionId);
      shouldFinalize = true;
    } catch (err) {
      if (isNotFoundError(err)) {
        shouldFinalize = true;
      } else {
        const hasSession = useSessionStore
          .getState()
          .sessions.some((s) => s.id === sessionId);
        if (!hasSession && sessionSnapshot) addSession(sessionSnapshot);
        if (isDeletingActive) {
          setActiveSession(prevActiveSessionId);
          switchSession(prevActiveSessionId);
        }
      }
    } finally {
      if (shouldFinalize) {
        // 确认删除完成后清理本地消息缓存
        removeSessionCache(sessionId);
      }
      setBusySessionId((cur) => (cur === sessionId ? null : cur));
    }
  };

  const searchAndNewRow = (
    <div className="flex items-center gap-1.5 px-1 pt-2 pb-1.5 flex-shrink-0">
      <div className="relative flex-1 min-w-0">
        <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-muted-foreground/50 pointer-events-none" />
        <input
          type="text"
          placeholder="搜索对话…"
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
          className={`w-full h-8 pl-8 text-[13px] rounded-lg border border-border/60 bg-background/80 outline-none placeholder:text-muted-foreground/50 focus:border-[var(--em-primary)] focus:ring-2 focus:ring-[var(--em-primary-alpha-15)] transition-all duration-200 ${searchQuery ? "pr-8" : "pr-3"}`}
        />
        {searchQuery ? (
          <button
            onClick={() => setSearchQuery("")}
            className="absolute right-2.5 top-1/2 -translate-y-1/2 h-4 w-4 flex items-center justify-center rounded-full bg-muted text-muted-foreground hover:bg-muted-foreground/20 hover:text-foreground transition-colors touch-compact"
          >
            <X className="h-3 w-3" />
          </button>
        ) : null}
      </div>
      <Button
        className="shrink-0 text-white rounded-xl"
        size="icon-sm"
        style={{ backgroundColor: "var(--em-primary)" }}
        onClick={() => {
          setEditingWorkspace(null);
          setAddFolderOpen(true);
        }}
        title="添加工作区"
        aria-label="添加工作区"
      >
        <FolderPlus className="h-4 w-4" />
      </Button>
    </div>
  );

  return (
    <div className="flex flex-col h-full">
      {searchAndNewRow}
      <ScrollArea className="flex-1 min-h-0">
        <div className="space-y-2 pb-2">
            {searchQuery && filteredSessions.length === 0 ? (
              <div className="flex flex-col items-center gap-2 px-2 py-6 text-muted-foreground">
                <MessageSquare
                  className="h-6 w-6 animate-empty-float"
                  style={{ color: "var(--em-primary)" }}
                />
                <p className="text-xs">暂无匹配对话</p>
              </div>
            ) : (
              <AnimatePresence mode="popLayout">
                <div className="space-y-3">
                  {groupedSessions.map((group) => {
                    const collapsed = collapsedGroups.has(group.key);
                    return (
                      <div key={group.key} className="space-y-0.5">
                        <div
                          className={cn(
                            "group/ws flex items-center gap-1 rounded-md px-2 py-1 text-[var(--em-primary-light)] transition-colors",
                            "hover:bg-[var(--em-primary-alpha-06)] hover:text-[var(--em-primary)]",
                            "focus-within:bg-[var(--em-primary-alpha-06)] focus-within:text-[var(--em-primary)]",
                            workspaceMenuKey === group.key && "bg-[var(--em-primary-alpha-06)] text-[var(--em-primary)]",
                          )}
                        >
                          <button
                            type="button"
                            className="flex min-w-0 flex-1 items-center gap-1.5 rounded-md py-0.5 text-left"
                            onClick={() => toggleGroup(group.key)}
                          >
                            <span className="relative h-3.5 w-3.5 shrink-0 text-current">
                              <Folder
                                className={cn(
                                  "pointer-events-none absolute inset-0 h-3.5 w-3.5 text-current transition-opacity group-hover/ws:opacity-0 group-focus-within/ws:opacity-0",
                                  workspaceMenuKey === group.key && "opacity-0",
                                )}
                              />
                              <ChevronDown
                                className={cn(
                                  "pointer-events-none absolute inset-0 h-3.5 w-3.5 text-current opacity-0 transition-[opacity,transform] group-hover/ws:opacity-100 group-focus-within/ws:opacity-100",
                                  workspaceMenuKey === group.key && "opacity-100",
                                  collapsed && "-rotate-90",
                                )}
                              />
                            </span>
                            <span className="min-w-0 truncate text-[12px] font-medium tracking-wide text-current" title={group.path || group.title}>
                              {group.title}
                            </span>
                          </button>
                          {group.canManage ? (
                            <DropdownMenu
                              open={workspaceMenuKey === group.key}
                              onOpenChange={(open) => setWorkspaceMenuKey(open ? group.key : null)}
                            >
                              <DropdownMenuTrigger asChild>
                                <Button
                                  variant="ghost"
                                  size="icon-sm"
                                  className={cn(
                                    "h-6 w-6 shrink-0 text-current opacity-0 pointer-events-none transition-opacity hover:bg-[var(--em-primary-alpha-10)] hover:text-current",
                                    "group-hover/ws:opacity-100 group-hover/ws:pointer-events-auto",
                                    "group-focus-within/ws:opacity-100 group-focus-within/ws:pointer-events-auto",
                                    "pointer-coarse:opacity-100 pointer-coarse:pointer-events-auto",
                                    workspaceMenuKey === group.key && "opacity-100 pointer-events-auto",
                                  )}
                                  title={`管理「${group.title}」`}
                                  aria-label={`管理「${group.title}」`}
                                >
                                  <Ellipsis className="h-3.5 w-3.5 text-current" />
                                </Button>
                              </DropdownMenuTrigger>
                              <DropdownMenuContent side="bottom" align="end" sideOffset={6} className={sidebarMenuPanelClass}>
                                <DropdownMenuItem
                                  className={sidebarMenuItemClass}
                                  onClick={() => {
                                    const current = workspaces.find((ws) => ws.id === group.workspaceId);
                                    setWorkspaceMenuKey(null);
                                    setAddFolderOpen(false);
                                    setEditingWorkspace(current ?? {
                                      id: group.workspaceId || "",
                                      path: group.path || "",
                                      title: group.title,
                                      is_default: group.isDefault,
                                    });
                                  }}
                                >
                                  <Pencil className="h-4 w-4" />
                                  编辑工作区
                                </DropdownMenuItem>
                                <DropdownMenuSeparator className="mx-1 my-1.5 bg-[var(--em-hairline)]" />
                                <DropdownMenuItem
                                  variant="destructive"
                                  className={sidebarMenuDangerItemClass}
                                  disabled={group.isDefault || !group.workspaceId}
                                  title={group.isDefault ? "不能删除默认工作区" : undefined}
                                  onClick={() => {
                                    if (!group.workspaceId || group.isDefault) return;
                                    setWorkspaceMenuKey(null);
                                    void handleDeleteWorkspace(group.workspaceId);
                                  }}
                                >
                                  <Trash2 className="h-4 w-4" />
                                  删除
                                </DropdownMenuItem>
                              </DropdownMenuContent>
                            </DropdownMenu>
                          ) : null}
                          {group.canCreate ? (
                            <Button
                              variant="ghost"
                              size="icon-sm"
                              className={cn(
                                "h-6 w-6 shrink-0 text-current opacity-0 pointer-events-none transition-opacity hover:bg-[var(--em-primary-alpha-10)] hover:text-current",
                                "group-hover/ws:opacity-100 group-hover/ws:pointer-events-auto",
                                "group-focus-within/ws:opacity-100 group-focus-within/ws:pointer-events-auto",
                                "pointer-coarse:opacity-100 pointer-coarse:pointer-events-auto",
                                workspaceMenuKey === group.key && "opacity-100 pointer-events-auto",
                              )}
                              title={`在「${group.title}」新建对话`}
                              aria-label={`在「${group.title}」新建对话`}
                              disabled={creating}
                              onClick={() => void handleNewSession(group.workspaceId, group.path)}
                            >
                              <Plus className="h-3.5 w-3.5 text-current" />
                            </Button>
                          ) : null}
                        </div>
                        {collapsed ? null : group.sessions.map((session) => {
                    const isActive = session.id === activeSessionId;
                    const awaitingApproval =
                      Boolean(session.pendingApproval) || (isActive && Boolean(chatPendingApproval));
                    const awaitingQuestion =
                      !awaitingApproval
                      && (Boolean(session.pendingQuestion) || (isActive && Boolean(chatPendingQuestion)));
                    const isRunning =
                      !awaitingApproval
                      && !awaitingQuestion
                      && (session.inFlight || (isActive && isStreaming));
                    const statusTone: SessionStatusTone | null = awaitingApproval
                      ? "approval"
                      : awaitingQuestion
                        ? "question"
                        : isRunning
                          ? "running"
                          : null;
                    const statusLabel =
                      statusTone === "approval"
                        ? "等待审批"
                        : statusTone === "question"
                          ? "等待回复"
                          : "正在进行";
                    const isEditing = editingSessionId === session.id;

                    return (
                      <motion.div
                        key={session.id}
                        variants={listItemVariants}
                        initial="initial"
                        animate="animate"
                        exit="exit"
                        layout
                        className={cn(
                          "group relative mx-2 flex min-w-0 cursor-pointer items-center gap-1 rounded-xl py-1.5 pr-1 pl-[calc(0.875rem+0.375rem)] transition-colors",
                          awaitingApproval
                            ? isActive
                              ? "bg-[color-mix(in_srgb,var(--em-gold)_18%,transparent)]"
                              : "bg-[color-mix(in_srgb,var(--em-gold)_10%,transparent)] hover:bg-[color-mix(in_srgb,var(--em-gold)_16%,transparent)]"
                            : awaitingQuestion
                              ? isActive
                                ? "bg-[color-mix(in_srgb,var(--em-cyan)_14%,transparent)]"
                                : "bg-[color-mix(in_srgb,var(--em-cyan)_8%,transparent)] hover:bg-[color-mix(in_srgb,var(--em-cyan)_12%,transparent)]"
                              : isActive
                                ? "bg-black/[0.055] dark:bg-white/[0.09]"
                                : "hover:bg-black/[0.035] dark:hover:bg-white/[0.06]",
                        )}
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
                        {statusTone ? (
                          <span className="absolute top-1/2 left-0 flex h-3.5 w-3.5 -translate-y-1/2 items-center justify-center">
                            <SessionStatusDot tone={statusTone} label={statusLabel} />
                          </span>
                        ) : null}
                        <div className="flex-1 min-w-0">
                          <AnimatePresence mode="wait">
                            {isEditing ? (
                              <motion.div
                                key="edit"
                                initial={{ opacity: 0, scale: 0.97 }}
                                animate={{ opacity: 1, scale: 1 }}
                                exit={{ opacity: 0, scale: 0.97 }}
                                transition={{ duration: 0.15 }}
                                className="flex items-center gap-1"
                                onClick={(e) => e.stopPropagation()}
                              >
                                <input
                                  ref={editInputRef}
                                  type="text"
                                  value={editValue}
                                  onChange={(e) => setEditValue(e.target.value)}
                                  onKeyDown={(e) => {
                                    if (e.key === "Enter") {
                                      e.preventDefault();
                                      void handleFinishEdit(session.id);
                                    } else if (e.key === "Escape") {
                                      handleCancelEdit();
                                    }
                                    e.stopPropagation();
                                  }}
                                  className="session-rename-input flex-1 min-w-0 h-7 text-[13px] bg-background/90 text-foreground rounded-lg px-2 outline-none border border-[var(--em-primary)]/40 focus:border-[var(--em-primary)] focus:ring-2 focus:ring-[var(--em-primary-alpha-15)] transition-all duration-200"
                                />
                                <button
                                  onClick={(e) => {
                                    e.stopPropagation();
                                    void handleFinishEdit(session.id);
                                  }}
                                  className="flex-shrink-0 h-7 w-7 flex items-center justify-center rounded-md text-white transition-colors duration-150 hover:opacity-90"
                                  style={{ backgroundColor: "var(--em-primary)" }}
                                  title="确认"
                                >
                                  <Check className="h-3 w-3" />
                                </button>
                                <button
                                  onClick={(e) => {
                                    e.stopPropagation();
                                    handleCancelEdit();
                                  }}
                                  className="flex-shrink-0 h-7 w-7 flex items-center justify-center rounded-md text-muted-foreground bg-muted hover:bg-muted-foreground/20 hover:text-foreground transition-colors duration-150"
                                  title="取消"
                                >
                                  <X className="h-3 w-3" />
                                </button>
                              </motion.div>
                            ) : (
                              <motion.div
                                key="display"
                                initial={false}
                                animate={{ opacity: 1 }}
                                className="min-w-0"
                              >
                                <span
                                  className={cn(
                                    "block min-w-0 truncate text-[13px] leading-snug",
                                    isActive
                                      ? "font-medium text-foreground"
                                      : "font-normal text-foreground/80",
                                  )}
                                  title={session.title}
                                  onDoubleClick={(e) => {
                                    e.stopPropagation();
                                    handleStartEdit(session.id, session.title);
                                  }}
                                >
                                  {session.title}
                                </span>
                              </motion.div>
                            )}
                          </AnimatePresence>
                        </div>

                        {/* Three-dot menu — visible on hover or when active */}
                        {!isEditing && (
                          <DropdownMenu>
                            <DropdownMenuTrigger asChild>
                              <button
                                className={cn(
                                  "flex h-6 w-6 flex-shrink-0 items-center justify-center rounded-lg text-muted-foreground transition-all duration-150",
                                  "hover:bg-[var(--em-primary-alpha-10)] hover:text-[var(--em-primary)]",
                                  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--em-primary)]",
                                  "data-[state=open]:bg-[var(--em-primary-alpha-10)] data-[state=open]:text-[var(--em-primary)] data-[state=open]:opacity-100",
                                  isActive
                                    ? "opacity-100"
                                    : "opacity-0 group-hover:opacity-100 touch-show",
                                )}
                                onClick={(e) => e.stopPropagation()}
                              >
                                <Ellipsis className="h-4 w-4" />
                              </button>
                            </DropdownMenuTrigger>
                            <DropdownMenuContent
                              side="bottom"
                              align="end"
                              sideOffset={6}
                              collisionPadding={8}
                              className={sidebarMenuPanelClass}
                            >
                              <DropdownMenuItem
                                className={sidebarMenuItemClass}
                                onClick={(e) => {
                                  e.stopPropagation();
                                  handleStartEdit(session.id, session.title);
                                }}
                              >
                                <Pencil className="h-4 w-4" />
                                重命名
                              </DropdownMenuItem>
                              <DropdownMenuSeparator className="mx-1 my-1.5 bg-[var(--em-hairline)]" />
                              <div className="flex items-center gap-1.5 px-2.5 pb-1 pt-0.5">
                                <Download className="h-3 w-3 text-[var(--em-primary-light)]" />
                                <span className="text-[11px] font-medium tracking-wide text-muted-foreground">导出</span>
                              </div>
                              <DropdownMenuItem
                                className={sidebarMenuItemClass}
                                onClick={(e) => {
                                  e.stopPropagation();
                                  void handleExport(session.id, "md");
                                }}
                              >
                                <FileText className="h-4 w-4" />
                                Markdown
                                <span className="ml-auto text-[11px] font-normal text-muted-foreground/70">.md</span>
                              </DropdownMenuItem>
                              <DropdownMenuItem
                                className={sidebarMenuItemClass}
                                onClick={(e) => {
                                  e.stopPropagation();
                                  void handleExport(session.id, "json");
                                }}
                              >
                                <FileJson className="h-4 w-4" />
                                JSON
                                <span className="ml-auto text-[11px] font-normal text-muted-foreground/70">.json</span>
                              </DropdownMenuItem>
                              <DropdownMenuSeparator className="mx-1 my-1.5 bg-[var(--em-hairline)]" />
                              <DropdownMenuItem
                                variant="destructive"
                                className={sidebarMenuDangerItemClass}
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
                        )}
                      </motion.div>
                    );
                  })}
                      </div>
                    );
                  })}
                </div>
              </AnimatePresence>
            )}
          </div>
      </ScrollArea>
      <AddWorkspaceDialog
        open={addFolderOpen || Boolean(editingWorkspace)}
        workspace={editingWorkspace}
        onOpenChange={(open) => {
          if (!open) {
            setAddFolderOpen(false);
            setEditingWorkspace(null);
          }
        }}
        onCreated={reloadWorkspaces}
      />
    </div>
  );
}
