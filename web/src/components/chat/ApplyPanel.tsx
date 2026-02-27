"use client";

import { useEffect, useState, useCallback } from "react";
import {
  Upload,
  X,
  Check,
  Trash2,
  Loader2,
  AlertTriangle,
  FileSpreadsheet,
} from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { useExcelStore } from "@/stores/excel-store";
import { useSessionStore } from "@/stores/session-store";

export function ApplyPanel({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const activeSessionId = useSessionStore((s) => s.activeSessionId);
  const pendingBackups = useExcelStore((s) => s.pendingBackups);
  const backupEnabled = useExcelStore((s) => s.backupEnabled);
  const backupLoading = useExcelStore((s) => s.backupLoading);
  const appliedPaths = useExcelStore((s) => s.appliedPaths);
  const fetchBackups = useExcelStore((s) => s.fetchBackups);
  const applyFile = useExcelStore((s) => s.applyFile);
  const applyAll = useExcelStore((s) => s.applyAll);
  const discardFile = useExcelStore((s) => s.discardFile);
  const discardAll = useExcelStore((s) => s.discardAll);

  const [confirmAllOpen, setConfirmAllOpen] = useState(false);
  const [applyingAll, setApplyingAll] = useState(false);
  const [applyingPaths, setApplyingPaths] = useState<Set<string>>(new Set());

  useEffect(() => {
    if (open && activeSessionId) {
      fetchBackups(activeSessionId);
    }
  }, [open, activeSessionId, fetchBackups]);

  const handleApplyOne = useCallback(
    async (originalPath: string) => {
      if (!activeSessionId) return;
      setApplyingPaths((prev) => new Set(prev).add(originalPath));
      await applyFile(activeSessionId, originalPath);
      setApplyingPaths((prev) => {
        const next = new Set(prev);
        next.delete(originalPath);
        return next;
      });
    },
    [activeSessionId, applyFile]
  );

  const handleApplyAll = useCallback(async () => {
    if (!activeSessionId) return;
    setApplyingAll(true);
    setConfirmAllOpen(false);
    await applyAll(activeSessionId);
    setApplyingAll(false);
  }, [activeSessionId, applyAll]);

  const handleDiscardOne = useCallback(
    async (originalPath: string) => {
      if (!activeSessionId) return;
      await discardFile(activeSessionId, originalPath);
    },
    [activeSessionId, discardFile]
  );

  const handleDiscardAll = useCallback(async () => {
    if (!activeSessionId) return;
    await discardAll(activeSessionId);
  }, [activeSessionId, discardAll]);

  const fileName = (p: string) => p.split("/").pop() || p;

  return (
    <>
      <Dialog open={open} onOpenChange={onOpenChange}>
        <DialogContent className="sm:max-w-[520px] max-h-[80vh] flex flex-col">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2 text-base">
              <Upload className="h-4 w-4" style={{ color: "var(--em-primary)" }} />
              应用到原文件
            </DialogTitle>
            <DialogDescription className="text-sm">
              将沙盒中修改过的文件覆盖回原始位置。
            </DialogDescription>
          </DialogHeader>

          <div className="flex-1 min-h-0 overflow-y-auto -mx-6 px-6">
            {backupLoading ? (
              <div className="flex items-center justify-center py-12 text-muted-foreground text-sm">
                <Loader2 className="h-4 w-4 animate-spin mr-2" />
                加载中...
              </div>
            ) : !backupEnabled ? (
              <div className="flex items-center justify-center py-12 text-muted-foreground text-sm">
                当前会话未启用备份模式
              </div>
            ) : pendingBackups.length === 0 ? (
              <div className="flex flex-col items-center justify-center py-12 text-sm text-muted-foreground gap-2">
                <Check className="h-6 w-6 text-emerald-500" />
                <span>所有修改已应用到原文件</span>
              </div>
            ) : (
              <div className="space-y-1">
                {pendingBackups.map((backup) => {
                  const isApplying = applyingPaths.has(backup.original_path);
                  const isApplied = appliedPaths.has(backup.original_path);
                  const modTime = backup.modified_at
                    ? new Date(backup.modified_at * 1000).toLocaleString("zh-CN", {
                        month: "short",
                        day: "numeric",
                        hour: "2-digit",
                        minute: "2-digit",
                      })
                    : null;

                  return (
                    <div
                      key={backup.original_path}
                      className={`flex items-center gap-2 px-3 py-2 rounded-lg border transition-colors ${
                        isApplied
                          ? "border-emerald-500/30 bg-emerald-50/50 dark:bg-emerald-950/20"
                          : "border-border hover:bg-muted/30"
                      }`}
                    >
                      <FileSpreadsheet
                        className="h-4 w-4 flex-shrink-0"
                        style={{ color: "var(--em-primary)" }}
                      />
                      <div className="flex-1 min-w-0">
                        <div className="text-sm font-medium truncate">
                          {fileName(backup.original_path)}
                        </div>
                        <div className="text-[10px] text-muted-foreground truncate">
                          {backup.original_path}
                          {modTime && <span className="ml-2">{modTime}</span>}
                        </div>
                      </div>

                      {isApplied ? (
                        <span className="flex items-center gap-1 text-xs text-emerald-600 dark:text-emerald-400">
                          <Check className="h-3 w-3" />
                          已应用
                        </span>
                      ) : (
                        <div className="flex items-center gap-1">
                          <button
                            onClick={() => handleDiscardOne(backup.original_path)}
                            className="p-1.5 rounded text-muted-foreground hover:text-destructive hover:bg-destructive/10 transition-colors"
                            title="丢弃"
                          >
                            <Trash2 className="h-3.5 w-3.5" />
                          </button>
                          <Button
                            size="sm"
                            className="h-7 px-2.5 text-xs text-white"
                            style={{ backgroundColor: "var(--em-primary)" }}
                            disabled={isApplying || applyingAll}
                            onClick={() => handleApplyOne(backup.original_path)}
                          >
                            {isApplying ? (
                              <Loader2 className="h-3 w-3 animate-spin" />
                            ) : (
                              <>
                                <Upload className="h-3 w-3 mr-1" />
                                Apply
                              </>
                            )}
                          </Button>
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            )}
          </div>

          {/* Footer actions */}
          {pendingBackups.length > 0 && (
            <div className="flex items-center justify-between pt-3 border-t border-border -mx-6 px-6">
              <button
                onClick={handleDiscardAll}
                className="text-xs text-muted-foreground hover:text-destructive transition-colors"
              >
                丢弃全部
              </button>
              <Button
                className="text-white"
                style={{ backgroundColor: "var(--em-primary)" }}
                disabled={applyingAll}
                onClick={() => setConfirmAllOpen(true)}
              >
                {applyingAll ? (
                  <>
                    <Loader2 className="h-4 w-4 animate-spin mr-1.5" />
                    应用中...
                  </>
                ) : (
                  <>
                    <Upload className="h-4 w-4 mr-1.5" />
                    全部应用 ({pendingBackups.length} 文件)
                  </>
                )}
              </Button>
            </div>
          )}
        </DialogContent>
      </Dialog>

      {/* Confirm-all dialog */}
      <Dialog open={confirmAllOpen} onOpenChange={setConfirmAllOpen}>
        <DialogContent className="sm:max-w-[420px]" onOpenAutoFocus={(e) => e.preventDefault()}>
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2 text-base">
              <AlertTriangle className="h-4 w-4 text-amber-500" />
              确认全部应用
            </DialogTitle>
            <DialogDescription className="text-sm mt-2">
              将覆盖以下 {pendingBackups.length} 个原始文件：
            </DialogDescription>
          </DialogHeader>
          <div className="max-h-[200px] overflow-y-auto space-y-1 my-2">
            {pendingBackups.map((b) => (
              <div key={b.original_path} className="text-xs text-muted-foreground truncate px-2 py-0.5">
                {fileName(b.original_path)}
                <span className="ml-1 opacity-50">({b.original_path})</span>
              </div>
            ))}
          </div>
          <p className="text-xs text-amber-600 dark:text-amber-400 flex items-center gap-1">
            <AlertTriangle className="h-3 w-3 flex-shrink-0" />
            此操作将覆盖原始文件，请确保已检查修改内容。
          </p>
          <div className="flex items-center justify-end gap-2 pt-2">
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setConfirmAllOpen(false)}
              className="text-muted-foreground"
            >
              取消
            </Button>
            <Button
              size="sm"
              className="text-white"
              style={{ backgroundColor: "var(--em-primary)" }}
              onClick={handleApplyAll}
            >
              确认应用全部
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </>
  );
}
