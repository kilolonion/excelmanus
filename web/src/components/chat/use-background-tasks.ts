"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { controlSubagentRun, fetchSessionTaskList, fetchSubagentRuns, type SubagentControlAction } from "@/lib/api";
import { completedSubagentFiles, isSubagentActive } from "@/lib/subagent-runs";
import { workspaceKeyForSessionId } from "@/lib/workspace-file-ref";
import type { SessionTaskList, SubagentRun } from "@/lib/types";
import { useChatStore } from "@/stores/chat-store";
import { useExcelStore } from "@/stores/excel-store";
import { useSessionStore } from "@/stores/session-store";
import { useWordStore } from "@/stores/word-store";
import { isBlankSession } from "@/lib/session-loading";

/** 由按 sessionId 挂载的任务面板持有；聊天流结束后继续查询活动任务。 */
export function useBackgroundTasks(sessionId: string, open: boolean) {
  const [runs, setRuns] = useState<SubagentRun[]>([]);
  const [taskList, setTaskList] = useState<SessionTaskList | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);
  const runsRef = useRef<SubagentRun[]>([]);
  const requestVersion = useRef(0);
  const mounted = useRef(false);
  const streaming = useChatStore((s) => s.loadedSessionId === sessionId && s.isStreaming);
  const messagesReady = useChatStore((s) => s.loadedSessionId === sessionId && !s.isLoadingMessages);
  const blank = useSessionStore((s) => isBlankSession(s.sessions.find((item) => item.id === sessionId)));
  const knownActive = useChatStore((s) => s.loadedSessionId === sessionId && s.messages.some((message) =>
    message.role === "assistant" && message.blocks.some((block) =>
      block.type === "subagent" && block.background && block.status === "running")));
  const hasRunningTasks = runs.some((run) => run.background && isSubagentActive(run.status));

  const accept = useCallback((rows: SubagentRun[]) => {
    const changedFiles = completedSubagentFiles(runsRef.current, rows);
    runsRef.current = rows;
    setRuns(rows);
    useChatStore.getState().syncSubagentRuns(sessionId, rows);
    if (changedFiles.length && useSessionStore.getState().activeSessionId === sessionId) {
      const excel = useExcelStore.getState();
      const workspaceKey = workspaceKeyForSessionId(sessionId);
      for (const path of changedFiles) {
        excel.addRecentFileIfNotDismissed({ path, filename: path.split("/").pop() || path }, workspaceKey);
        excel.notifyWorkbookChanged(path, workspaceKey);
      }
      useWordStore.getState().handleFilesChanged(changedFiles);
      excel.bumpWorkspaceFilesVersion();
    }
  }, [sessionId]);

  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; requestVersion.current += 1; };
  }, []);

  useEffect(() => {
    // Empty chats have neither persisted tasks nor an engine to restore.
    // Initial history loading owns the connection budget until it can paint.
    if (!messagesReady || (blank && !streaming) || !(open || knownActive || hasRunningTasks)) return;
    let stopped = false;
    const controller = new AbortController();
    let polling = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const poll = async () => {
      if (stopped || polling) return;
      clearTimeout(timer);
      if (document.hidden) return;
      polling = true;
      const version = ++requestVersion.current;
      if (open && !runsRef.current.length) setLoading(true);
      try {
        const [rowsResult, taskListResult] = await Promise.allSettled([
          fetchSubagentRuns(sessionId, { signal: controller.signal }),
          fetchSessionTaskList(sessionId, { signal: controller.signal }),
        ]);
        if (stopped || version !== requestVersion.current) return;
        if (rowsResult.status === "rejected") throw rowsResult.reason;
        accept(rowsResult.value);
        // 任务清单失败不阻塞后台任务刷新；保留上次获取的状态。
        if (taskListResult.status === "fulfilled") setTaskList(taskListResult.value);
        setError("");
      } catch (cause) {
        if (stopped || version !== requestVersion.current) return;
        setError(cause instanceof Error ? cause.message : "任务状态暂时无法更新");
      } finally {
        polling = false;
        if (!stopped && version === requestVersion.current) setLoading(false);
      }
      if (stopped) return;
      const active = runsRef.current.some((run) => run.background && isSubagentActive(run.status));
      if (open || streaming || active) {
        timer = setTimeout(() => void poll(), active || streaming ? 2000 : 5000);
      }
    };
    const onFocus = () => void poll();
    const onVisible = () => { if (!document.hidden) void poll(); };
    window.addEventListener("focus", onFocus);
    document.addEventListener("visibilitychange", onVisible);
    void poll();
    return () => {
      stopped = true;
      controller.abort();
      requestVersion.current += 1;
      clearTimeout(timer);
      window.removeEventListener("focus", onFocus);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [sessionId, open, streaming, messagesReady, blank, knownActive, hasRunningTasks, revision, accept]);

  const refresh = useCallback(() => setRevision((value) => value + 1), []);
  const control = useCallback(async (runId: string, action: SubagentControlAction, message = "") => {
    const run = await controlSubagentRun(sessionId, runId, action, message);
    if (mounted.current) {
      // 丢弃动作完成前发出的旧查询；resume 返回新 ID，保留旧执行的结果。
      requestVersion.current += 1;
      const previous = runsRef.current;
      accept(previous.some((row) => row.run_id === run.run_id)
        ? previous.map((row) => row.run_id === run.run_id ? run : row)
        : [...previous, run]);
      setError("");
      refresh();
    }
    return run;
  }, [sessionId, accept, refresh]);

  return { runs: runs.filter((run) => run.background), taskList, loading, error, refresh, control };
}
