"use client";

import { useEffect, useRef } from "react";
import { useExcelStore } from "@/stores/excel-store";
import { useChatStore } from "@/stores/chat-store";
import { useSessionStore } from "@/stores/session-store";
import { useWordStore } from "@/stores/word-store";
import { useFilePreviewStore } from "@/stores/file-preview-store";
import { useWorkbookConversationStore } from "@/stores/workbook-conversation-store";
import { useWorkbookWorkspaceStore, workbookWorkspaceKey } from "@/stores/workbook-workspace-store";
import { fetchWorkbookView } from "@/lib/api";
import {
  workspaceKeyFromSession,
} from "@/lib/workspace-file-ref";

/** Bind workbook UI to the selected workspace. History events are loaded once,
 * by chat-store, alongside the message page that needs them. */
export function ExcelDataRecovery() {
  const loadedSessionId = useChatStore((s) => s.loadedSessionId);
  const workspaceKey = useSessionStore((state) => workspaceKeyFromSession(
    state.sessions.find((session) => session.id === loadedSessionId)));
  const prevSessionRef = useRef<string | null>(null);

  useEffect(() => {
    if (!loadedSessionId || useSessionStore.getState().activeSessionId !== loadedSessionId) return;

    let cancelled = false;
    // Bind the first loaded session too: on a fresh Desktop profile no workbook
    // panel has opened yet, so activeWorkspaceKey would otherwise remain null.
    if (prevSessionRef.current !== loadedSessionId || useExcelStore.getState().activeWorkspaceKey !== workspaceKey) {
      const sessions = useSessionStore.getState().sessions;
      const next = sessions.find((item) => item.id === loadedSessionId);
      const nextWorkspaceKey = workspaceKeyFromSession(next);
      const excel = useExcelStore.getState();
      const target = useWorkbookConversationStore.getState().targets[loadedSessionId];
      const workspace = useWorkbookWorkspaceStore.getState().workspaces[workbookWorkspaceKey(loadedSessionId, nextWorkspaceKey)];
      const focused = workspace?.files.find((file) => file.path === workspace.focused);
      excel.closeCompare();
      excel.closePanel();
      excel.rebindSession(
        excel.activeWorkspaceKey,
        nextWorkspaceKey,
      );
      if (target?.file.workspaceKey === nextWorkspaceKey) {
        if (target.showSheet) {
          excel.openFullView(target.file.relative, target.sheet, target.layout);
          if (focused) excel.focusWorkbook(focused.path);
        } else {
          excel.closeFullView();
          useWorkbookConversationStore.getState().observe(loadedSessionId, target.file, { status: "loading" });
          void fetchWorkbookView({ path: target.file.relative, workspaceKey: nextWorkspaceKey,
            workspaceId: target.file.workspaceId, sessionId: loadedSessionId, sheet: target.sheet, withStyles: false,
          }).then((view) => {
            if (cancelled || useSessionStore.getState().activeSessionId !== loadedSessionId) return;
            useWorkbookConversationStore.getState().observe(loadedSessionId, target.file, {
              status: "ready", sheet: view.active_sheet || view.regions[0]?.sheet, version: view.content_version,
            });
          }).catch((err) => {
            if (cancelled || useSessionStore.getState().activeSessionId !== loadedSessionId) return;
            useWorkbookConversationStore.getState().observe(loadedSessionId, target.file, {
              status: "error", error: err instanceof Error ? err.message : "表格读取失败",
            });
          });
        }
      } else if (prevSessionRef.current) {
        excel.closeFullView();
      }
      useWordStore.getState().rebindWorkspace(nextWorkspaceKey);
      useFilePreviewStore.getState().clearForSessionChange();
    }
    prevSessionRef.current = loadedSessionId;

    return () => { cancelled = true; };
  }, [loadedSessionId, workspaceKey]);

  return null; // 这是一个纯逻辑组件，不渲染任何内容
}
