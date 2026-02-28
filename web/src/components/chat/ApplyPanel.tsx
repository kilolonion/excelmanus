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
  Download,
  Undo2,
  Eye,
  ChevronDown,
  ChevronRight,
  Activity,
  ArrowUpFromLine,
  PackageCheck,
} from "lucide-react";
import { motion, AnimatePresence } from "framer-motion";
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
import { buildBackupDownloadUrl, type BackupFile, type AppliedFile } from "@/lib/api";

/** 格式化文件大小差异 */
function formatSizeDelta(bytes: number): string {
  const abs = Math.abs(bytes);
  const sign = bytes >= 0 ? "+" : "-";
  if (abs < 1024) return `${sign}${abs} B`;
  if (abs < 1024 * 1024) return `${sign}${(abs / 1024).toFixed(1)} KB`;
  return `${sign}${(abs / (1024 * 1024)).toFixed(1)} MB`;
}

/** 生成单个文件的变更摘要标签数组 */
function renderSummaryTags(backup: BackupFile): { label: string; color: string }[] {
  const s = backup.summary;
  if (!s) return [];
  const tags: { label: string; color: string }[] = [];
  if (s.cells_added) tags.push({ label: `${s.cells_added} 新增`, color: "emerald" });
  if (s.cells_changed) tags.push({ label: `${s.cells_changed} 修改`, color: "amber" });
  if (s.cells_removed) tags.push({ label: `${s.cells_removed} 删除`, color: "red" });
  if (s.sheets_added?.length) tags.push({ label: `+${s.sheets_added.length} sheet`, color: "blue" });
  if (s.sheets_removed?.length) tags.push({ label: `-${s.sheets_removed.length} sheet`, color: "rose" });
  if (s.size_delta_bytes != null && s.size_delta_bytes !== 0) {
    tags.push({ label: formatSizeDelta(s.size_delta_bytes), color: s.size_delta_bytes > 0 ? "slate" : "slate" });
  }
  return tags;
}

