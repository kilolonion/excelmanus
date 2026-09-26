"use client";

import { memo, useMemo, useState, useCallback, useRef, useEffect, type DragEvent } from "react";
import { defaultRangeExtractor, useVirtualizer } from "@tanstack/react-virtual";
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
  Folder,
  FolderOpen,
  FolderPlus,
  Inbox,
  LoaderCircle,
  ChevronDown,
  Plus,
  CheckCheck,
  ExternalLink,
  Clipboard,
} from "lucide-react";
import { motion, AnimatePresence } from "framer-motion";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  deleteSession,
  abortChat,
  updateSessionTitle,
  exportSession,
  apiPost,
  type ExportFormat,
  fetchWorkspaces,
  reorderWorkspaces,
  deleteWorkspaceFolder,
} from "@/lib/api";
import { createOrReuseSession } from "@/lib/session-actions";
import type { WorkspaceFolder } from "@/lib/types";
import { stopGeneration } from "@/lib/chat-actions";
import { useSessionStore } from "@/stores/session-store";
import { useChatStore } from "@/stores/chat-store";
import { useAuthConfigStore } from "@/stores/auth-config-store";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { cn } from "@/lib/utils";
import { applySidebarOrder, moveSidebarItem, type DropSide } from "@/lib/sidebar-order";
import { AddWorkspaceDialog } from "@/components/sidebar/AddWorkspaceDialog";
import {
  glassMenuDangerItemClass,
  glassMenuItemClass,
  glassMenuPanelClass,
} from "@/components/ui/menu-panel";

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
type SidebarDrag = { kind: "workspace" | "session"; id: string; groupKey: string };
type SidebarDrop = { id: string; side: DropSide; rowKey: string };

function SessionStatusDot({ tone, label }: { tone: SessionStatusTone; label: string }) {
  return (
    <span
      className={cn("session-status-dot", `session-status-dot--${tone}`)}
      title={label}
      aria-label={label}
    />
  );
}

