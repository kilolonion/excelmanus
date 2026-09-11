"use client";

import { RefreshCw } from "lucide-react";

export function ExcelWriteConflictBar({
  onReload,
  error,
}: {
  onReload: () => void;
  error?: string | null;
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
        文件已被其他来源修改，当前编辑未写入。可继续保留草稿，或重新加载磁盘版本。
      </span>
      <button
        type="button"
        onClick={onReload}
        className="flex items-center gap-1 px-2 py-1 rounded text-xs font-medium text-white shrink-0"
        style={{ backgroundColor: "var(--em-primary)" }}
      >
        <RefreshCw className="h-3 w-3" />
        重新加载
      </button>
    </div>
  );
}
