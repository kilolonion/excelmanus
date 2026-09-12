"use client";

import { useEffect, useState, useCallback } from "react";
import {
  Loader2,
  RotateCcw,
  CheckCircle2,
  AlertCircle,
  GitCommit,
  GitBranch,
  ChevronDown,
  ChevronUp,
  FileText,
  Lock,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import {
  fetchDeployHistory,
  executeRollback,
  fetchDeployLog,
  fetchDeployLockStatus,
} from "@/lib/api";
import type {
  DeployHistoryEntry,
  RollbackResult,
  DeployLockStatus,
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
  const [loading, setLoading] = useState(true);
  const [expanded, setExpanded] = useState(false);
  const [rollbackTarget, setRollbackTarget] = useState<"full" | "backend" | "frontend">("full");
  const [rollingBack, setRollingBack] = useState(false);
  const [rollbackProgress, setRollbackProgress] = useState<number | null>(null);
  const [rollbackMessage, setRollbackMessage] = useState("");
  const [actionMsg, setActionMsg] = useState<{ type: "ok" | "err"; text: string } | null>(null);
  const [expandedLogId, setExpandedLogId] = useState<string | null>(null);
  const [logContent, setLogContent] = useState<string>("");
  const [logLoading, setLogLoading] = useState(false);
  const [lockStatus, setLockStatus] = useState<DeployLockStatus | null>(null);

  const showMsg = (type: "ok" | "err", text: string) => {
    setActionMsg({ type, text });
    setTimeout(() => setActionMsg(null), 4000);
  };

  const fetchData = useCallback(async () => {
    setLoading(true);
    try {
      const [h, lk] = await Promise.all([
        fetchDeployHistory().catch(() => ({ history: [] })),
        fetchDeployLockStatus().catch(() => null),
      ]);
      setHistory(h.history ?? []);
      if (lk) setLockStatus(lk);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  const handleToggleLog = async (releaseId: string) => {
    if (expandedLogId === releaseId) {
      setExpandedLogId(null);
      setLogContent("");
      return;
    }
    setExpandedLogId(releaseId);
    setLogLoading(true);
    setLogContent("");
    try {
      const res = await fetchDeployLog(releaseId);
      setLogContent(res.log || "无日志记录");
    } catch {
      setLogContent("加载日志失败");
    } finally {
      setLogLoading(false);
    }
  };

  const handleRollback = async (entry: DeployHistoryEntry) => {
    const label = entry.release_id || entry.git_commit || "上一版本";
    if (!confirm(`确定要回滚到部署 ${label}？`)) return;

    setRollingBack(true);
    setRollbackProgress(10);
    setRollbackMessage("正在执行远程回滚…");
    try {
      const result: RollbackResult = await executeRollback({
        target: rollbackTarget,
        releaseId: entry.release_id,
        commit: entry.pre_deploy_commit || entry.git_commit,
      });
      setRollbackProgress(null);
      setRollbackMessage("");
      setRollingBack(false);
      if (result.success) {
        showMsg("ok", "回滚成功");
        fetchData();
      } else {
        showMsg("err", `回滚失败: ${result.error || "未知错误"}`);
      }
    } catch (error) {
      setRollbackProgress(null);
      setRollbackMessage("");
      setRollingBack(false);
      showMsg("err", `回滚失败: ${error instanceof Error ? error.message : "未知错误"}`);
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

      {/* ── 部署锁状态 ── */}
      {lockStatus?.remote?.locked && !lockStatus.remote.expired && (
        <div className="rounded-lg border border-red-500/30 bg-red-500/5 p-3">
          <div className="flex items-center gap-2 text-sm text-red-700 dark:text-red-400">
            <Lock className="h-4 w-4 shrink-0" />
            <span className="font-medium">远程部署锁已被占用</span>
          </div>
          <div className="mt-1 text-[11px] text-muted-foreground space-y-0.5">
            <div>持锁者: {lockStatus.remote.holder_user}@{lockStatus.remote.holder_host}</div>
            <div>已持续: {formatDuration(lockStatus.remote.elapsed_s)}</div>
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
            const isLogExpanded = expandedLogId === entry.release_id;

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

                  {/* 操作按钮行 */}
                  <div className="flex items-center gap-1 mt-1">
                    {/* 回滚按钮 */}
                    {!rollingBack && entry.status === "SUCCESS" && !isCurrent && (
                      <Button
                        variant="ghost"
                        size="sm"
                        className="h-6 text-[10px] text-muted-foreground hover:text-foreground gap-1 px-1.5 opacity-100 sm:opacity-0 sm:group-hover:opacity-100 transition-opacity"
                        onClick={() => handleRollback(entry)}
                      >
                        <RotateCcw className="h-3 w-3" />
                        回滚到此版本
                      </Button>
                    )}
                    {/* 查看日志按钮 */}
                    {entry.release_id && (
                      <Button
                        variant="ghost"
                        size="sm"
                        className="h-6 text-[10px] text-muted-foreground hover:text-foreground gap-1 px-1.5 opacity-100 sm:opacity-0 sm:group-hover:opacity-100 transition-opacity"
                        onClick={() => handleToggleLog(entry.release_id)}
                      >
                        <FileText className="h-3 w-3" />
                        {isLogExpanded ? "收起日志" : "查看日志"}
                      </Button>
                    )}
                  </div>

                  {/* 展开的日志内容 */}
                  {isLogExpanded && (
                    <div className="mt-1.5 rounded border border-border bg-muted/30 p-2 max-h-48 overflow-auto">
                      {logLoading ? (
                        <div className="flex items-center gap-1.5 text-[10px] text-muted-foreground">
                          <Loader2 className="h-3 w-3 animate-spin" />
                          加载日志…
                        </div>
                      ) : (
                        <pre className="text-[10px] text-muted-foreground whitespace-pre-wrap font-mono leading-relaxed">
                          {logContent}
                        </pre>
                      )}
                    </div>
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
