"use client";

import { useState, useEffect, useCallback } from "react";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogFooter,
  DialogTitle,
  DialogDescription,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  CornerDownLeft,
  Check,
  FilePlus2,
  FileMinus2,
  FileEdit,
  ChevronDown,
  ChevronRight,
  Loader2,
  FileWarning,
} from "lucide-react";
import type { RollbackFileChange, RollbackPreviewResult } from "@/lib/api";

const STORAGE_KEY = "em_rollback_file_preference";

export type RollbackFilePreference = "always_rollback" | "never_rollback" | null;

function loadPreference(): RollbackFilePreference {
  if (typeof window === "undefined") return null;
  const val = localStorage.getItem(STORAGE_KEY);
  if (val === "always_rollback" || val === "never_rollback") return val;
  return null;
}

function savePreference(pref: RollbackFilePreference) {
  if (typeof window === "undefined") return;
  if (pref === null) {
    localStorage.removeItem(STORAGE_KEY);
  } else {
    localStorage.setItem(STORAGE_KEY, pref);
  }
}

export function getRollbackFilePreference(): RollbackFilePreference {
  return loadPreference();
}

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
  const filename = change.path.split("/").pop() || change.path;
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
  onConfirm: (rollbackFiles: boolean) => void;
  onCancel: () => void;
}

export function RollbackConfirmDialog({
  open,
  sessionId,
  turnIndex,
  onConfirm,
  onCancel,
}: RollbackConfirmDialogProps) {
  const [dontAskAgain, setDontAskAgain] = useState(false);
  const [preview, setPreview] = useState<RollbackPreviewResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) {
      setPreview(null);
      setError(null);
      setDontAskAgain(false);
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

  const handleConfirm = useCallback(
    (rollbackFiles: boolean) => {
      if (dontAskAgain) {
        savePreference(rollbackFiles ? "always_rollback" : "never_rollback");
      }
      onConfirm(rollbackFiles);
    },
    [dontAskAgain, onConfirm]
  );

  // 快捷键：Enter = 回滚，Shift+Enter = 不回滚，Esc = 取消
  useEffect(() => {
    if (!open) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        e.stopPropagation();
        handleConfirm(true);
      } else if (e.key === "Enter" && e.shiftKey) {
        e.preventDefault();
        e.stopPropagation();
        handleConfirm(false);
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
    <Dialog open={open} onOpenChange={(v) => !v && onCancel()}>
      <DialogContent
        className="w-[calc(100%-1.5rem)] sm:max-w-[560px] rounded-xl max-h-[90dvh] overflow-y-auto"
        showCloseButton={false}
        onOpenAutoFocus={(e) => e.preventDefault()}
      >
        <DialogHeader>
          <DialogTitle className="text-base font-semibold">
            从历史消息重新提交？
          </DialogTitle>
          <DialogDescription className="text-sm text-muted-foreground">
            重新提交将回退到该消息，并清除之后的所有对话。
            {preview && preview.removed_messages > 0 && (
              <span className="text-foreground/70"> 将移除 {preview.removed_messages} 条消息。</span>
            )}
          </DialogDescription>
        </DialogHeader>

        {/* 文件变更预览区 */}
        <div className="my-1">
          {loading && (
            <div className="flex items-center justify-center gap-2 py-6 text-sm text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" />
              正在加载变更预览...
            </div>
          )}

          {error && (
            <div className="flex items-center gap-2 py-3 px-3 text-sm text-muted-foreground bg-muted/30 rounded-lg">
              <FileWarning className="h-4 w-4 text-yellow-500" />
              {error}
            </div>
          )}

          {!loading && !error && preview && (
            hasChanges ? (
              <div>
                {/* 变更统计 */}
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

                {/* 文件列表 */}
                <ScrollArea className="max-h-[40dvh] sm:max-h-[280px] rounded-lg border bg-background">
                  <div>
                    {fileChanges.map((change, i) => (
                      <FileChangeItem key={`${change.path}-${i}`} change={change} />
                    ))}
                  </div>
                </ScrollArea>

                <p className="mt-2 text-[11px] text-muted-foreground/60 hidden sm:block">
                  选择「回退并重发」将撤销以上文件变更。点击可展开查看 diff。
                </p>
              </div>
            ) : (
              <div className="py-3 px-3 text-sm text-muted-foreground bg-muted/30 rounded-lg">
                没有检测到可回退的文件变更。
              </div>
            )
          )}
        </div>

        <label
          className="flex items-center gap-1.5 text-xs text-muted-foreground cursor-pointer select-none"
          onClick={() => setDontAskAgain(!dontAskAgain)}
        >
          <span
            role="checkbox"
            aria-checked={dontAskAgain}
            className={[
              "inline-flex items-center justify-center shrink-0 rounded-[3px] border transition-colors",
              "h-[14px] w-[14px]",
              dontAskAgain
                ? "bg-primary border-primary text-primary-foreground"
                : "border-muted-foreground/40 bg-transparent",
            ].join(" ")}
          >
            {dontAskAgain && <Check className="h-[10px] w-[10px]" strokeWidth={2.5} />}
          </span>
          不再询问
        </label>

        <DialogFooter className="gap-2 sm:gap-2">
          <Button
            variant="ghost"
            size="sm"
            onClick={onCancel}
            className="text-muted-foreground"
          >
            取消
            <kbd className="ml-1.5 text-[10px] text-muted-foreground/60 font-normal hidden sm:inline">
              esc
            </kbd>
          </Button>
          <Button
            variant="outline"
            size="sm"
            onClick={() => handleConfirm(false)}
          >
            不回退改动
            <span className="ml-1.5 items-center gap-0.5 text-[10px] text-muted-foreground/60 hidden sm:inline-flex">
              <span>⇧</span>
              <CornerDownLeft className="h-2.5 w-2.5" />
            </span>
          </Button>
          <Button size="sm" onClick={() => handleConfirm(true)}>
            回退并重发
            <CornerDownLeft className="ml-1.5 h-3 w-3 opacity-60 hidden sm:inline" />
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
