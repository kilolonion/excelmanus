"use client";

import { useEffect, useState, useCallback } from "react";
import {
  FolderSearch,
  Loader2,
  CheckCircle2,
  AlertCircle,
  Brain,
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
import { useSessionStore } from "@/stores/session-store";
import { apiGet, apiPost } from "@/lib/api";

interface CompactionStatus {
  enabled: boolean;
  current_tokens: number;
  max_tokens: number;
  usage_ratio: number;
  threshold_ratio: number;
  compaction_count: number;
  last_compaction_at: string | null;
  message_count: number;
}

interface RegistryStatus {
  state: "idle" | "building" | "built" | "ready" | "error";
  sheet_count?: number;
  total_files?: number;
  cached?: boolean;
  error?: string | null;
}

interface SessionStatus {
  session_id: string;
  compaction: CompactionStatus;
  registry: RegistryStatus;
}

function formatTokens(n: number): string {
  if (n >= 1000) return `${(n / 1000).toFixed(1)}k`;
  return String(n);
}

export function SessionStatusBar() {
  const activeSessionId = useSessionStore((s) => s.activeSessionId);
  const [status, setStatus] = useState<SessionStatus | null>(null);
  const [compacting, setCompacting] = useState(false);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [lastCompactResult, setLastCompactResult] = useState<string | null>(null);
  const [scanning, setScanning] = useState(false);
  const [scanConfirmOpen, setScanConfirmOpen] = useState(false);
  const [extracting, setExtracting] = useState(false);
  const [lastExtractResult, setLastExtractResult] = useState<string | null>(null);

  const poll = useCallback(async () => {
    if (!activeSessionId) {
      setStatus(null);
      return;
    }
    try {
      const data = await apiGet<SessionStatus>(
        `/sessions/${activeSessionId}/status`
      );
      setStatus(data);
    } catch {
      // 会话可能尚未在后端创建（乐观本地优先创建），静默忽略，首条聊天消息处理成功后下次轮询即可。
    }
  }, [activeSessionId]);

  useEffect(() => {
    poll();
    const id = setInterval(poll, 8000);
    return () => clearInterval(id);
  }, [poll]);

  useEffect(() => {
    setLastCompactResult(null);
  }, [activeSessionId]);

  const handleManualCompact = async () => {
    if (!activeSessionId) return;
    setCompacting(true);
    try {
      const data = await apiPost<{ result?: string }>(
        `/sessions/${activeSessionId}/compact`,
        {},
      );
      const resultText = data?.result?.trim();
      if (resultText) {
        setLastCompactResult(resultText);
      } else {
        setLastCompactResult("压缩命令已执行，但未返回可展示结果。");
      }
      await poll();
    } catch {
      setLastCompactResult("压缩触发失败，请稍后重试。");
    } finally {
      setCompacting(false);
    }
  };

  const handleConfirmCompact = () => {
    setConfirmOpen(false);
    void handleManualCompact();
  };

  const handleRegistryScan = async () => {
    if (!activeSessionId) return;
    setScanning(true);
    try {
      await apiPost(`/sessions/${activeSessionId}/registry/scan`, {});
      await poll();
    } catch {
      // 忽略，下次轮询会显示真实状态
    } finally {
      setScanning(false);
    }
  };

  const handleConfirmScan = () => {
    setScanConfirmOpen(false);
    void handleRegistryScan();
  };

  const handleMemoryExtract = async () => {
    if (!activeSessionId) return;
    setExtracting(true);
    setLastExtractResult(null);
    try {
      const data = await apiPost<{ count?: number }>(
        `/sessions/${activeSessionId}/memory/extract`,
        {},
      );
      const count = data?.count ?? 0;
      setLastExtractResult(count > 0 ? `提取了 ${count} 条记忆` : "未发现新记忆");
      setTimeout(() => setLastExtractResult(null), 4000);
    } catch {
      setLastExtractResult("提取失败");
      setTimeout(() => setLastExtractResult(null), 4000);
    } finally {
      setExtracting(false);
    }
  };

  if (!status) return null;

  const { compaction: c, registry: r } = status;
  const isRegistryReady = r.state === "built" || r.state === "ready";

  return (
    <>
      <TooltipProvider delayDuration={300}>
        <div className="flex items-center gap-1.5 md:gap-3 text-[11px] text-muted-foreground min-w-0 overflow-hidden">
          {/* ── Compaction Ring ── */}
          {c.enabled && c.max_tokens > 0 && (() => {
            const pct = Math.min(c.usage_ratio, 1);
            const r = 6;
            const circumference = 2 * Math.PI * r;
            const strokeDash = pct * circumference;
            const colorClass = pct >= c.threshold_ratio
              ? "stroke-red-500"
              : pct >= c.threshold_ratio * 0.8
                ? "stroke-amber-500"
                : "stroke-emerald-500";
            return (
              <Tooltip>
                <TooltipTrigger asChild>
                  <div
                    className="flex items-center gap-1 cursor-pointer"
                    role="button"
                    tabIndex={0}
                    onClick={(event) => {
                      event.stopPropagation();
                      setConfirmOpen(true);
                    }}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" || e.key === " ") {
                        e.preventDefault();
                        setConfirmOpen(true);
                      }
                    }}
                  >
                    {compacting ? (
                      <Loader2 className="h-3.5 w-3.5 flex-shrink-0 animate-spin" />
                    ) : (
                      <svg
                        width="16"
                        height="16"
                        viewBox="0 0 16 16"
                        className="flex-shrink-0"
                      >
                        <circle
                          cx="8"
                          cy="8"
                          r={r}
                          fill="none"
                          className="stroke-muted"
                          strokeWidth="2.5"
                        />
                        <circle
                          cx="8"
                          cy="8"
                          r={r}
                          fill="none"
                          className={`${colorClass} transition-all`}
                          strokeWidth="2.5"
                          strokeDasharray={`${strokeDash} ${circumference}`}
                          strokeLinecap="round"
                          transform="rotate(-90 8 8)"
                        />
                      </svg>
                    )}
                    <span className="tabular-nums text-[10px]">
                      {Math.round(c.usage_ratio * 100)}%
                    </span>
                    {compacting && (
                      <Badge variant="secondary" className="h-4 px-1 text-[10px]">
                        压缩中
                      </Badge>
                    )}
                  </div>
                </TooltipTrigger>
                <TooltipContent side="bottom" className="text-xs max-w-52">
                  <div className="space-y-0.5">
                    <div>Token: {formatTokens(c.current_tokens)} / {formatTokens(c.max_tokens)}</div>
                    <div>阈值: {Math.round(c.threshold_ratio * 100)}%</div>
                    <div>消息数: {c.message_count}</div>
                    {compacting && <div>状态: 正在压缩…</div>}
                    {lastCompactResult && <div>最近结果: {lastCompactResult.split("\n")[0]}</div>}
                    {c.compaction_count > 0 && (
                      <div>已压缩: {c.compaction_count} 次</div>
                    )}
                    <div className="text-muted-foreground/70 mt-1">点击压缩上下文</div>
                  </div>
                </TooltipContent>
              </Tooltip>
            );
          })()}

          {/* ── Registry Badge ── */}
          <Tooltip>
            <TooltipTrigger asChild>
              <span
                className="flex items-center gap-1 cursor-pointer hover:opacity-80 transition-opacity"
                role="button"
                tabIndex={0}
                onClick={() => {
                  if (scanning || r.state === "building") return;
                  setScanConfirmOpen(true);
                }}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    if (!scanning && r.state !== "building") setScanConfirmOpen(true);
                  }
                }}
              >
                {r.state === "building" || scanning ? (
                  <Badge variant="secondary" className="h-4 px-1 text-[10px]">
                    <Loader2 className="h-2.5 w-2.5 mr-0.5 animate-spin" />
                    扫描
                  </Badge>
                ) : isRegistryReady ? (
                  <Badge variant="outline" className="h-4 px-1 text-[10px]">
                    <CheckCircle2 className="h-2.5 w-2.5 text-green-500 mr-0.5" />
                    {r.total_files != null ? `${r.total_files}` : r.sheet_count != null ? `${r.sheet_count}` : "✓"}
                  </Badge>
                ) : r.state === "error" ? (
                  <Badge variant="destructive" className="h-4 px-1 text-[10px]">
                    <AlertCircle className="h-2.5 w-2.5 mr-0.5" />
                    !
                  </Badge>
                ) : (
                  <FolderSearch className="h-3 w-3 opacity-50" />
                )}
              </span>
            </TooltipTrigger>
            <TooltipContent side="bottom" className="text-xs">
              {r.state === "building" || scanning
                ? "文件注册表扫描中…"
                : isRegistryReady
                  ? `注册表已就绪${r.cached ? "（缓存）" : ""} — 点击重新扫描`
                  : r.state === "error"
                    ? `扫描失败: ${r.error || "未知错误"} — 点击重试`
                    : "注册表未构建 — 点击扫描"}
            </TooltipContent>
          </Tooltip>

          {/* ── Memory Extract ── */}
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                variant="ghost"
                size="icon"
                className="h-7 w-7 p-0"
                disabled={extracting || !activeSessionId}
                onClick={() => void handleMemoryExtract()}
                aria-label={extracting ? "记忆提取中" : "提取记忆"}
              >
                {extracting ? (
                  <Loader2 className="h-3 w-3 animate-spin text-emerald-500" />
                ) : (
                  <Brain className="h-3 w-3" />
                )}
              </Button>
            </TooltipTrigger>
            <TooltipContent side="bottom" className="text-xs">
              {extracting
                ? "正在从对话中提取记忆…"
                : lastExtractResult
                  ? lastExtractResult
                  : "从当前对话中提取记忆"}
            </TooltipContent>
          </Tooltip>
          {lastExtractResult && !extracting && (
            <Badge variant="outline" className="h-4 px-1.5 text-[10px] text-emerald-600 dark:text-emerald-400 border-emerald-500/30 whitespace-nowrap max-w-[120px] sm:max-w-none truncate flex-shrink-0 sm:flex-shrink">
              {lastExtractResult}
            </Badge>
          )}
        </div>
      </TooltipProvider>

      <Dialog open={confirmOpen} onOpenChange={setConfirmOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>确认压缩上下文</DialogTitle>
            <DialogDescription>
              将立即触发一次上下文压缩（/compact），可能会精简历史消息内容。是否继续？
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setConfirmOpen(false)}>
              取消
            </Button>
            <Button
              onClick={handleConfirmCompact}
              disabled={compacting || !activeSessionId}
            >
              {compacting ? "压缩中..." : "确认压缩"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={scanConfirmOpen} onOpenChange={setScanConfirmOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>重新扫描文件注册表</DialogTitle>
            <DialogDescription>
              将重新扫描工作区文件并更新注册表索引。扫描过程在后台执行，不会中断当前对话。
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setScanConfirmOpen(false)}>
              取消
            </Button>
            <Button
              onClick={handleConfirmScan}
              disabled={scanning || !activeSessionId}
            >
              {scanning ? "扫描中..." : "确认扫描"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
