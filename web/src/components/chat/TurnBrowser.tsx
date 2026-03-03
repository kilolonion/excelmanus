"use client";

import { useEffect, useState, useCallback } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  ListOrdered,
  Loader2,
  RotateCcw,
  MessageSquareText,
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
import { useIsMobile } from "@/hooks/use-mobile";
import { useSessionStore } from "@/stores/session-store";
import {
  fetchChatTurns,
  rollbackChat,
  rollbackPreview,
  type ChatTurn,
  type RollbackPreviewResult,
} from "@/lib/api";

interface TurnItemProps {
  turn: ChatTurn;
  isLatest: boolean;
  onRollback: (turnIndex: number) => void;
  rolling: boolean;
}

function TurnItem({ turn, isLatest, onRollback, rolling }: TurnItemProps) {
  return (
    <div className="relative pl-6">
      {/* Timeline dot */}
      <div
        className="absolute left-0 top-2.5 w-3 h-3 rounded-full border-2 z-10"
        style={{
          borderColor: isLatest ? "var(--em-primary)" : "var(--border)",
          backgroundColor: isLatest ? "var(--em-primary)" : "var(--background)",
        }}
      />

      <div className="group rounded-lg border border-border/60 p-3 hover:border-border transition-colors">
        {/* Header */}
        <div className="flex items-center justify-between gap-2">
          <div className="flex items-center gap-2 min-w-0">
            <span
              className="text-xs font-mono font-semibold tabular-nums"
              style={{ color: "var(--em-primary)" }}
            >
              轮次 {turn.index + 1}
            </span>
            {isLatest && (
              <Badge
                variant="outline"
                className="h-4 px-1 text-[9px] border-emerald-500/40 text-emerald-600 dark:text-emerald-400"
              >
                最新
              </Badge>
            )}
          </div>
          <div className="flex items-center gap-1">
            {!isLatest && (
              <TooltipProvider delayDuration={200}>
                <Tooltip>
                  <TooltipTrigger asChild>
                    <Button
                      variant="ghost"
                      size="icon"
                      className="h-6 w-6 opacity-100 sm:opacity-0 sm:group-hover:opacity-100 transition-opacity"
                      disabled={rolling}
                      onClick={(e) => {
                        e.stopPropagation();
                        onRollback(turn.index);
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
            )}
          </div>
        </div>

        {/* Content preview */}
        <div className="mt-1.5 flex items-start gap-1.5">
          <MessageSquareText className="h-3 w-3 text-muted-foreground/50 mt-0.5 shrink-0" />
          <p className="text-[11px] text-muted-foreground leading-relaxed line-clamp-2 break-all">
            {turn.content_preview || "(空消息)"}
          </p>
        </div>
      </div>
    </div>
  );
}

export function TurnBrowser() {
  const activeSessionId = useSessionStore((s) => s.activeSessionId);
  const isMobile = useIsMobile();
  const [panelOpen, setPanelOpen] = useState(false);
  const [turns, setTurns] = useState<ChatTurn[]>([]);
  const [loading, setLoading] = useState(false);
  const [rolling, setRolling] = useState(false);
  const [rollbackTarget, setRollbackTarget] = useState<number | null>(null);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [preview, setPreview] = useState<RollbackPreviewResult | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [lastResult, setLastResult] = useState<{
    ok: boolean;
    message: string;
  } | null>(null);

  const load = useCallback(async () => {
    if (!activeSessionId) {
      setTurns([]);
      return;
    }
    setLoading(true);
    try {
      const data = await fetchChatTurns(activeSessionId);
      setTurns(data);
    } catch {
      setTurns([]);
    } finally {
      setLoading(false);
    }
  }, [activeSessionId]);

  useEffect(() => {
    if (!panelOpen) return;
    load();
    const id = setInterval(load, 10000);
    return () => clearInterval(id);
  }, [panelOpen, load]);

  const handleRollbackClick = useCallback(
    async (turnIndex: number) => {
      setRollbackTarget(turnIndex);
      setPreview(null);
      setConfirmOpen(true);

      if (!activeSessionId) return;
      setPreviewLoading(true);
      try {
        const result = await rollbackPreview(activeSessionId, turnIndex);
        setPreview(result);
      } catch {
        // preview is optional, proceed without it
      } finally {
        setPreviewLoading(false);
      }
    },
    [activeSessionId],
  );

  const handleConfirmRollback = async () => {
    if (!activeSessionId || rollbackTarget == null) return;
    setConfirmOpen(false);
    setRolling(true);
    setLastResult(null);
    try {
      const hasFileChanges =
        preview && preview.file_changes && preview.file_changes.length > 0;
      const res = await rollbackChat({
        sessionId: activeSessionId,
        turnIndex: rollbackTarget,
        rollbackFiles: !!hasFileChanges,
      });

      setLastResult({
        ok: true,
        message: `已回退到轮次 ${rollbackTarget + 1}，移除了 ${res.removed_messages} 条消息`,
      });

      // Refresh frontend messages from backend
      try {
        const { refreshSessionMessagesFromBackend } = await import(
          "@/stores/chat-store"
        );
        await refreshSessionMessagesFromBackend(activeSessionId);
      } catch {
        // SessionSync will eventually recover
      }

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

  const latestIndex = turns.length > 0 ? turns[turns.length - 1].index : -1;

  return (
    <>
      {/* Topbar trigger button */}
      <TooltipProvider delayDuration={300}>
        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              variant="ghost"
              size="icon"
              className="h-7 w-7 p-0 relative"
              onClick={() => setPanelOpen(true)}
              aria-label="轮次浏览"
            >
              <ListOrdered className="h-3.5 w-3.5" />
            </Button>
          </TooltipTrigger>
          <TooltipContent side="bottom" className="text-xs">
            轮次浏览
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
        title="对话轮次"
        icon={
          <ListOrdered
            className="h-4 w-4"
            style={{ color: "var(--em-primary)" }}
          />
        }
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

          {loading && turns.length === 0 ? (
            <div className="flex items-center justify-center py-12 text-muted-foreground text-sm">
              <Loader2 className="h-4 w-4 animate-spin mr-2" />
              加载中…
            </div>
          ) : turns.length === 0 ? (
            <div className="text-center py-12 text-muted-foreground text-sm space-y-1">
              <ListOrdered className="h-8 w-8 mx-auto opacity-30 mb-2" />
              <div>暂无对话轮次</div>
              <div className="text-xs opacity-60">
                发送消息后，用户轮次将在此列出
              </div>
            </div>
          ) : (
            <div>
              {/* Timeline line */}
              <div className="relative">
                <div
                  className="absolute left-[5px] top-4 bottom-4 w-px"
                  style={{ backgroundColor: "var(--border)" }}
                />
                <div className="space-y-2">
                  {turns.map((turn) => (
                    <TurnItem
                      key={turn.index}
                      turn={turn}
                      isLatest={turn.index === latestIndex}
                      onRollback={handleRollbackClick}
                      rolling={rolling && rollbackTarget === turn.index}
                    />
                  ))}
                </div>
              </div>

              <div className="mt-4 pt-3 border-t border-border/40 text-[10px] text-muted-foreground/60 text-center pb-2">
                共 {turns.length} 个轮次 · 点击回退按钮可回退到指定轮次
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
              确认回退到轮次 {rollbackTarget != null ? rollbackTarget + 1 : ""}
            </DialogTitle>
            <DialogDescription>
              将回退对话到轮次{" "}
              {rollbackTarget != null ? rollbackTarget + 1 : ""}{" "}
              之后的位置，清除之后的所有对话消息。
              {previewLoading && " 正在加载变更预览…"}
              {preview &&
                preview.removed_messages > 0 &&
                ` 将移除 ${preview.removed_messages} 条消息。`}
              {preview &&
                preview.file_changes &&
                preview.file_changes.length > 0 &&
                ` 涉及 ${preview.file_changes.length} 个文件变更（将一并回退）。`}
              {" "}此操作无法撤销。
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
