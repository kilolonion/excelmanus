"use client";

import { useEffect } from "react";
import { useExcelStore } from "@/stores/excel-store";
import { useChatStore } from "@/stores/chat-store";

/**
 * ExcelDataRecovery 组件
 * 
 * 解决页面刷新后 Excel diff 数据丢失的问题。
 * 在页面加载时主动从后端恢复 Excel 相关数据。
 */
export function ExcelDataRecovery() {
  const currentSessionId = useChatStore((s) => s.currentSessionId);
  const diffs = useExcelStore((s) => s.diffs);

  useEffect(() => {
    // 只在有会话但没有 diff 数据时触发恢复
    if (!currentSessionId || diffs.length > 0) return;

    const recoverExcelData = async () => {
      try {
        // 动态导入以避免循环依赖
        const { fetchSessionExcelEvents } = await import("@/lib/api");
        const { diffs: recoveredDiffs, previews: recoveredPreviews, affected_files } = 
          await fetchSessionExcelEvents(currentSessionId);

        if (recoveredDiffs.length === 0 && recoveredPreviews.length === 0 && affected_files.length === 0) {
          return;
        }

        const excelStore = useExcelStore.getState();

        // 恢复文件列表
        for (const fp of affected_files) {
          if (!fp) continue;
          const filename = fp.split("/").pop() || fp;
          excelStore.addRecentFileIfNotDismissed({ path: fp, filename });
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
            return { diffs: merged.slice(-500) }; // 保持最近 500 个 diff
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
  }, [currentSessionId, diffs.length]);

  return null; // 这是一个纯逻辑组件，不渲染任何内容
}