const TAG_COLORS: Record<string, string> = {
  emerald: "text-emerald-600 dark:text-emerald-400 bg-emerald-500/10",
  amber: "text-amber-600 dark:text-amber-400 bg-amber-500/10",
  red: "text-red-600 dark:text-red-400 bg-red-500/10",
  blue: "text-blue-600 dark:text-blue-400 bg-blue-500/10",
  rose: "text-rose-600 dark:text-rose-400 bg-rose-500/10",
  slate: "text-muted-foreground bg-muted/60",
};

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
  const backupInFlight = useExcelStore((s) => s.backupInFlight);
  const appliedPaths = useExcelStore((s) => s.appliedPaths);
  const undoableApplies = useExcelStore((s) => s.undoableApplies);
  const fetchBackups = useExcelStore((s) => s.fetchBackups);
  const applyFile = useExcelStore((s) => s.applyFile);
  const applyAll = useExcelStore((s) => s.applyAll);
  const discardFile = useExcelStore((s) => s.discardFile);
  const discardAll = useExcelStore((s) => s.discardAll);
  const undoApply = useExcelStore((s) => s.undoApply);
  const openPanel = useExcelStore((s) => s.openPanel);

  const [confirmAllOpen, setConfirmAllOpen] = useState(false);
  const [applyingAll, setApplyingAll] = useState(false);
  const [applyingPaths, setApplyingPaths] = useState<Set<string>>(new Set());
  const [undoingPaths, setUndoingPaths] = useState<Set<string>>(new Set());
  const [showUndoSection, setShowUndoSection] = useState(false);

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

  const handleUndoOne = useCallback(
    async (item: AppliedFile) => {
      if (!activeSessionId) return;
      setUndoingPaths((prev) => new Set(prev).add(item.original));
      await undoApply(activeSessionId, item);
      setUndoingPaths((prev) => {
        const next = new Set(prev);
        next.delete(item.original);
        return next;
      });
    },
    [activeSessionId, undoApply]
  );

  const handlePreviewFile = useCallback(
    (filePath: string) => {
      openPanel(filePath);
      onOpenChange(false);
    },
    [openPanel, onOpenChange]
  );

  const handleDownload = useCallback(
    (backupPath: string) => {
      if (!activeSessionId) return;
      const url = buildBackupDownloadUrl(activeSessionId, backupPath);
      window.open(url, "_blank");
    },
    [activeSessionId]
  );

  const fileName = (p: string) => p.split("/").pop() || p;

  return (
    <>
      <Dialog open={open} onOpenChange={onOpenChange}>
        <DialogContent className="sm:max-w-[560px] max-h-[80vh] flex flex-col !rounded-3xl !p-0 overflow-hidden border-0 shadow-2xl" showCloseButton={false}>
          {/* 顶部品牌色条 */}
          <div className="h-1.5 w-full rounded-t-3xl" style={{ backgroundColor: "var(--em-primary)" }} />
          {/* 顶部光晕 */}
          <div className="absolute top-0 inset-x-0 h-24 bg-gradient-to-b from-[var(--em-primary-alpha-10)] via-[var(--em-primary-alpha-06)] to-transparent pointer-events-none" />

          <div className="relative px-6 pt-5 pb-0">
            <div className="flex items-start gap-4">
              {/* 图标 */}
              <div className="relative flex-shrink-0 mt-0.5">
                <div className="absolute inset-0 rounded-full opacity-20" style={{ backgroundColor: "var(--em-primary)" }} />
                <div className="relative flex items-center justify-center w-11 h-11 rounded-full border" style={{ backgroundColor: "var(--em-primary-alpha-10)", borderColor: "var(--em-primary-alpha-25)" }}>
                  <ArrowUpFromLine className="h-5 w-5" style={{ color: "var(--em-primary)" }} />
                </div>
              </div>
              <div className="flex-1 min-w-0">
                <h3 className="font-semibold text-base text-foreground">应用到原文件</h3>
                <p className="text-sm text-muted-foreground mt-1">
                  将沙盒中修改过的文件覆盖回原始位置。应用后支持撤销。
                </p>
              </div>
              {/* 关闭按钮 */}
              <button
                onClick={() => onOpenChange(false)}
                className="flex-shrink-0 text-muted-foreground/50 hover:text-foreground transition-colors p-1.5 rounded-xl hover:bg-muted/80"
              >
                <X className="h-4.5 w-4.5" />
              </button>
            </div>
          </div>

          {/* In-flight warning banner */}
          {backupInFlight && (
            <div className="mx-6 mt-4 flex items-center gap-2.5 px-4 py-2.5 rounded-2xl bg-amber-50 dark:bg-amber-950/30 border border-amber-200/60 dark:border-amber-800/40 text-amber-700 dark:text-amber-300 text-xs">
              <Activity className="h-4 w-4 flex-shrink-0 animate-pulse" />
              <span>Agent 正在处理中，建议等待完成后再应用修改。</span>
            </div>
          )}

          <div className="flex-1 min-h-0 overflow-y-auto px-6 pt-4 pb-2">
            {backupLoading ? (
              <div className="flex flex-col items-center justify-center py-16 text-muted-foreground text-sm gap-3">
                <div className="relative">
                  <div className="absolute inset-0 rounded-full opacity-20 animate-ping" style={{ backgroundColor: "var(--em-primary)" }} />
                  <Loader2 className="h-6 w-6 animate-spin" style={{ color: "var(--em-primary)" }} />
                </div>
                <span>加载文件列表...</span>
              </div>
            ) : !backupEnabled ? (
              <div className="flex flex-col items-center justify-center py-16 text-sm text-muted-foreground gap-3">
                <div className="flex items-center justify-center w-12 h-12 rounded-full bg-muted/50">
                  <AlertTriangle className="h-5 w-5 text-muted-foreground/50" />
                </div>
                <span>当前会话未启用备份模式</span>
              </div>
            ) : pendingBackups.length === 0 && undoableApplies.length === 0 ? (
              <div className="flex flex-col items-center justify-center py-16 text-sm text-muted-foreground gap-3">
                <div className="flex items-center justify-center w-12 h-12 rounded-full bg-emerald-500/10">
                  <PackageCheck className="h-5 w-5 text-emerald-500" />
                </div>
                <span className="font-medium">所有修改已应用到原文件</span>
              </div>
            ) : (
              <div className="space-y-2">
                {pendingBackups.map((backup, idx) => {
                  const isApplying = applyingPaths.has(backup.original_path);
                  const isApplied = appliedPaths.has(backup.original_path);
                  const modTime = backup.modified_at
                    ? new Date(backup.modified_at * 1000).toLocaleString("zh-CN", {
                        month: "numeric",
                        day: "numeric",
                        hour: "2-digit",
                        minute: "2-digit",
                      })
                    : null;
                  const summaryTags = renderSummaryTags(backup);

                  return (
                    <motion.div
                      key={backup.original_path}
                      initial={{ opacity: 0, y: 8 }}
                      animate={{ opacity: 1, y: 0 }}
                      transition={{ delay: idx * 0.04, duration: 0.25 }}
                      className={`group relative rounded-2xl border transition-all duration-200 ${
                        isApplied
                          ? "border-emerald-500/30 bg-emerald-50/50 dark:bg-emerald-950/20"
                          : "border-border/60 hover:border-border hover:bg-muted/20 hover:shadow-sm"
                      }`}
                    >
                      <div className="px-4 py-3">
                        <div className="flex items-center gap-3">
                          <button
                            onClick={() => handlePreviewFile(backup.backup_path)}
                            className="flex-shrink-0 flex items-center justify-center w-9 h-9 rounded-xl transition-colors hover:bg-muted/60"
                            style={{ backgroundColor: "var(--em-primary-alpha-06)" }}
                            title="预览文件"
                          >
                            <FileSpreadsheet
                              className="h-4.5 w-4.5"
                              style={{ color: "var(--em-primary)" }}
                            />
                          </button>
                          <div className="flex-1 min-w-0">
                            <div className="text-sm font-semibold truncate text-foreground">
                              {fileName(backup.original_path)}
                            </div>
                            <div className="text-[11px] text-muted-foreground/70 truncate mt-0.5 font-mono">
                              {backup.original_path}
                              {modTime && <span className="ml-2 font-sans">{modTime}</span>}
                            </div>
                          </div>

                          {isApplied ? (
                            <span className="flex items-center gap-1.5 text-xs font-medium text-emerald-600 dark:text-emerald-400 bg-emerald-500/10 px-3 py-1.5 rounded-full">
                              <Check className="h-3.5 w-3.5" />
                              已应用
                            </span>
                          ) : (
                            <div className="flex items-center gap-1">
                              <button
                                onClick={() => handleDiscardOne(backup.original_path)}
                                className="p-2 rounded-xl text-muted-foreground/40 hover:text-destructive hover:bg-destructive/8 transition-all"
                                title="丢弃"
                              >
                                <Trash2 className="h-4 w-4" />
                              </button>
                              <Button
                                size="sm"
                                className="h-8 px-3.5 text-xs text-white rounded-xl font-semibold gap-1.5 shadow-sm hover:shadow-md transition-all"
                                style={{ backgroundColor: "var(--em-primary)" }}
                                disabled={isApplying || applyingAll || backupInFlight}
                                onClick={() => handleApplyOne(backup.original_path)}
                              >
                                {isApplying ? (
                                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                                ) : (
                                  <>
                                    <Upload className="h-3.5 w-3.5" />
                                    Apply
                                  </>
                                )}
                              </Button>
                            </div>
                          )}
                        </div>

                        {/* 变更摘要标签 */}
                        {summaryTags.length > 0 && (
                          <div className="flex flex-wrap items-center gap-1.5 mt-2.5 ml-12">
                            {summaryTags.map((tag, i) => (
                              <span
                                key={i}
                                className={`inline-flex items-center text-[10px] font-medium px-2 py-0.5 rounded-full ${TAG_COLORS[tag.color] || TAG_COLORS.slate}`}
                              >
                                {tag.label}
                              </span>
                            ))}
                          </div>
                        )}
                      </div>
                    </motion.div>
                  );
                })}
              </div>
            )}

            {/* Undo section — 已应用文件可撤销 */}
            {undoableApplies.length > 0 && (
              <div className="mt-4 pt-3 border-t border-border/40">
                <button
                  onClick={() => setShowUndoSection(!showUndoSection)}
                  className="flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground transition-colors mb-2 group/undo"
                >
                  <ChevronDown className={`h-3.5 w-3.5 transition-transform duration-200 ${showUndoSection ? "" : "-rotate-90"}`} />
                  <span className="font-medium">已应用（可撤销）</span>
                  <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-muted/60 text-muted-foreground/70">{undoableApplies.length}</span>
                </button>
                <AnimatePresence>
                  {showUndoSection && (
                    <motion.div
                      initial={{ height: 0, opacity: 0 }}
                      animate={{ height: "auto", opacity: 1 }}
                      exit={{ height: 0, opacity: 0 }}
                      transition={{ duration: 0.2 }}
                      className="overflow-hidden"
                    >
                      <div className="space-y-1.5">
                        {undoableApplies.map((item) => (
                          <div
                            key={item.undo_path || item.original}
                            className="flex items-center gap-3 px-4 py-2.5 rounded-xl border border-emerald-500/15 bg-emerald-50/30 dark:bg-emerald-950/10"
                          >
                            <div className="flex items-center justify-center w-6 h-6 rounded-lg bg-emerald-500/10">
                              <Check className="h-3.5 w-3.5 text-emerald-500" />
                            </div>
                            <div className="flex-1 min-w-0 text-xs font-medium truncate text-muted-foreground">
                              {fileName(item.original)}
                            </div>
                            {item.undo_path && (
                              <button
                                onClick={() => handleUndoOne(item)}
                                disabled={undoingPaths.has(item.original)}
                                className="flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-[11px] font-medium text-amber-600 dark:text-amber-400 hover:bg-amber-50 dark:hover:bg-amber-950/30 transition-colors border border-amber-200/50 dark:border-amber-800/30"
                                title="撤销应用"
                              >
                                {undoingPaths.has(item.original) ? (
                                  <Loader2 className="h-3 w-3 animate-spin" />
                                ) : (
                                  <>
                                    <Undo2 className="h-3 w-3" />
                                    撤销
                                  </>
                                )}
                              </button>
                            )}
                          </div>
                        ))}
                      </div>
                    </motion.div>
                  )}
                </AnimatePresence>
              </div>
            )}
          </div>

          {/* Footer actions */}
          {pendingBackups.length > 0 && (
            <div className="flex items-center justify-between px-6 py-4 border-t border-border/40 bg-muted/15">
              <button
                onClick={handleDiscardAll}
                className="text-xs font-medium text-muted-foreground/70 hover:text-destructive transition-colors px-2 py-1 rounded-lg hover:bg-destructive/5"
              >
                丢弃全部
              </button>
              <Button
                className="text-white h-10 px-5 rounded-xl font-semibold text-sm gap-2 shadow-md hover:shadow-lg transition-all"
                style={{ backgroundColor: "var(--em-primary)" }}
                disabled={applyingAll || backupInFlight}
                onClick={() => setConfirmAllOpen(true)}
              >
                {applyingAll ? (
                  <>
                    <Loader2 className="h-4 w-4 animate-spin" />
                    应用中...
                  </>
                ) : (
                  <>
                    <Upload className="h-4 w-4" />
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
        <DialogContent className="sm:max-w-[440px] !rounded-3xl !p-0 overflow-hidden border-0 shadow-2xl" showCloseButton={false} onOpenAutoFocus={(e) => e.preventDefault()}>
          {/* 顶部警告色条 */}
          <div className="h-1.5 w-full rounded-t-3xl bg-amber-500" />
          <div className="absolute top-0 inset-x-0 h-20 bg-gradient-to-b from-amber-500/15 via-amber-500/5 to-transparent pointer-events-none" />

          <div className="relative px-6 pt-5 pb-0">
            <div className="flex items-start gap-4">
              <div className="relative flex-shrink-0">
                <div className="flex items-center justify-center w-11 h-11 rounded-full bg-amber-500/10 border border-amber-500/20">
                  <AlertTriangle className="h-5 w-5 text-amber-500" />
                </div>
              </div>
              <div className="flex-1 min-w-0">
                <h3 className="font-semibold text-base text-foreground">确认全部应用</h3>
                <p className="text-sm text-muted-foreground mt-1">
                  将覆盖以下 {pendingBackups.length} 个原始文件：
                </p>
              </div>
            </div>
          </div>

          <div className="px-6 py-3">
            <div className="max-h-[200px] overflow-y-auto space-y-1.5 rounded-2xl border border-border/40 bg-muted/15 p-3">
              {pendingBackups.map((b) => (
                <div key={b.original_path} className="flex items-center gap-2 text-xs py-1">
                  <FileSpreadsheet className="h-3.5 w-3.5 flex-shrink-0" style={{ color: "var(--em-primary)" }} />
                  <span className="font-medium truncate">{fileName(b.original_path)}</span>
                  <span className="text-muted-foreground/40 truncate text-[10px] font-mono">({b.original_path})</span>
                </div>
              ))}
            </div>

            <div className="flex items-center gap-2 mt-3 px-3 py-2.5 rounded-xl bg-amber-50/80 dark:bg-amber-950/20 border border-amber-200/40 dark:border-amber-800/30">
              <AlertTriangle className="h-3.5 w-3.5 flex-shrink-0 text-amber-500" />
              <p className="text-[11px] text-amber-600 dark:text-amber-400">
                此操作将覆盖原始文件，应用后可在面板中撤销。
              </p>
            </div>
          </div>

          <div className="flex items-center justify-end gap-2.5 px-6 py-4 border-t border-border/40 bg-muted/15">
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setConfirmAllOpen(false)}
              className="text-muted-foreground rounded-xl h-9 px-4"
            >
              取消
            </Button>
            <Button
              size="sm"
              className="text-white rounded-xl h-9 px-5 font-semibold shadow-sm"
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
