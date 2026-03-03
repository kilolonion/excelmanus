"use client";

import { useEffect, useState, useCallback } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  History,
  Loader2,
  RotateCcw,
  FileText,
  Wrench,
  ChevronDown,
  ChevronUp,
  AlertTriangle,
  CheckCircle2,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { SlidePanel } from "@/components/ui/slide-panel";
import { useSessionStore } from "@/stores/session-store";
import { useIsMobile } from "@/hooks/use-mobile";
import {
  fetchCheckpoints,
  checkpointRollback,
  type CheckpointItem,
} from "@/lib/api";

function formatTime(isoStr: string): string {
  try {
    const d = new Date(isoStr);
    return d.toLocaleTimeString(undefined, {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    });
  } catch {
    return isoStr;
  }
}

function formatDate(isoStr: string): string {
  try {
    const d = new Date(isoStr);
    const today = new Date();
    if (d.toDateString() === today.toDateString()) return "今天";
    const yesterday = new Date(today);
    yesterday.setDate(yesterday.getDate() - 1);
    if (d.toDateString() === yesterday.toDateString()) return "昨天";
    return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
  } catch {
    return "";
  }
}

function shortenPath(path: string): string {
  const parts = path.replace(/\\/g, "/").split("/");
  return parts[parts.length - 1] || path;
}

interface CheckpointTimelineItemProps {
  cp: CheckpointItem;
  isLatest: boolean;
  onRollback: (turnNumber: number) => void;
  rolling: boolean;
  isMobile: boolean;
}

