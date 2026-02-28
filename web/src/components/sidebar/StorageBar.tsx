"use client";

import { useEffect, useState, useCallback } from "react";
import { HardDrive } from "lucide-react";
import { fetchWorkspaceStorage, type WorkspaceStorage } from "@/lib/api";
import { useExcelStore } from "@/stores/excel-store";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";

function formatSize(mb: number): string {
  if (mb < 1) return `${Math.round(mb * 1024)} KB`;
  if (mb >= 1024) return `${(mb / 1024).toFixed(1)} GB`;
  return `${mb.toFixed(1)} MB`;
}

/** 根据使用率返回颜色：<50% 绿，50-80% 黄，>80% 红 */
function getBarColor(ratio: number): string {
  if (ratio < 0.5) return "var(--em-success, #22c55e)";
  if (ratio < 0.8) return "var(--em-warning, #eab308)";
  return "var(--em-danger, #ef4444)";
}

function getBarBg(ratio: number): string {
  if (ratio < 0.5) return "rgba(34,197,94,0.12)";
  if (ratio < 0.8) return "rgba(234,179,8,0.12)";
  return "rgba(239,68,68,0.12)";
}

export function StorageBar() {
  const [storage, setStorage] = useState<WorkspaceStorage | null>(null);
  const workspaceFilesVersion = useExcelStore((s) => s.workspaceFilesVersion);

  const refresh = useCallback(() => {
    fetchWorkspaceStorage().then((s) => {
      if (s) setStorage(s);
    }).catch(() => {});
  }, []);

  // 初始加载
  useEffect(() => { refresh(); }, [refresh]);

  // 文件变化时刷新
  useEffect(() => {
    if (workspaceFilesVersion === 0) return;
    const t = setTimeout(refresh, 800);
    return () => clearTimeout(t);
  }, [workspaceFilesVersion, refresh]);

  if (!storage) return null;

  const sizeRatio = storage.max_bytes > 0
    ? Math.min(storage.total_bytes / storage.max_bytes, 1)
    : 0;
  const fileRatio = storage.max_files > 0
    ? Math.min(storage.file_count / storage.max_files, 1)
    : 0;
  const displayRatio = Math.max(sizeRatio, fileRatio);
  const color = getBarColor(displayRatio);
  const bg = getBarBg(displayRatio);
  const pct = Math.round(displayRatio * 100);

  const isOver = storage.over_size || storage.over_files;

  return (
    <TooltipProvider delayDuration={200}>
      <Tooltip>
        <TooltipTrigger asChild>
          <div className="px-1 mb-1.5 cursor-default select-none">
            <div className="flex items-center gap-1.5 mb-1">
              <HardDrive className="h-3 w-3 flex-shrink-0" style={{ color }} />
              <span className="text-[10px] text-muted-foreground leading-none">
                {formatSize(storage.size_mb)} / {formatSize(storage.max_size_mb)}
              </span>
              <span className="ml-auto text-[10px] font-medium leading-none" style={{ color }}>
                {pct}%
              </span>
            </div>
            <div
              className="h-1.5 rounded-full overflow-hidden transition-colors duration-300"
              style={{ backgroundColor: bg }}
            >
              <div
                className="h-full rounded-full transition-all duration-500 ease-out"
                style={{
                  width: `${Math.max(pct, 1)}%`,
                  backgroundColor: color,
                }}
              />
            </div>
            {isOver && (
              <div
                className="mt-1 px-1.5 py-1 rounded text-[10px] font-medium leading-tight"
                style={{ backgroundColor: "rgba(239,68,68,0.12)", color: "var(--em-danger, #ef4444)" }}
              >
                工作区已满，请清理文件后继续对话
              </div>
            )}
          </div>
        </TooltipTrigger>
        <TooltipContent side="bottom" className="text-xs">
          <div className="space-y-0.5">
            <div>存储：{formatSize(storage.size_mb)} / {formatSize(storage.max_size_mb)}</div>
            <div>文件：{storage.file_count} / {storage.max_files} 个</div>
            {isOver && (
              <div className="text-destructive font-medium">⚠ 已超出配额，对话已暂停</div>
            )}
          </div>
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}
