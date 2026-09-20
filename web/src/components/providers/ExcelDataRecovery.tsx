"use client";

import { useEffect, useRef } from "react";
import { useExcelStore } from "@/stores/excel-store";
import { useChatStore } from "@/stores/chat-store";
import { useSessionStore } from "@/stores/session-store";
import { useWordStore } from "@/stores/word-store";
import { useFilePreviewStore } from "@/stores/file-preview-store";
import { useWorkbookConversationStore } from "@/stores/workbook-conversation-store";
import { fetchWorkbookView } from "@/lib/api";
import { fileBaseName } from "@/lib/revision-display";
import {
  workspaceKeyForSessionId,
  workspaceKeyFromSession,
} from "@/lib/workspace-file-ref";

/**
 * ExcelDataRecovery 组件
 *
 * 解决页面刷新后 Excel diff 数据丢失的问题。
 * 在页面加载时主动从后端恢复 Excel 相关数据。
 */
export function ExcelDataRecovery() {
  const loadedSessionId = useChatStore((s) => s.loadedSessionId);
  const prevSessionRef = useRef<string | null>(null);

  useEffect(() => {
    if (!loadedSessionId || useSessionStore.getState().activeSessionId !== loadedSessionId) return;

    // Bind the first loaded session too: on a fresh Desktop profile no workbook
    // panel has opened yet, so activeWorkspaceKey would otherwise remain null.
    if (prevSessionRef.current !== loadedSessionId) {
      const sessions = useSessionStore.getState().sessions;
      const next = sessions.find((item) => item.id === loadedSessionId);
      const nextWorkspaceKey = workspaceKeyFromSession(next);
      const excel = useExcelStore.getState();
      const target = useWorkbookConversationStore.getState().targets[loadedSessionId];
      excel.rebindSession(
        excel.activeWorkspaceKey,
        nextWorkspaceKey,
      );
      if (target?.file.workspaceKey === nextWorkspaceKey) {
        if (target.showSheet) {
          excel.openFullView(target.file.relative, target.sheet, target.layout);
        } else {
          excel.closeFullView();
          useWorkbookConversationStore.getState().observe(loadedSessionId, target.file, { status: "loading" });
          void fetchWorkbookView({ path: target.file.relative, workspaceKey: nextWorkspaceKey,
            workspaceId: target.file.workspaceId, sessionId: loadedSessionId, sheet: target.sheet, withStyles: false,
          }).then((view) => {
            if (useSessionStore.getState().activeSessionId !== loadedSessionId) return;
            useWorkbookConversationStore.getState().observe(loadedSessionId, target.file, {
              status: "ready", sheet: view.active_sheet || view.windows[0]?.sheet, version: view.content_version,
            });
          }).catch((err) => {
            if (useSessionStore.getState().activeSessionId !== loadedSessionId) return;
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

    const recoverExcelData = async () => {
      try {
        // 动态导入以避免循环依赖
        const { fetchSessionExcelEvents } = await import("@/lib/api");
        const { diffs: recoveredDiffs, previews: recoveredPreviews, affected_files } =
          await fetchSessionExcelEvents(loadedSessionId);

        if (recoveredDiffs.length === 0 && recoveredPreviews.length === 0 && affected_files.length === 0) {
          return;
        }

        // 恢复期间会话可能已切换
        if (useChatStore.getState().loadedSessionId !== loadedSessionId) return;

        const excelStore = useExcelStore.getState();

        // 恢复文件列表（按来源会话的工作区键入桶）
        const sourceWorkspaceKey = workspaceKeyForSessionId(loadedSessionId);
        for (const fp of affected_files) {
          if (!fp) continue;
          const filename = fileBaseName(fp) || fp;
          excelStore.addRecentFileIfNotDismissed({ path: fp, filename }, sourceWorkspaceKey);
        }

        // 恢复 diff 数据
        if (recoveredDiffs.length > 0) {
          const convertedDiffs = recoveredDiffs.map((d) => ({
            toolCallId: d.tool_call_id,
            filePath: d.file_path,
            sheet: d.sheet,
            affectedRange: d.affected_range,
            changes: d.changes.map((c) => ({
              cell: c.cell,
              old: c.old,
              new: c.new,
            })),
            timestamp: d.timestamp ? new Date(d.timestamp).getTime() : Date.now(),
          }));

          // 批量添加 diff 数据
          useExcelStore.setState((state) => {
            const existing = state.diffs ?? [];
            const seen = new Set(
              existing.map((d) => `${d.toolCallId}::${d.filePath}::${d.sheet}::${d.affectedRange}`)
            );
            const merged = [...existing];

            for (const diff of convertedDiffs) {
              const key = `${diff.toolCallId}::${diff.filePath}::${diff.sheet}::${diff.affectedRange}`;
              if (seen.has(key)) continue;
              seen.add(key);
              merged.push(diff);
            }

            if (merged.length === existing.length) return {};
            return { diffs: merged.slice(-500) };
          });
        }

        // 恢复预览数据
        if (recoveredPreviews.length > 0) {
          for (const p of recoveredPreviews) {
            excelStore.addPreview({
              toolCallId: p.tool_call_id,
              filePath: p.file_path,
              sheet: p.sheet,
              columns: p.columns,
              rows: p.rows,
              totalRows: p.total_rows,
              truncated: p.truncated,
            });
          }
        }

        console.log(`Excel data recovery completed: ${recoveredDiffs.length} diffs, ${recoveredPreviews.length} previews`);
      } catch (error) {
        console.warn("Excel data recovery failed:", error);
      }
    };

    // 延迟执行，确保其他组件已经初始化
    const timeoutId = setTimeout(recoverExcelData, 100);
    return () => clearTimeout(timeoutId);
  }, [loadedSessionId]);

  return null; // 这是一个纯逻辑组件，不渲染任何内容
}