function CheckpointTimelineItem({ cp, isLatest, onRollback, rolling, isMobile }: CheckpointTimelineItemProps) {
  const [expanded, setExpanded] = useState(false);

  return (
    <div className="relative pl-6">
      {/* Timeline dot */}
      <div
        className="absolute left-0 top-2 w-3 h-3 rounded-full border-2 z-10"
        style={{
          borderColor: isLatest ? "var(--em-primary)" : "var(--border)",
          backgroundColor: isLatest ? "var(--em-primary)" : "var(--background)",
        }}
      />

      <div
        className={`group rounded-lg border border-border/60 transition-colors cursor-pointer ${
          isMobile ? "p-3.5 active:bg-muted/30" : "p-3 hover:border-border"
        }`}
        onClick={() => setExpanded(!expanded)}
      >
        {/* Header */}
        <div className="flex items-center justify-between gap-2">
          <div className="flex items-center gap-2 min-w-0">
            <span className="text-xs font-mono font-semibold tabular-nums" style={{ color: "var(--em-primary)" }}>
              轮次 {cp.turn_number}
            </span>
            <span className="text-[10px] text-muted-foreground tabular-nums">
              {formatTime(cp.created_at)}
            </span>
            {isLatest && (
              <Badge variant="outline" className="h-4 px-1 text-[9px] border-emerald-500/40 text-emerald-600 dark:text-emerald-400">
                当前
              </Badge>
            )}
          </div>
          <div className="flex items-center gap-1">
            {!isLatest && (
              isMobile ? (
                <Button
                  variant="ghost"
                  size="icon"
                  className="h-8 w-8 shrink-0"
                  disabled={rolling}
                  onClick={(e) => {
                    e.stopPropagation();
                    onRollback(cp.turn_number);
                  }}
                >
                  {rolling ? (
                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  ) : (
                    <RotateCcw className="h-3.5 w-3.5" />
                  )}
                </Button>
              ) : (
                <TooltipProvider delayDuration={200}>
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <Button
                        variant="ghost"
                        size="icon"
                        className="h-6 w-6 opacity-0 group-hover:opacity-100 transition-opacity"
                        disabled={rolling}
                        onClick={(e) => {
                          e.stopPropagation();
                          onRollback(cp.turn_number);
                        }}
                      >
                        {rolling ? (
                          <Loader2 className="h-3 w-3 animate-spin" />
                        ) : (
                          <RotateCcw className="h-3 w-3" />
                        )}
                      </Button>
                    </TooltipTrigger>
                    <TooltipContent side="left" className="text-xs">
                      回退到此轮次
                    </TooltipContent>
                  </Tooltip>
                </TooltipProvider>
              )
            )}
            {expanded ? (
              <ChevronUp className="h-3 w-3 text-muted-foreground" />
            ) : (
              <ChevronDown className="h-3 w-3 text-muted-foreground" />
            )}
          </div>
        </div>

        {/* Summary line */}
        <div className="flex items-center gap-2 mt-1.5 text-[11px] text-muted-foreground">
          <span className="flex items-center gap-0.5">
            <FileText className="h-3 w-3" />
            {cp.files_modified.length} 文件
          </span>
          {cp.tool_names.length > 0 && (
            <span className="flex items-center gap-0.5">
              <Wrench className="h-3 w-3" />
              {cp.tool_names.length} 工具
            </span>
          )}
          <span className="tabular-nums">{cp.version_count} 版本</span>
        </div>

        {/* Expanded details */}
        <AnimatePresence>
          {expanded && (
            <motion.div
              initial={{ height: 0, opacity: 0 }}
              animate={{ height: "auto", opacity: 1 }}
              exit={{ height: 0, opacity: 0 }}
              transition={{ duration: 0.15 }}
              className="overflow-hidden"
            >
              <div className="mt-2 pt-2 border-t border-border/40 space-y-1.5">
                {cp.files_modified.length > 0 && (
                  <div>
                    <div className="text-[10px] font-medium text-muted-foreground mb-0.5">修改文件</div>
                    <div className="flex flex-wrap gap-1">
                      {cp.files_modified.map((f) => (
                        <Badge
                          key={f}
                          variant="secondary"
                          className="h-5 px-1.5 text-[10px] font-mono max-w-[140px] sm:max-w-[200px] truncate"
                          title={f}
                        >
                          {shortenPath(f)}
                        </Badge>
                      ))}
                    </div>
                  </div>
                )}
                {cp.tool_names.length > 0 && (
                  <div>
                    <div className="text-[10px] font-medium text-muted-foreground mb-0.5">使用工具</div>
                    <div className="flex flex-wrap gap-1">
                      {cp.tool_names.map((t) => (
                        <Badge
                          key={t}
                          variant="outline"
                          className="h-5 px-1.5 text-[10px]"
                        >
                          {t}
                        </Badge>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            </motion.div>
          )}
        </AnimatePresence>
      </div>
    </div>
  );
}

export function CheckpointTimeline() {
  const activeSessionId = useSessionStore((s) => s.activeSessionId);
  const isMobile = useIsMobile();
  const [panelOpen, setPanelOpen] = useState(false);
  const [checkpoints, setCheckpoints] = useState<CheckpointItem[]>([]);
  const [enabled, setEnabled] = useState(false);
  const [loading, setLoading] = useState(false);
  const [rolling, setRolling] = useState(false);
  const [rollbackTarget, setRollbackTarget] = useState<number | null>(null);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [lastResult, setLastResult] = useState<{ ok: boolean; message: string } | null>(null);

  const load = useCallback(async () => {
    if (!activeSessionId) {
      setCheckpoints([]);
      setEnabled(false);
      return;
    }
    setLoading(true);
    try {
      const data = await fetchCheckpoints(activeSessionId);
      setCheckpoints(data.checkpoints);
      setEnabled(data.checkpoint_enabled);
    } catch {
      setCheckpoints([]);
      setEnabled(false);
    } finally {
      setLoading(false);
    }
  }, [activeSessionId]);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    if (!panelOpen) return;
    load();
    const id = setInterval(load, 10000);
    return () => clearInterval(id);
  }, [panelOpen, load]);

  const handleRollbackClick = (turnNumber: number) => {
    setRollbackTarget(turnNumber);
    setConfirmOpen(true);
  };

  const handleConfirmRollback = async () => {
    if (!activeSessionId || rollbackTarget == null) return;
    setConfirmOpen(false);
    setRolling(true);
    setLastResult(null);
    try {
      const res = await checkpointRollback(activeSessionId, rollbackTarget);
      setLastResult({
        ok: true,
        message: `已回退到轮次 ${res.turn_number}，恢复了 ${res.count} 个文件`,
      });
      await load();
    } catch (err) {
      setLastResult({
        ok: false,
        message: err instanceof Error ? err.message : "回退失败",
      });
    } finally {
      setRolling(false);
      setRollbackTarget(null);
    }
  };

  if (!enabled) return null;

  const sorted = [...checkpoints].sort((a, b) => b.turn_number - a.turn_number);
  const latestTurn = sorted.length > 0 ? sorted[0].turn_number : -1;

  // Group checkpoints by date
  const groups: { date: string; items: CheckpointItem[] }[] = [];
  for (const cp of sorted) {
    const dateLabel = formatDate(cp.created_at);
    const existing = groups.find((g) => g.date === dateLabel);
    if (existing) {
      existing.items.push(cp);
    } else {
      groups.push({ date: dateLabel, items: [cp] });
    }
  }

  return (
    <>
      {/* Topbar trigger button */}
      <TooltipProvider delayDuration={300}>
        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              variant="ghost"
              size="icon"
              className="h-8 w-8 sm:h-7 sm:w-7 p-0 relative"
              onClick={() => setPanelOpen(true)}
              aria-label="Checkpoint 时间线"
            >
              <History className="h-4 w-4 sm:h-3.5 sm:w-3.5" />
              {checkpoints.length > 0 && (
                <span
                  className="absolute -top-0.5 -right-0.5 flex h-3.5 min-w-[14px] items-center justify-center rounded-full text-[9px] font-bold text-white px-0.5"
                  style={{ backgroundColor: "var(--em-primary)" }}
                >
                  {checkpoints.length}
                </span>
              )}
            </Button>
          </TooltipTrigger>
          <TooltipContent side="bottom" className="text-xs">
            Checkpoint 时间线{checkpoints.length > 0 ? ` (${checkpoints.length})` : ""}
          </TooltipContent>
        </Tooltip>
      </TooltipProvider>

      {/* Slide Panel */}
      <SlidePanel
        open={panelOpen}
        onClose={() => {
          setPanelOpen(false);
          setLastResult(null);
        }}
        title="Checkpoint 时间线"
        icon={<History className="h-4 w-4" style={{ color: "var(--em-primary)" }} />}
        width={400}
      >
        <div className="px-4 py-3" style={{ paddingBottom: isMobile ? "max(0.75rem, env(safe-area-inset-bottom))" : undefined }}>
          {/* Result banner */}
          <AnimatePresence>
            {lastResult && (
              <motion.div
                initial={{ height: 0, opacity: 0 }}
                animate={{ height: "auto", opacity: 1 }}
                exit={{ height: 0, opacity: 0 }}
                className="overflow-hidden mb-3"
              >
                <div
                  className={`flex items-center gap-2 rounded-lg px-3 py-2 text-xs ${
                    lastResult.ok
                      ? "bg-emerald-500/10 text-emerald-700 dark:text-emerald-300"
                      : "bg-red-500/10 text-red-700 dark:text-red-300"
                  }`}
                >
                  {lastResult.ok ? (
                    <CheckCircle2 className="h-3.5 w-3.5 shrink-0" />
                  ) : (
                    <AlertTriangle className="h-3.5 w-3.5 shrink-0" />
                  )}
                  {lastResult.message}
                </div>
              </motion.div>
            )}
          </AnimatePresence>

          {loading && checkpoints.length === 0 ? (
            <div className="flex items-center justify-center py-12 text-muted-foreground text-sm">
              <Loader2 className="h-4 w-4 animate-spin mr-2" />
              加载中…
            </div>
          ) : checkpoints.length === 0 ? (
            <div className="text-center py-12 text-muted-foreground text-sm space-y-1">
              <History className="h-8 w-8 mx-auto opacity-30 mb-2" />
              <div>暂无 Checkpoint</div>
              <div className="text-xs opacity-60">
                AI 执行工具操作后会自动创建文件快照
              </div>
            </div>
          ) : (
            <div>
              <div className="space-y-4">
                {groups.map((group) => (
                  <div key={group.date}>
                    <div className="text-[10px] font-medium text-muted-foreground uppercase tracking-wider mb-2">
                      {group.date}
                    </div>
                    {/* Timeline line */}
                    <div className="relative">
                      <div
                        className="absolute left-[5px] top-4 bottom-4 w-px"
                        style={{ backgroundColor: "var(--border)" }}
                      />
                      <div className="space-y-2">
                        {group.items.map((cp) => (
                          <CheckpointTimelineItem
                            key={cp.turn_number}
                            cp={cp}
                            isLatest={cp.turn_number === latestTurn}
                            onRollback={handleRollbackClick}
                            rolling={rolling && rollbackTarget === cp.turn_number}
                            isMobile={isMobile}
                          />
                        ))}
                      </div>
                    </div>
                  </div>
                ))}
              </div>

              <div className="mt-4 pt-3 border-t border-border/40 text-[10px] text-muted-foreground/60 text-center pb-2">
                共 {checkpoints.length} 个 checkpoint · 点击轮次展开详情
              </div>
            </div>
          )}
        </div>
      </SlidePanel>

      {/* Rollback confirmation dialog */}
      <Dialog open={confirmOpen} onOpenChange={setConfirmOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <AlertTriangle className="h-4 w-4 text-amber-500" />
              确认回退到轮次 {rollbackTarget}
            </DialogTitle>
            <DialogDescription>
              将回退文件状态到轮次 {rollbackTarget} 之前的快照。此操作会覆盖当前文件内容，无法撤销。
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setConfirmOpen(false)}>
              取消
            </Button>
            <Button
              variant="destructive"
              onClick={handleConfirmRollback}
              disabled={rolling}
            >
              {rolling ? (
                <>
                  <Loader2 className="h-3.5 w-3.5 mr-1.5 animate-spin" />
                  回退中…
                </>
              ) : (
                "确认回退"
              )}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
