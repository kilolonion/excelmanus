"use client";

import { RefreshCw } from "lucide-react";
import { useWorkbookWorkflowStore } from "@/stores/workbook-workflow-store";
import { activeSession, fileRefFromSession } from "@/lib/workspace-file-ref";

export function ExcelWriteConflictBar({
  onReload,
  error,
  filePath,
}: {
  onReload: () => void;
  error?: string | null;
  filePath?: string;
}) {
  if (error) {
    return (
      <div className="border-t border-border bg-destructive/10 px-3 py-2 flex items-center gap-2 flex-shrink-0">
        <span className="text-xs text-destructive flex-1 truncate">
          {error || "保存失败，已保留当前编辑"}
        </span>
      </div>
    );
  }

  return (
    <div className="border-t border-amber-500/30 bg-amber-500/10 px-3 py-2 flex items-center gap-2 flex-shrink-0">
      <span className="text-xs text-amber-800 dark:text-amber-200 flex-1">
        文件已被其他来源修改。可以核对并合并双方修改，或保留草稿稍后处理。
      </span>
      {filePath && <button type="button" className="text-xs underline shrink-0" onClick={() => {
        const session = activeSession();
        if (session) useWorkbookWorkflowStore.getState().openConflict({ file: fileRefFromSession(filePath, session), sessionId: session.id });
      }}>核对并合并</button>}
      <button
        type="button"
        onClick={() => { if (window.confirm("重新加载会放弃未保存的修改，是否继续？")) onReload(); }}
        className="flex items-center gap-1 px-2 py-1 rounded text-xs font-medium text-white shrink-0"
        style={{ backgroundColor: "var(--em-primary)" }}
      >
        <RefreshCw className="h-3 w-3" />
        重新加载
      </button>
    </div>
  );
}
