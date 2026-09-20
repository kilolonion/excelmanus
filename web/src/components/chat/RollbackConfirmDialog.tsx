"use client";

import { useState, useEffect, useCallback } from "react";
import {
  OverlayCard,
  OverlayCardAction,
  OverlayCardBody,
  OverlayCardFooter,
  OverlayCardHeader,
  OverlayCardInset,
} from "@/components/ui/overlay-card";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  CornerDownLeft,
  RotateCcw,
  FilePlus2,
  FileMinus2,
  FileEdit,
  ChevronDown,
  ChevronRight,
  Loader2,
  FileWarning,
} from "lucide-react";
import type { RollbackFileChange, RollbackPreviewResult } from "@/lib/api";
import { displayFileName } from "@/lib/file-identity";

function formatBytes(bytes: number | null): string {
  if (bytes === null || bytes === undefined) return "-";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function FileChangeIcon({ type }: { type: string }) {
  switch (type) {
    case "added":
      return <FilePlus2 className="h-3.5 w-3.5 text-green-500 shrink-0" />;
    case "deleted":
      return <FileMinus2 className="h-3.5 w-3.5 text-red-500 shrink-0" />;
    default:
      return <FileEdit className="h-3.5 w-3.5 text-yellow-500 shrink-0" />;
  }
}

function changeTypeLabel(type: string): string {
  switch (type) {
    case "added": return "新增";
    case "deleted": return "删除";
    default: return "修改";
  }
}

function DiffPreview({ diff }: { diff: string }) {
  const lines = diff.split("\n").slice(0, 60);
  return (
    <div className="mt-1.5 rounded-md border bg-muted/30 overflow-hidden">
      <pre className="text-[10px] sm:text-[11px] leading-[1.5] p-2 overflow-x-auto font-mono">
        {lines.map((line, i) => {
          let cls = "text-muted-foreground";
          if (line.startsWith("+") && !line.startsWith("+++")) cls = "text-green-600 dark:text-green-400";
          else if (line.startsWith("-") && !line.startsWith("---")) cls = "text-red-600 dark:text-red-400";
          else if (line.startsWith("@@")) cls = "text-blue-500 dark:text-blue-400";
          return (
            <div key={i} className={cls}>
              {line || "\u00A0"}
            </div>
          );
        })}
        {diff.split("\n").length > 60 && (
          <div className="text-muted-foreground/60 italic">... 更多内容已省略</div>
        )}
      </pre>
    </div>
  );
}

function FileChangeItem({ change }: { change: RollbackFileChange }) {
  const [expanded, setExpanded] = useState(false);
  const hasDiff = !!change.diff;
  const filename = displayFileName(change.path) || change.path;
  const dir = change.path.includes("/")
    ? change.path.slice(0, change.path.lastIndexOf("/") + 1)
    : "";

  return (
    <div className="border-b last:border-b-0 border-border/40">
      <button
        className="flex items-center gap-1.5 sm:gap-2 w-full px-2.5 sm:px-3 py-2 sm:py-1.5 text-left hover:bg-muted/40 active:bg-muted/60 transition-colors touch-manipulation"
        onClick={() => hasDiff && setExpanded(!expanded)}
        disabled={!hasDiff}
      >
        <FileChangeIcon type={change.change_type} />
        <div className="flex-1 min-w-0 truncate">
          <span className="text-xs">
            {dir && <span className="text-muted-foreground/60 hidden sm:inline">{dir}</span>}
            <span className="font-medium">{filename}</span>
          </span>
        </div>
        <span className={[
          "text-[10px] px-1.5 py-0.5 rounded-full font-medium shrink-0",
          change.change_type === "added" ? "bg-green-500/10 text-green-600 dark:text-green-400" :
          change.change_type === "deleted" ? "bg-red-500/10 text-red-600 dark:text-red-400" :
          "bg-yellow-500/10 text-yellow-600 dark:text-yellow-400",
        ].join(" ")}>
          {changeTypeLabel(change.change_type)}
        </span>
        {change.change_type === "modified" && change.before_size != null && change.after_size != null && (
          <span className="text-[10px] text-muted-foreground/60 shrink-0 hidden sm:inline">
            {formatBytes(change.before_size)} → {formatBytes(change.after_size)}
          </span>
        )}
        {hasDiff && (
          expanded
            ? <ChevronDown className="h-3 w-3 text-muted-foreground/50 shrink-0" />
            : <ChevronRight className="h-3 w-3 text-muted-foreground/50 shrink-0" />
        )}
      </button>
      {expanded && change.diff && <DiffPreview diff={change.diff} />}
    </div>
  );
}

interface RollbackConfirmDialogProps {
  open: boolean;
  sessionId: string | null;
  turnIndex: number;
  onConfirm: () => void;
  onCancel: () => void;
}

export function RollbackConfirmDialog({
  open,
  sessionId,
  turnIndex,
  onConfirm,
  onCancel,
}: RollbackConfirmDialogProps) {
  const [preview, setPreview] = useState<RollbackPreviewResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) {
      setPreview(null);
      setError(null);
      return;
    }
    if (!sessionId) return;

    let cancelled = false;
    setLoading(true);
    setError(null);

    import("@/lib/api").then(({ rollbackPreview }) =>
      rollbackPreview(sessionId, turnIndex)
    ).then((result) => {
      if (!cancelled) {
        setPreview(result);
        setLoading(false);
      }
    }).catch((err) => {
      if (!cancelled) {
        setError(err?.message || "预览加载失败");
        setLoading(false);
      }
    });

    return () => { cancelled = true; };
  }, [open, sessionId, turnIndex]);

  const handleConfirm = useCallback(() => {
    onConfirm();
  }, [onConfirm]);

  // 快捷键：Enter = 确认对话回退，Esc = 取消
  useEffect(() => {
    if (!open) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        e.stopPropagation();
        handleConfirm();
      }
    };
    window.addEventListener("keydown", handler, true);
    return () => window.removeEventListener("keydown", handler, true);
  }, [open, handleConfirm]);

  const fileChanges = preview?.file_changes ?? [];
  const addedCount = fileChanges.filter((f) => f.change_type === "added").length;
  const modifiedCount = fileChanges.filter((f) => f.change_type === "modified").length;
  const deletedCount = fileChanges.filter((f) => f.change_type === "deleted").length;
  const hasChanges = fileChanges.length > 0;

  return (
    <OverlayCard
      open={open}
      onOpenChange={(v) => !v && onCancel()}
      size="lg"
      tone="warning"
      onOpenAutoFocus={(e) => e.preventDefault()}
    >
      <OverlayCardHeader
        icon={<RotateCcw className="h-5 w-5" />}
        title="从历史消息重新提交？"
        description={
          <>
            重新提交将回退到该消息，并清除之后的所有对话。磁盘文件不会被改写。
            {preview && preview.removed_messages > 0 && (
              <span className="text-foreground/70"> 将移除 {preview.removed_messages} 条消息。</span>
            )}
          </>
        }
        onClose={onCancel}
      />

      <OverlayCardBody>
        {loading && (
          <div className="flex items-center justify-center gap-2 py-6 text-sm text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" />
            正在加载变更预览...
          </div>
        )}

        {error && (
          <div className="flex items-center gap-2 py-3 px-3 text-sm text-muted-foreground bg-muted/30 rounded-xl">
            <FileWarning className="h-4 w-4 text-yellow-500" />
            {error}
          </div>
        )}

        {!loading && !error && preview && (
          hasChanges ? (
            <div>
              <div className="flex flex-wrap items-center gap-x-3 gap-y-1 mb-2 text-xs text-muted-foreground">
                <span className="font-medium text-foreground/80">
                  文件变更 ({fileChanges.length})
                </span>
                {addedCount > 0 && (
                  <span className="text-green-600 dark:text-green-400">+{addedCount} 新增</span>
                )}
                {modifiedCount > 0 && (
                  <span className="text-yellow-600 dark:text-yellow-400">~{modifiedCount} 修改</span>
                )}
                {deletedCount > 0 && (
                  <span className="text-red-600 dark:text-red-400">-{deletedCount} 删除</span>
                )}
              </div>

              <OverlayCardInset padded={false}>
                <ScrollArea className="max-h-[40dvh] sm:max-h-[280px]">
                  <div>
                    {fileChanges.map((change, i) => (
                      <FileChangeItem key={`${change.path}-${i}`} change={change} />
                    ))}
                  </div>
                </ScrollArea>
              </OverlayCardInset>

              <p className="mt-2 text-[11px] text-muted-foreground/60 hidden sm:block">
                对话回退不改磁盘。文件回退请用版本面板。
              </p>
            </div>
          ) : (
            <div className="py-3 px-3 text-sm text-muted-foreground bg-muted/30 rounded-xl">
              没有检测到该轮之后的文件变更。
            </div>
          )
        )}
      </OverlayCardBody>

      <OverlayCardFooter>
        <OverlayCardAction action="ghost" onClick={onCancel}>
          取消
          <kbd className="ml-0.5 text-[10px] text-muted-foreground/60 font-normal hidden sm:inline">
            esc
          </kbd>
        </OverlayCardAction>
        <OverlayCardAction action="primary" onClick={handleConfirm}>
          回退并重发
          <CornerDownLeft className="h-3 w-3 opacity-60 hidden sm:inline" />
        </OverlayCardAction>
      </OverlayCardFooter>
    </OverlayCard>
  );
}
