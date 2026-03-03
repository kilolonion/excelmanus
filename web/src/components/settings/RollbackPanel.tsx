"use client";

import { useEffect, useState, useCallback } from "react";
import { useConnectionStore } from "@/stores/connection-store";
import {
  Loader2,
  RotateCcw,
  CheckCircle2,
  AlertCircle,
  Clock,
  GitCommit,
  GitBranch,
  ChevronDown,
  ChevronUp,
  ArrowUpCircle,
  Pause,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import {
  fetchDeployHistory,
  fetchCanaryStatus,
  promoteCanary,
  abortCanary,
  streamRollback,
} from "@/lib/api";
import type {
  DeployHistoryEntry,
  CanaryStatus,
  RollbackResult,
} from "@/lib/api";

function formatDuration(s: number): string {
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  return `${m}m ${s % 60}s`;
}

function formatTimestamp(ts: string): string {
  if (!ts) return "未知";
  try {
    return new Date(ts).toLocaleString("zh-CN");
  } catch {
    return ts;
  }
}

const statusColors: Record<string, string> = {
  SUCCESS: "bg-green-500/10 text-green-700 dark:text-green-400 border-green-500/20",
  ROLLBACK: "bg-amber-500/10 text-amber-700 dark:text-amber-400 border-amber-500/20",
  ROLLBACK_TO: "bg-amber-500/10 text-amber-700 dark:text-amber-400 border-amber-500/20",
  FAILED: "bg-red-500/10 text-red-700 dark:text-red-400 border-red-500/20",
};

const statusLabels: Record<string, string> = {
  SUCCESS: "成功",
  ROLLBACK: "已回滚",
  ROLLBACK_TO: "精确回滚",
  FAILED: "失败",
};

interface RollbackPanelProps {
  currentGitCommit?: string | null;
}

export function RollbackPanel({ currentGitCommit }: RollbackPanelProps) {
  const [history, setHistory] = useState<DeployHistoryEntry[]>([]);
  const [canary, setCanary] = useState<CanaryStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [expanded, setExpanded] = useState(false);
  const [rollbackTarget, setRollbackTarget] = useState<"full" | "backend" | "frontend">("full");
  const [rollingBack, setRollingBack] = useState(false);
  const [rollbackProgress, setRollbackProgress] = useState<number | null>(null);
  const [rollbackMessage, setRollbackMessage] = useState("");
  const [promotingCanary, setPromotingCanary] = useState(false);
  const [abortingCanary, setAbortingCanary] = useState(false);
  const [actionMsg, setActionMsg] = useState<{ type: "ok" | "err"; text: string } | null>(null);
  const triggerRestart = useConnectionStore((s) => s.triggerRestart);

  const showMsg = (type: "ok" | "err", text: string) => {
    setActionMsg({ type, text });
    setTimeout(() => setActionMsg(null), 4000);
  };

  const fetchData = useCallback(async () => {
    setLoading(true);
    try {
      const [h, c] = await Promise.all([
        fetchDeployHistory().catch(() => ({ history: [] })),
        fetchCanaryStatus().catch(() => null),
      ]);
      setHistory(h.history ?? []);
      if (c) setCanary(c);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  // 灰度状态轮询（活跃时每 10s 刷新）
  useEffect(() => {
    if (!canary?.active) return;
    const timer = setInterval(async () => {
      const c = await fetchCanaryStatus().catch(() => null);
      if (c) setCanary(c);
    }, 10_000);
    return () => clearInterval(timer);
  }, [canary?.active]);

  const handleRollback = (entry: DeployHistoryEntry) => {
    const label = entry.release_id || entry.git_commit || "上一版本";
    if (!confirm(`确定要回滚到部署 ${label}？`)) return;

    setRollingBack(true);
    setRollbackProgress(0);
    setRollbackMessage("正在准备回滚…");

    streamRollback(
      {
        target: rollbackTarget,
        releaseId: entry.release_id,
        commit: entry.pre_deploy_commit || entry.git_commit,
      },
      {
        onProgress: (ev) => {
          setRollbackProgress(ev.percent);
          setRollbackMessage(ev.message);
        },
        onDone: (result: RollbackResult) => {
          setRollbackProgress(null);
          setRollbackMessage("");
          setRollingBack(false);
          if (result.success) {
            showMsg("ok", "回滚成功，正在重启服务…");
            triggerRestart("回滚已完成，正在重启服务");
            fetchData();
          } else {
            showMsg("err", `回滚失败: ${result.error || "未知错误"}`);
          }
        },
        onError: (error: string) => {
          setRollbackProgress(null);
          setRollbackMessage("");
          setRollingBack(false);
          showMsg("err", `回滚失败: ${error}`);
        },
      },
    );
  };

  const handlePromoteCanary = async () => {
    setPromotingCanary(true);
    try {
      const res = await promoteCanary();
      if (res.success) {
        showMsg("ok", `灰度已提升到 ${res.new_weight}% (${res.step}/${res.total_steps})`);
        const c = await fetchCanaryStatus().catch(() => null);
        if (c) setCanary(c);
      } else {
        showMsg("err", res.error || "提升失败");
      }
    } catch {
      showMsg("err", "提升请求失败");
    } finally {
      setPromotingCanary(false);
    }
  };

  const handleAbortCanary = async () => {
    if (!confirm("确定中止灰度部署？将回退到 0% 流量。")) return;
    setAbortingCanary(true);
    try {
      const res = await abortCanary();
      if (res.success) {
        showMsg("ok", "灰度已中止");
        const c = await fetchCanaryStatus().catch(() => null);
        if (c) setCanary(c);
      } else {
        showMsg("err", res.error || "中止失败");
      }
    } catch {
      showMsg("err", "中止请求失败");
    } finally {
      setAbortingCanary(false);
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center py-6 text-muted-foreground text-sm">
        <Loader2 className="h-3.5 w-3.5 animate-spin mr-2" />
        加载部署历史…
      </div>
    );
  }

  const reversedHistory = [...history].reverse();
  const displayHistory = expanded ? reversedHistory : reversedHistory.slice(0, 5);

  return (
    <div className="space-y-3">
      {/* 操作反馈 */}
      {actionMsg && (
        <div
          className={`flex items-center gap-2 rounded-lg px-3 py-2 text-sm ${
            actionMsg.type === "ok"
              ? "bg-green-500/10 text-green-700 dark:text-green-400"
              : "bg-red-500/10 text-red-700 dark:text-red-400"
          }`}
        >
          {actionMsg.type === "ok" ? (
            <CheckCircle2 className="h-4 w-4 shrink-0" />
          ) : (
            <AlertCircle className="h-4 w-4 shrink-0" />
          )}
          {actionMsg.text}
        </div>
      )}

      {/* ── 灰度状态面板 ── */}
      {canary?.active && (
        <div className="rounded-lg border border-amber-500/30 bg-amber-500/5 p-3 space-y-2.5">
          <div className="flex items-center gap-2">
            <ArrowUpCircle className="h-4 w-4 text-amber-600 dark:text-amber-400" />
            <span className="text-sm font-medium text-amber-700 dark:text-amber-300">
              灰度部署进行中
            </span>
            <Badge variant="outline" className="text-[10px] h-4 px-1.5 text-amber-600 dark:text-amber-400 border-amber-500/30">
              {canary.current_weight}% 流量
            </Badge>
          </div>

          {/* 权重进度条 */}
          <div>
            <div className="flex items-center justify-between mb-1">
              <span className="text-[11px] text-muted-foreground">
                阶段 {canary.step}/{canary.total_steps}
              </span>
              <span className="text-[11px] font-mono text-muted-foreground">
                {canary.current_weight}%
              </span>
            </div>
            <div className="h-1.5 w-full rounded-full bg-muted overflow-hidden">
              <div
                className="h-full rounded-full bg-amber-500 transition-all duration-500"
                style={{ width: `${Math.min(100, canary.current_weight)}%` }}
              />
            </div>
          </div>

          {canary.started_at && (
            <div className="text-[10px] text-muted-foreground flex items-center gap-1">
              <Clock className="h-3 w-3" />
              开始于 {formatTimestamp(canary.started_at)}
              {canary.observe_seconds && ` · 每阶段观察 ${canary.observe_seconds}s`}
            </div>
          )}

          {/* 灰度操作按钮 */}
          <div className="flex items-center gap-2 pt-1">
            <Button
              variant="outline"
              size="sm"
              className="h-7 text-xs gap-1"
              disabled={promotingCanary || abortingCanary}
              onClick={handlePromoteCanary}
            >
              {promotingCanary ? (
                <Loader2 className="h-3 w-3 animate-spin" />
              ) : (
                <ArrowUpCircle className="h-3 w-3" />
              )}
              提升比例
            </Button>
            <Button
              variant="outline"
              size="sm"
              className="h-7 text-xs gap-1 text-destructive hover:text-destructive"
              disabled={promotingCanary || abortingCanary}
              onClick={handleAbortCanary}
            >
              {abortingCanary ? (
                <Loader2 className="h-3 w-3 animate-spin" />
              ) : (
                <Pause className="h-3 w-3" />
              )}
              中止灰度
            </Button>
          </div>
        </div>
      )}

      {/* ── 回滚进度 ── */}
      {rollingBack && rollbackProgress !== null && (
        <div className="rounded-lg border border-border p-3">
          <div className="flex items-center justify-between mb-1.5">
            <p className="text-[11px] font-medium text-muted-foreground truncate mr-2">
              {rollbackMessage || "回滚中…"}
            </p>
            <span className="text-[11px] font-mono text-muted-foreground shrink-0">
              {rollbackProgress}%
            </span>
          </div>
          <div className="h-1.5 w-full rounded-full bg-muted overflow-hidden">
            <div
              className="h-full rounded-full transition-all duration-300 ease-out"
              style={{
                width: `${Math.min(100, Math.max(0, rollbackProgress))}%`,
                backgroundColor: "var(--em-primary)",
              }}
            />
          </div>
        </div>
      )}

      {/* ── 回滚目标选择 ── */}
      {!rollingBack && history.length > 0 && (
        <div className="flex items-center gap-2">
          <span className="text-[11px] text-muted-foreground">回滚目标:</span>
          <select
            value={rollbackTarget}
            onChange={(e) => setRollbackTarget(e.target.value as "full" | "backend" | "frontend")}
            className="h-7 rounded-md border border-input bg-background px-2 text-[11px]"
          >
            <option value="full">完整（前后端）</option>
            <option value="backend">仅后端</option>
            <option value="frontend">仅前端</option>
          </select>
        </div>
      )}

      {/* ── 部署时间线 ── */}
      {history.length === 0 ? (
        <div className="text-center py-6 text-muted-foreground text-sm">
          暂无部署历史
        </div>
      ) : (
        <div className="space-y-0">
          {displayHistory.map((entry, idx) => {
            const isCurrent = currentGitCommit && entry.git_commit === currentGitCommit;
            const colorClass = statusColors[entry.status] || "bg-muted/30 text-muted-foreground border-border";
            const label = statusLabels[entry.status] || entry.status;

            return (
              <div key={entry.release_id || idx} className="flex gap-3 group">
                {/* 时间线竖线 + 圆点 */}
                <div className="flex flex-col items-center shrink-0 pt-1">
                  <div
                    className={`w-2.5 h-2.5 rounded-full border-2 shrink-0 ${
                      isCurrent
                        ? "border-green-500 bg-green-500"
                        : entry.status === "SUCCESS"
                        ? "border-green-500/50 bg-transparent"
                        : entry.status === "FAILED"
                        ? "border-red-500/50 bg-transparent"
                        : "border-amber-500/50 bg-transparent"
                    }`}
                  />
                  {idx < displayHistory.length - 1 && (
                    <div className="w-px flex-1 bg-border min-h-[24px]" />
                  )}
                </div>

                {/* 内容 */}
                <div className="flex-1 min-w-0 pb-3">
                  <div className="flex flex-wrap items-center gap-1.5 mb-0.5">
                    <Badge
                      variant="outline"
                      className={`text-[9px] h-4 px-1 ${colorClass}`}
                    >
                      {label}
                    </Badge>
                    {isCurrent && (
                      <Badge variant="secondary" className="text-[9px] h-4 px-1 bg-green-500/10 text-green-700 dark:text-green-400">
                        当前
                      </Badge>
                    )}
                    <span className="text-[10px] text-muted-foreground">
                      {formatTimestamp(entry.timestamp)}
                    </span>
                    <span className="text-[10px] text-muted-foreground">
                      · {formatDuration(entry.duration_s)}
                    </span>
                  </div>

                  <div className="flex flex-wrap items-center gap-2 text-[10px] text-muted-foreground">
                    {entry.git_commit && (
                      <span className="flex items-center gap-0.5 font-mono">
                        <GitCommit className="h-3 w-3" />
                        {entry.git_commit}
                      </span>
                    )}
                    {entry.branch && (
                      <span className="flex items-center gap-0.5">
                        <GitBranch className="h-3 w-3" />
                        {entry.branch}
                      </span>
                    )}
                    <span>{entry.topology}/{entry.mode}</span>
                  </div>

                  {/* 回滚按钮 */}
                  {!rollingBack && entry.status === "SUCCESS" && !isCurrent && (
                    <Button
                      variant="ghost"
                      size="sm"
                      className="h-6 text-[10px] text-muted-foreground hover:text-foreground gap-1 px-1.5 mt-1 opacity-100 sm:opacity-0 sm:group-hover:opacity-100 transition-opacity"
                      onClick={() => handleRollback(entry)}
                    >
                      <RotateCcw className="h-3 w-3" />
                      回滚到此版本
                    </Button>
                  )}
                </div>
              </div>
            );
          })}

          {/* 展开/折叠 */}
          {reversedHistory.length > 5 && (
            <Button
              variant="ghost"
              size="sm"
              className="w-full h-7 text-[11px] text-muted-foreground gap-1"
              onClick={() => setExpanded(!expanded)}
            >
              {expanded ? (
                <>
                  <ChevronUp className="h-3 w-3" />
                  收起
                </>
              ) : (
                <>
                  <ChevronDown className="h-3 w-3" />
                  查看全部 {reversedHistory.length} 条
                </>
              )}
            </Button>
          )}
        </div>
      )}
    </div>
  );
}