export const SessionList = memo(function SessionList() {
  const viewportRef = useRef<HTMLDivElement>(null);
  const sessions = useSessionStore((s) => s.sessions);
  const sidebarSessionOrder = useSessionStore((s) => s.sidebarSessionOrder);
  const readAtByWorkspace = useSessionStore((s) => s.readAtByWorkspace);
  const markWorkspaceRead = useSessionStore((s) => s.markWorkspaceRead);
  const activeSessionId = useSessionStore((s) => s.activeSessionId);
  const addSession = useSessionStore((s) => s.addSession);
  const setActiveSession = useSessionStore((s) => s.setActiveSession);
  const removeSession = useSessionStore((s) => s.removeSession);
  const switchSession = useChatStore((s) => s.switchSession);
  const removeSessionCache = useChatStore((s) => s.removeSessionCache);
  const isStreaming = useChatStore((s) => s.isStreaming);
  const chatPendingApproval = useChatStore((s) => s.pendingApproval);
  const chatPendingQuestion = useChatStore((s) => s.pendingQuestion);
  const deployMode = useAuthConfigStore((s) => s.deployMode);
  const [fileManagerName, setFileManagerName] = useState("文件管理器");

  const [busySessionId, setBusySessionId] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState("");
  useEffect(() => { viewportRef.current?.scrollTo({ top: 0 }); }, [searchQuery]);
  const [editingSessionId, setEditingSessionId] = useState<string | null>(null);
  const [editValue, setEditValue] = useState("");
  const editInputRef = useRef<HTMLInputElement>(null);
  const [creating, setCreating] = useState(false);
  const [creatingWorkspaceKey, setCreatingWorkspaceKey] = useState<string | null>(null);
  const [createError, setCreateError] = useState<string | null>(null);
  const [workspaces, setWorkspaces] = useState<WorkspaceFolder[]>([]);
  const [collapsedGroups, setCollapsedGroups] = useState<Set<string>>(new Set());
  const [addFolderOpen, setAddFolderOpen] = useState(false);
  const [editingWorkspace, setEditingWorkspace] = useState<WorkspaceFolder | null>(null);
  const [workspaceMenuKey, setWorkspaceMenuKey] = useState<string | null>(null);
  const [sessionMenuId, setSessionMenuId] = useState<string | null>(null);
  const [drag, setDrag] = useState<SidebarDrag | null>(null);
  const [dragSessions, setDragSessions] = useState<typeof sessions | null>(null);
  const [dropTarget, setDropTarget] = useState<SidebarDrop | null>(null);
  const [orderError, setOrderError] = useState<string | null>(null);
  const [reorderingWorkspace, setReorderingWorkspace] = useState(false);
  const deletingSessionIdsRef = useRef(new Set<string>());
  const deletingWorkspaceSessionsRef = useRef(false);
  const dragPreview = useRef<HTMLElement | null>(null);
  const dragBlocked = useRef(false);
  const dragRef = useRef<SidebarDrag | null>(null);
  const dragPointerY = useRef<number | null>(null);
  const dragScrollFrame = useRef<number | null>(null);

  useEffect(() => {
    const platform = typeof navigator === "undefined" ? "" : navigator.userAgent.toLowerCase();
    if (platform.includes("mac os") || platform.includes("macintosh")) setFileManagerName("Finder");
    else if (platform.includes("windows")) setFileManagerName("资源管理器");
    else if (platform.includes("linux")) setFileManagerName("文件管理器");
  }, []);

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

  const handleNewSession = useCallback(async (
    workspaceId?: string | null,
    workspacePath?: string | null,
    workspaceKey?: string,
  ) => {
    if (creating) return;
    setCreating(true);
    setCreatingWorkspaceKey(workspaceKey ?? "__global__");
    setCreateError(null);
    try {
      // The global “new chat” button has no group argument. Inherit the
      // active conversation's workspace so a new chat keeps seeing the same
      // uploaded/recent files instead of silently landing in another folder.
      // Group-specific + buttons still use their explicit scope.
      const sessionState = useSessionStore.getState();
      const active = sessionState.sessions.find(
        (session) => session.id === sessionState.activeSessionId,
      );
      const scopedWorkspaceId = workspaceId === undefined ? active?.workspaceId : workspaceId;
      const scopedWorkspacePath = workspacePath === undefined ? active?.workspacePath : workspacePath;
      await createOrReuseSession({
        workspaceId: scopedWorkspaceId,
        workspacePath: scopedWorkspacePath,
        reuseBlank: false,
      });
    } catch (err) {
      console.error("新建对话失败:", err);
      setCreateError(err instanceof Error ? err.message : "新建对话失败，请检查后端服务后重试");
    } finally {
      setCreating(false);
      setCreatingWorkspaceKey(null);
    }
  }, [creating]);

  const reloadWorkspaces = useCallback(async () => {
    try {
      setWorkspaces(await fetchWorkspaces());
    } catch {
      setWorkspaces([]);
    }
  }, []);

  const handleDragStart = useCallback((event: DragEvent<HTMLElement>, source: SidebarDrag) => {
    if (reorderingWorkspace || dragBlocked.current) {
      event.preventDefault();
      return;
    }
    // A cancelled native drag may not reach the original (virtualized) row.
    // Never carry its preview into the next gesture.
    dragPreview.current?.remove();
    const element = event.currentTarget;
    const rect = element.getBoundingClientRect();
    // Snapshot only the header/card, before dimming the stationary source rows.
    const preview = element.cloneNode(true) as HTMLElement;
    preview.setAttribute("aria-hidden", "true");
    preview.querySelectorAll("button, input").forEach((child) => child.setAttribute("tabindex", "-1"));
    Object.assign(preview.style, {
      position: "fixed", top: "-10000px", left: "0", width: `${rect.width}px`,
      margin: "0", border: "1px solid color-mix(in srgb, var(--em-primary) 28%, transparent)",
      borderRadius: "14px", background: "linear-gradient(135deg, color-mix(in srgb, var(--em-primary) 12%, var(--em-panel)), var(--em-panel))",
      boxShadow: "0 18px 42px color-mix(in srgb, var(--em-primary) 25%, #0003), 0 3px 10px #0002",
      transform: "rotate(1.2deg) scale(1.02)", opacity: "0.96", overflow: "hidden", pointerEvents: "none",
    });
    document.body.appendChild(preview);
    dragPreview.current = preview;
    event.dataTransfer.setDragImage(preview, event.clientX - rect.left, event.clientY - rect.top);
    dragRef.current = source;
    dragPointerY.current = event.clientY;
    setDrag(source);
    setDragSessions(sessions);
    setDropTarget(null);
    setOrderError(null);
    event.dataTransfer.effectAllowed = "move";
    event.dataTransfer.setData("text/plain", source.id);
  }, [reorderingWorkspace, sessions]);

  const stopDragAutoScroll = useCallback(() => {
    dragPointerY.current = null;
    if (dragScrollFrame.current !== null) {
      window.cancelAnimationFrame(dragScrollFrame.current);
      dragScrollFrame.current = null;
    }
  }, []);

  const updateDragAutoScroll = useCallback((clientY: number) => {
    dragPointerY.current = clientY;
    if (dragScrollFrame.current !== null) return;

    const step = () => {
      const viewport = viewportRef.current;
      const pointerY = dragPointerY.current;
      if (!dragRef.current || !viewport || pointerY === null) {
        dragScrollFrame.current = null;
        return;
      }

      const rect = viewport.getBoundingClientRect();
      const edgeSize = Math.min(112, Math.max(56, rect.height * 0.2));
      const distanceFromTop = pointerY - rect.top;
      const distanceFromBottom = rect.bottom - pointerY;
      let direction = 0;
      let distance = 0;
      if (distanceFromTop < edgeSize && viewport.scrollTop > 0) {
        direction = -1;
        distance = edgeSize - distanceFromTop;
      } else if (
        distanceFromBottom < edgeSize
        && viewport.scrollTop < viewport.scrollHeight - viewport.clientHeight
      ) {
        direction = 1;
        distance = edgeSize - distanceFromBottom;
      }

      if (direction === 0) {
        dragScrollFrame.current = null;
        return;
      }

      const intensity = Math.min(1, distance / edgeSize);
      viewport.scrollTop += direction * (3 + Math.round(15 * intensity ** 1.35));
      dragScrollFrame.current = window.requestAnimationFrame(step);
    };

    dragScrollFrame.current = window.requestAnimationFrame(step);
  }, []);

  const clearDrag = useCallback(() => {
    dragBlocked.current = false;
    dragRef.current = null;
    stopDragAutoScroll();
    setDrag(null);
    setDragSessions(null);
    setDropTarget(null);
    dragPreview.current?.remove();
    dragPreview.current = null;
  }, [stopDragAutoScroll]);

  useEffect(() => {
    // Native dragend/drop can target the document or another component after
    // a reorder. Clean up outside the list too. Drop must bubble so the row
    // can save its new order before we release the frozen rows.
    const finishDrag = () => {
      if (dragPreview.current) clearDrag();
    };
    window.addEventListener("dragend", finishDrag, true);
    window.addEventListener("drop", finishDrag);
    // Starting another pointer gesture also recovers a missing dragend.
    window.addEventListener("pointerdown", finishDrag, true);
    return () => {
      window.removeEventListener("dragend", finishDrag, true);
      window.removeEventListener("drop", finishDrag);
      window.removeEventListener("pointerdown", finishDrag, true);
      stopDragAutoScroll();
      dragRef.current = null;
      dragPreview.current?.remove();
    };
  }, [clearDrag, stopDragAutoScroll]);

  const handleViewportDragOver = useCallback((event: DragEvent<HTMLDivElement>) => {
    if (!dragRef.current) return;
    event.preventDefault();
    updateDragAutoScroll(event.clientY);
  }, [updateDragAutoScroll]);

  const handleViewportDragLeave = useCallback((event: DragEvent<HTMLDivElement>) => {
    if (!event.currentTarget.contains(event.relatedTarget as Node | null)) stopDragAutoScroll();
  }, [stopDragAutoScroll]);

  const saveWorkspaceOrder = useCallback(async (sourceId: string, target: SidebarDrop) => {
    const previous = workspaces;
    const next = moveSidebarItem(previous, sourceId, target.id, target.side);
    if (next === previous) return;
    setWorkspaces(next);
    setReorderingWorkspace(true);
    try {
      const persisted = await reorderWorkspaces(next.map((workspace) => workspace.id));
      if (persisted.length > 0) setWorkspaces(persisted);
    } catch (err) {
      console.error("保存工作区顺序失败:", err);
      setWorkspaces(previous);
      setOrderError("工作区顺序保存失败，已恢复原顺序，请重试。");
    } finally {
      setReorderingWorkspace(false);
    }
  }, [workspaces]);

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
      return (dragSessions ?? sessions).filter(
        (session) => !q || fuzzyMatch(session.title.toLowerCase(), q)
      );
    },
    [sessions, dragSessions, searchQuery]
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
      const items = applySidebarOrder(filteredSessions.filter(
        (s) => (ws.id && s.workspaceId === ws.id) || (ws.path && s.workspacePath === ws.path),
      ), sidebarSessionOrder[ws.id || ws.path]);
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
      sessions: applySidebarOrder(rest, sidebarSessionOrder.__ungrouped__),
    });
    return groups;
  }, [filteredSessions, workspaces, sidebarSessionOrder]);

  // Flatten headers and expanded sessions so even a large workspace history
  // renders only the viewport. Collapsed sessions do not retain hidden DOM.
  const rows = useMemo(() => groupedSessions.flatMap((group) => {
    const items: { key: string; group: typeof group; session?: typeof sessions[number] }[] = [
      { key: `group:${group.key}`, group },
    ];
    if (!collapsedGroups.has(group.key)) {
      for (const session of group.sessions) items.push({ key: `${group.key}:${session.id}`, group, session });
    }
    return items;
  }), [groupedSessions, collapsedGroups]);
  const pinnedIndices = rows.flatMap((row, index) =>
    (row.session && (row.session.id === editingSessionId || row.session.id === sessionMenuId))
      || (!row.session && row.group.key === workspaceMenuKey)
      || (drag?.kind === "workspace" && !row.session && row.group.key === drag.groupKey)
      || (drag?.kind === "session" && row.session?.id === drag.id) ? [index] : []);
  const virtualizer = useVirtualizer({
    count: rows.length,
    getScrollElement: () => viewportRef.current,
    getItemKey: (index) => rows[index].key,
    estimateSize: () => 44,
    overscan: 5,
    rangeExtractor: (range) => [...new Set([...defaultRangeExtractor(range), ...pinnedIndices])].sort((a, b) => a - b),
  });

  const getDropTarget = (event: DragEvent<HTMLDivElement>, index: number): SidebarDrop | null => {
    if (!drag) return null;
    const row = rows[index];
    const rect = event.currentTarget.getBoundingClientRect();
    const side: DropSide = event.clientY < rect.top + rect.height / 2 ? "before" : "after";
    if (drag.kind === "session") {
      if (row.group.key !== drag.groupKey || !row.session || row.session.id === drag.id) return null;
      return { id: row.session.id, side, rowKey: row.key };
    }
    if (!row.group.workspaceId || row.group.key === drag.groupKey) return null;
    const groupRows = rows.filter((item) => item.group.key === row.group.key);
    const groupSide = row.session ? "after" : side;
    return {
      id: row.group.workspaceId, side: groupSide,
      rowKey: groupSide === "before" ? groupRows[0].key : groupRows[groupRows.length - 1].key,
    };
  };

  const handleDrop = (event: DragEvent<HTMLDivElement>, index: number) => {
    const target = getDropTarget(event, index);
    if (!drag) return;
    event.preventDefault();
    if (target) {
      if (drag.kind === "workspace") {
        void saveWorkspaceOrder(drag.id, target);
      } else {
        // Include hidden search results so their relative order survives a filtered drag.
        const group = rows[index].group;
        const members = sessions.filter((session) => group.workspaceId
          ? session.workspaceId === group.workspaceId || Boolean(group.path && session.workspacePath === group.path)
          : !workspaces.some((ws) => session.workspaceId === ws.id || Boolean(ws.path && session.workspacePath === ws.path)));
        const ordered = applySidebarOrder(members, sidebarSessionOrder[drag.groupKey]);
        const next = moveSidebarItem(ordered, drag.id, target.id, target.side);
        useSessionStore.getState().setSidebarSessionOrder(drag.groupKey, next.map((session) => session.id));
      }
    }
    clearDrag();
  };

  const toggleGroup = useCallback((key: string) => {
    setCollapsedGroups((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }, []);

  const handleDelete = useCallback(async (sessionId: string, fromWorkspaceDelete = false) => {
    if (
      deletingSessionIdsRef.current.has(sessionId)
      || (!fromWorkspaceDelete && busySessionId)
      || (deletingWorkspaceSessionsRef.current && !fromWorkspaceDelete)
    ) return;
    deletingSessionIdsRef.current.add(sessionId);
    setBusySessionId(sessionId);

    const chatState = useChatStore.getState();
    const sessionStore = useSessionStore.getState();
    const prevSessions = sessionStore.sessions;
    const prevActiveSessionId = sessionStore.activeSessionId;
    const isDeletingCurrent = sessionId === prevActiveSessionId;
    const isDeletingActive = isDeletingCurrent;
    const sessionSnapshot = prevSessions.find((s) => s.id === sessionId) ?? null;
    const nextActive = prevSessions.find((s) => s.id !== sessionId);

    // 若删除的是当前正在流式输出的会话，则同时中止前端 SSE。
    if (isDeletingCurrent && chatState.abortController) {
      stopGeneration();
    }

    // 先从侧栏乐观移除并写入删除 tombstone；这样重叠的会话轮询不会
    // 把旧列表重新插回来，取消请求耗时也不会让界面停留在旧状态。
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

    // 等待后端取消请求完成后再发 DELETE，避免后端仍处于 in_flight
    // 状态而返回 409，导致乐观移除的会话被回滚。
    try {
      await abortChat(sessionId);
    } catch {
      // The DELETE below is authoritative; an already-finished task or a
      // temporarily unavailable abort endpoint must not block it.
    }

    let shouldFinalize = false;
    try {
      for (let attempt = 0; ; attempt += 1) {
        try {
          await deleteSession(sessionId);
          break;
        } catch (err) {
          const status = (err as { status?: unknown })?.status;
          if (status !== 409 || attempt >= 4) throw err;
          await new Promise((resolve) => setTimeout(resolve, 100 * (attempt + 1)));
        }
      }
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
      deletingSessionIdsRef.current.delete(sessionId);
      setBusySessionId((cur) => (cur === sessionId ? null : cur));
    }
  }, [addSession, busySessionId, removeSession, removeSessionCache, setActiveSession, switchSession]);

  const handleRevealWorkspace = useCallback(async (workspacePath: string, workspaceId?: string | null) => {
    if (deployMode !== "standalone") {
      try {
        await navigator.clipboard.writeText(workspacePath);
        setCreateError("服务器模式无法打开本机文件管理器，工作区路径已复制。");
      } catch {
        setCreateError("服务器模式无法打开本机文件管理器，请手动复制工作区路径。");
      }
      return;
    }
    try {
      await apiPost("/files/reveal", { path: ".", workspace_id: workspaceId || undefined });
    } catch (err) {
      setCreateError(err instanceof Error ? err.message : "无法打开工作区所在文件夹");
    }
  }, [deployMode]);

  const handleDeleteWorkspaceSessions = useCallback(async (sessionIds: string[]) => {
    if (
      sessionIds.length === 0
      || deletingWorkspaceSessionsRef.current
      || deletingSessionIdsRef.current.size > 0
    ) return;
    deletingWorkspaceSessionsRef.current = true;
    // Reuse the existing per-session deletion path so active streams are
    // stopped and local message caches are cleaned up consistently.
    try {
      for (const sessionId of sessionIds) {
        await handleDelete(sessionId, true);
      }
    } finally {
      deletingWorkspaceSessionsRef.current = false;
    }
  }, [handleDelete]);

  const searchRow = (
    <div className="em-session-tools flex flex-col px-1 pt-3 pb-2 flex-shrink-0">
      <Button
        className="em-new-session-button group w-full justify-between rounded-xl px-3.5 text-white shadow-sm"
        onClick={() => void handleNewSession()}
        disabled={creating}
        aria-busy={creating}
      >
        <span className="flex items-center gap-2">
          <span className="em-new-session-icon flex h-6 w-6 items-center justify-center rounded-lg bg-white/15">
            {creatingWorkspaceKey === "__global__"
              ? <LoaderCircle className="h-3.5 w-3.5 animate-spin" />
              : <Plus className="h-3.5 w-3.5" />}
          </span>
          <span className="text-[12px] font-semibold tracking-wide">新建对话</span>
        </span>
        <span className="em-new-session-hint text-[10px] font-medium text-white/65">开始工作</span>
      </Button>
      <div className="mt-2 flex items-center gap-2">
        <div className="relative min-w-0 flex-1">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-muted-foreground/50 pointer-events-none" />
          <input
            type="text"
            placeholder="搜索对话…"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className={`em-session-search w-full h-9 pl-8 text-[12px] rounded-xl border border-[var(--em-line)] bg-card/80 outline-none placeholder:text-muted-foreground/50 focus:border-[var(--em-primary)] focus:ring-2 focus:ring-[var(--em-primary-alpha-15)] transition-all duration-200 ${searchQuery ? "pr-8" : "pr-3"}`}
          />
          {searchQuery ? (
            <button
              onClick={() => setSearchQuery("")}
              className="absolute right-2.5 top-1/2 -translate-y-1/2 h-4 w-4 flex items-center justify-center rounded-full bg-muted text-muted-foreground hover:bg-muted-foreground/20 hover:text-foreground transition-colors touch-compact"
              aria-label="清除搜索"
            >
              <X className="h-3 w-3" />
            </button>
          ) : null}
        </div>
        <Button
          className="em-add-workspace-button shrink-0 rounded-xl shadow-sm"
          size="icon-sm"
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
      <div className="em-session-summary mt-2 flex items-center justify-between px-1 text-[10px] font-medium tracking-wide text-muted-foreground/65">
        <span>{searchQuery ? "搜索结果" : "最近对话"}</span>
        <span>{filteredSessions.length} 个对话</span>
      </div>
      {createError ? (
        <div role="alert" className="mt-1.5 flex items-start gap-1.5 px-2 text-[11px] leading-4 text-destructive">
          <span className="min-w-0 flex-1">{createError}</span>
          <button type="button" className="shrink-0 underline underline-offset-2" onClick={() => setCreateError(null)}>关闭</button>
        </div>
      ) : null}
    </div>
  );

  return (
    <div
      className="flex flex-col h-full"
      data-sidebar-dragging={drag?.kind || undefined}
      onDragEnd={clearDrag}
      onDragLeave={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setDropTarget(null);
      }}>
      {searchRow}
      {orderError && <p role="alert" className="px-3 pb-2 text-xs text-destructive">{orderError}</p>}
      <ScrollArea
        className="flex-1 min-h-0"
        viewportRef={viewportRef}
        onDragOver={handleViewportDragOver}
        onDragLeave={handleViewportDragLeave}
      >
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
                <div style={{ height: virtualizer.getTotalSize(), position: "relative" }}>
                  {virtualizer.getVirtualItems().map((row) => {
                    const { group, session } = rows[row.index];
                    const collapsed = collapsedGroups.has(group.key);
                    const readAt = readAtByWorkspace[group.key];
                    const unreadCount = readAt
                      ? group.sessions.filter((item) => item.updatedAt && item.updatedAt > readAt).length
                      : 0;
                    const frozen = Boolean(drag && (drag.kind === "workspace" ? drag.groupKey === group.key : drag.id === session?.id));
                    const indicator = dropTarget?.rowKey === rows[row.index].key ? dropTarget.side : null;
                    return (
                      <div key={row.key} data-index={row.index} ref={virtualizer.measureElement}
                        data-sidebar-row={rows[row.index].key}
                        data-drag-frozen={frozen || undefined}
                        data-drag-active={drag ? "true" : undefined}
                        data-drag-source={frozen ? "true" : undefined}
                        data-drop-target={indicator ? "true" : undefined}
                        onDragEnter={(event) => {
                          if (drag) event.preventDefault();
                        }}
                        onDragOver={(event) => {
                          if (!drag) return;
                          event.preventDefault();
                          const target = getDropTarget(event, row.index);
                          event.dataTransfer.dropEffect = target ? "move" : "none";
                          setDropTarget(target);
                        }}
                        onDrop={(event) => handleDrop(event, row.index)}
                        style={{ position: "absolute", top: 0, left: 0, width: "100%", transform: `translateY(${row.start}px)`, paddingBottom: 2 }}>
                        {indicator && <div aria-hidden="true" data-drop-indicator={indicator}
                          className="pointer-events-none absolute inset-x-2 z-10 h-0.5 rounded-full bg-[var(--em-primary)]"
                          style={indicator === "before" ? { top: -1 } : { bottom: 0 }} />}
                        <div>
                        {!session ? (
                        <div
                          className={cn(
                            "em-workspace-group group/ws flex select-none items-center gap-1 px-2 py-1 transition-colors",
                            group.workspaceId && "cursor-grab active:cursor-grabbing",
                          )}
                          data-menu-open={workspaceMenuKey === group.key || undefined}
                          draggable={Boolean(group.workspaceId) && !reorderingWorkspace}
                          onPointerDownCapture={(event) => {
                            const button = (event.target as Element).closest("button");
                            dragBlocked.current = Boolean(button && !button.hasAttribute("data-workspace-drag-handle"));
                          }}
                          onDragStart={(event) => {
                            if (group.workspaceId) handleDragStart(event, { kind: "workspace", id: group.workspaceId, groupKey: group.key });
                          }}
                        >
                          <button
                            type="button"
                            data-workspace-drag-handle="true"
                            className="flex min-w-0 flex-1 items-center gap-1.5 rounded-md py-0.5 text-left"
                            onClick={() => toggleGroup(group.key)}
                            aria-expanded={!collapsed}
                            title={`${collapsed ? "展开" : "折叠"}「${group.title}」的对话${group.workspaceId ? "；按住拖动调整工作区顺序" : ""}`}
                          >
                            <ChevronDown
                              className={cn(
                                "pointer-events-none h-3.5 w-3.5 shrink-0 text-current transition-transform duration-[250ms] ease-[cubic-bezier(0.4,0,0.2,1)]",
                                collapsed && "-rotate-90",
                              )}
                            />
                            <span className="em-workspace-icon" aria-hidden="true">
                              {group.key === "__ungrouped__"
                                ? <Inbox className="h-3.5 w-3.5" />
                                : collapsed
                                  ? <Folder className="h-3.5 w-3.5" />
                                  : <FolderOpen className="h-3.5 w-3.5" />}
                            </span>
                            <span className="min-w-0 truncate text-current" title={group.path || group.title}>
                              {group.title}
                            </span>
                            <span className="em-workspace-count" aria-label={`${group.sessions.length} 个对话`}>
                              {group.sessions.length}
                            </span>
                            {unreadCount > 0 ? (
                              <span className="em-workspace-unread" aria-label={`${unreadCount} 个未读对话`}>
                                {unreadCount > 99 ? "99+" : unreadCount}
                              </span>
                            ) : null}
                            {group.isDefault ? (
                              <span
                                className="inline-flex shrink-0 items-center rounded-full bg-[var(--em-primary-alpha-12)] px-1.5 py-0.5 text-[9px] font-semibold leading-none text-[var(--em-primary)]"
                                title="默认文件夹，不能删除"
                              >
                                默认文件夹
                              </span>
                            ) : null}
                          </button>
                          {(group.canManage || group.sessions.length > 0) ? (
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
                              <DropdownMenuContent side="bottom" align="end" sideOffset={6} className={glassMenuPanelClass}>
                                {group.canManage ? (
                                  <DropdownMenuItem
                                    className={glassMenuItemClass}
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
                                ) : null}
                                {group.path ? (
                                  <DropdownMenuItem
                                    className={glassMenuItemClass}
                                    onClick={() => {
                                      setWorkspaceMenuKey(null);
                                      void handleRevealWorkspace(group.path || "", group.workspaceId);
                                    }}
                                  >
                                    {deployMode === "standalone" ? <ExternalLink className="h-4 w-4" /> : <Clipboard className="h-4 w-4" />}
                                    {deployMode === "standalone"
                                      ? fileManagerName === "Finder" ? "在 Finder 中显示" : `在${fileManagerName}中显示`
                                      : "复制工作区路径"}
                                  </DropdownMenuItem>
                                ) : null}
                                <DropdownMenuSeparator className="mx-1 my-1.5 bg-[var(--em-hairline)]" />
                                <DropdownMenuItem
                                  className={glassMenuItemClass}
                                  onClick={() => {
                                    markWorkspaceRead(group.key);
                                    setWorkspaceMenuKey(null);
                                  }}
                                >
                                  <CheckCheck className="h-4 w-4" />
                                  全部标为已读
                                </DropdownMenuItem>
                                {group.sessions.length > 0 ? (
                                  <DropdownMenuItem
                                    variant="destructive"
                                    className={glassMenuDangerItemClass}
                                    onClick={() => {
                                      setWorkspaceMenuKey(null);
                                      void handleDeleteWorkspaceSessions(group.sessions.map((item) => item.id));
                                    }}
                                  >
                                    <Trash2 className="h-4 w-4" />
                                    删除所有对话
                                  </DropdownMenuItem>
                                ) : null}
                                {group.canManage ? <DropdownMenuSeparator className="mx-1 my-1.5 bg-[var(--em-hairline)]" /> : null}
                                <DropdownMenuItem
                                  variant="destructive"
                                  className={glassMenuDangerItemClass}
                                  disabled={group.isDefault || !group.workspaceId}
                                  title={group.isDefault ? "不能删除默认工作区" : undefined}
                                  onClick={() => {
                                    if (!group.workspaceId || group.isDefault) return;
                                    setWorkspaceMenuKey(null);
                                    void handleDeleteWorkspace(group.workspaceId);
                                  }}
                                >
                                  <Trash2 className="h-4 w-4" />
                                  {group.isDefault ? "删除（默认文件夹不可删除）" : "删除"}
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
                              aria-busy={creatingWorkspaceKey === group.key}
                              disabled={creating}
                              onClick={() => void handleNewSession(group.workspaceId, group.path, group.key)}
                            >
                              {creatingWorkspaceKey === group.key
                                ? <LoaderCircle className="h-3.5 w-3.5 animate-spin text-current" />
                                : <Plus className="h-3.5 w-3.5 text-current" />}
                            </Button>
                          ) : null}
                        </div>
                        ) : (() => {
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
                      <div
                        key={session.id}
                        className={cn(
                          "em-session-card group relative mx-2 flex min-w-0 cursor-pointer select-none items-center gap-1 rounded-xl py-2 pr-1 pl-[calc(0.875rem+0.375rem)] transition-colors",
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
                        data-active={isActive || undefined}
                        data-status={statusTone || undefined}
                        tabIndex={0}
                        role="button"
                        draggable={!isEditing}
                        onPointerDownCapture={(event) => {
                          dragBlocked.current = Boolean((event.target as Element).closest("button, input"));
                        }}
                        onDragStart={(event) => handleDragStart(event, { kind: "session", id: session.id, groupKey: group.key })}
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
                          <DropdownMenu onOpenChange={(open) => setSessionMenuId(open ? session.id : null)}>
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
                              className={glassMenuPanelClass}
                            >
                              <DropdownMenuItem
                                className={glassMenuItemClass}
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
                                className={glassMenuItemClass}
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
                                className={glassMenuItemClass}
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
                                className={glassMenuDangerItemClass}
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
                      </div>
                    );
                  })()}
                        </div>
                      </div>
                    );
                  })}
                </div>
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
});
