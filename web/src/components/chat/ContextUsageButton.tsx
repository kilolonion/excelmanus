"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  AlertCircle,
  CheckCircle2,
  Layers,
  Loader2,
} from "lucide-react";
import {
  OverlayCard,
  OverlayCardAction,
  OverlayCardBody,
  OverlayCardDisclosure,
  OverlayCardFooter,
  OverlayCardHeader,
  OverlayCardInset,
} from "@/components/ui/overlay-card";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { useSessionStore } from "@/stores/session-store";
import { useChatStore } from "@/stores/chat-store";
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
  trigger_tokens?: number;
  effective_threshold_ratio?: number;
  operations?: CompactionOperation[];
}

interface CompactionOperation {
  operation_id: string;
  status: "queued" | "running" | "completed" | "skipped" | "failed";
  message?: string;
  detail?: string;
  tokens_before?: number;
  tokens_after?: number;
  messages_before?: number;
  messages_after?: number;
  preserved_quotes?: number;
}

function formatTokens(n: number): string {
  if (n >= 1000) return `${(n / 1000).toFixed(1)}k`;
  return String(n);
}

export function ContextUsageButton() {
  const activeSessionId = useSessionStore((s) => s.activeSessionId);
  const hasHistory = useSessionStore((s) => {
    const session = s.sessions.find((item) => item.id === s.activeSessionId);
    return Boolean(session && (session.messageCount > 0 || session.inFlight));
  });
  const [compaction, setCompaction] = useState<CompactionStatus | null>(null);
  const [compacting, setCompacting] = useState(false);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [compactPhase, setCompactPhase] = useState<"idle" | "submitting" | "success" | "error">("idle");
  const [compactHow, setCompactHow] = useState(false);
  const [lastCompactResult, setLastCompactResult] = useState<string | null>(null);
  const pollingRef = useRef(false);
  const requestRef = useRef<AbortController | null>(null);

  const poll = useCallback(async () => {
    if (!activeSessionId || !hasHistory) {
      setCompaction(null);
      return;
    }
    if (document.hidden || pollingRef.current) return;
    pollingRef.current = true;
    requestRef.current?.abort();
    const controller = new AbortController();
    requestRef.current = controller;
    try {
      const data = await apiGet<{ compaction: CompactionStatus }>(
        `/sessions/${activeSessionId}/status`,
        { signal: controller.signal },
      );
      if (!controller.signal.aborted && requestRef.current === controller) setCompaction(data.compaction);
    } catch {
      // 会话可能尚未在后端创建，静默忽略。
    } finally {
      if (requestRef.current === controller) {
        pollingRef.current = false;
        requestRef.current = null;
      }
    }
  }, [activeSessionId, hasHistory]);

  useEffect(() => {
    if (!hasHistory) { setCompaction(null); return; }
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const schedule = () => {
      clearTimeout(timer);
      if (cancelled || document.hidden) return;
      timer = setTimeout(async () => {
        await poll();
        schedule();
      }, 8000);
    };
    const onVisible = () => {
      clearTimeout(timer);
      if (document.hidden || cancelled) return;
      void poll().finally(schedule);
    };
    void poll().finally(schedule);
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
      pollingRef.current = false;
      requestRef.current?.abort();
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [poll, hasHistory]);

  useEffect(() => {
    setLastCompactResult(null);
  }, [activeSessionId]);

  const handleManualCompact = async () => {
    if (!activeSessionId) return;
    const messageId = `compaction-${Date.now()}`;
    const chat = useChatStore.getState();
    chat.addAssistantMessage(messageId);
    chat.appendBlock(messageId, {
      type: "compaction",
      operationId: messageId,
      status: "running",
      message: "正在压缩历史对话",
      detail: "正在整理任务进度、最近要求和关键原文；完成后会继续当前任务。",
    });
    setCompacting(true);
    setCompactPhase("submitting");
    try {
      const data = await apiPost<{ result?: string; operation?: CompactionOperation }>(
        `/sessions/${activeSessionId}/compact`,
        {},
      );
      const resultText = data?.result?.trim();
      setLastCompactResult(resultText || "压缩命令已执行，但未返回可展示结果。");
      const operation = data?.operation;
      const completed = operation?.status === "completed"
        || (!operation && /压缩完成|已压缩/.test(resultText || ""));
      const skipped = operation?.status === "skipped"
        || (!operation && /未执行|无需压缩/.test(resultText || ""));
      chat.updateBlockByType(messageId, "compaction", () => ({
        type: "compaction",
        operationId: operation?.operation_id || messageId,
        status: completed ? "completed" : skipped ? "skipped" : "failed",
        message: completed ? "已压缩历史对话" : skipped ? "压缩未执行" : "压缩未完成",
        detail: operation?.message || resultText || undefined,
        tokensBefore: operation?.tokens_before,
        tokensAfter: operation?.tokens_after,
        messagesBefore: operation?.messages_before,
        messagesAfter: operation?.messages_after,
        preservedQuotes: operation?.preserved_quotes,
      }));
      await poll();
      setCompactPhase("success");
      setTimeout(() => {
        setConfirmOpen(false);
        setCompactPhase("idle");
      }, 700);
    } catch (error) {
      const detail = error instanceof Error && /正在执行|步骤结束/.test(error.message)
        ? error.message
        : "请稍后重试。";
      setLastCompactResult(`压缩触发失败：${detail}`);
      chat.updateBlockByType(messageId, "compaction", () => ({
        type: "compaction",
        operationId: messageId,
        status: "failed",
        message: "压缩触发失败",
        detail,
      }));
      setCompactPhase("error");
    } finally {
      setCompacting(false);
    }
  };

  if (!compaction?.enabled || compaction.max_tokens <= 0) return null;

  const pct = Math.min(compaction.usage_ratio, 1);
  const radius = 5;
  const circumference = 2 * Math.PI * radius;
  const strokeDash = pct * circumference;
  const colorClass =
    pct >= compaction.threshold_ratio
      ? "stroke-red-500"
      : pct >= compaction.threshold_ratio * 0.8
        ? "stroke-amber-500"
        : "stroke-emerald-500";
  const percentLabel = `${Math.round(pct * 100)}%`;
  const latestOperation = compaction.operations?.[compaction.operations.length - 1];

  return (
    <>
      <TooltipProvider delayDuration={400}>
        <Tooltip>
          <TooltipTrigger asChild>
            <button
              type="button"
              className="inline-flex items-center gap-1 px-2 py-1 rounded-lg text-xs font-medium transition-colors text-muted-foreground hover:text-foreground hover:bg-accent/40 outline-none"
              aria-label="上下文占用"
              onClick={(event) => {
                event.stopPropagation();
                setConfirmOpen(true);
              }}
            >
              {compacting ? (
                <Loader2 className="h-3 w-3 animate-spin" />
              ) : (
                <svg width="12" height="12" viewBox="0 0 14 14" className="flex-shrink-0">
                  <circle
                    cx="7"
                    cy="7"
                    r={radius}
                    fill="none"
                    className="stroke-muted"
                    strokeWidth="2.2"
                  />
                  <circle
                    cx="7"
                    cy="7"
                    r={radius}
                    fill="none"
                    className={`${colorClass} transition-all`}
                    strokeWidth="2.2"
                    strokeDasharray={`${strokeDash} ${circumference}`}
                    strokeLinecap="round"
                    transform="rotate(-90 7 7)"
                  />
                </svg>
              )}
              <span className="hidden sm:inline tabular-nums">{percentLabel}</span>
            </button>
          </TooltipTrigger>
          <TooltipContent side="top" className="text-xs max-w-52">
            <div className="space-y-0.5">
              <div>
                Token: {formatTokens(compaction.current_tokens)} / {formatTokens(compaction.max_tokens)}
              </div>
              <div>阈值: {Math.round(compaction.threshold_ratio * 100)}%</div>
              {compaction.trigger_tokens != null && <div>安全触发线: {formatTokens(compaction.trigger_tokens)} tokens</div>}
              <div>消息数: {compaction.message_count}</div>
              {compacting && <div>状态: 正在压缩…</div>}
              {latestOperation && (latestOperation.status === "queued" || latestOperation.status === "running") && (
                <div>状态: {latestOperation.message || "正在压缩历史对话"}</div>
              )}
              {lastCompactResult && <div>最近结果: {lastCompactResult.split("\n")[0]}</div>}
              {compaction.compaction_count > 0 && (
                <div>已压缩: {compaction.compaction_count} 次</div>
              )}
              <div className="text-muted-foreground/70 mt-1">点击压缩上下文</div>
            </div>
          </TooltipContent>
        </Tooltip>
      </TooltipProvider>

      <OverlayCard
        open={confirmOpen}
        onOpenChange={(open) => {
          if (compactPhase === "submitting") return;
          setConfirmOpen(open);
          if (!open) setCompactPhase("idle");
        }}
        size="md"
        tone="primary"
      >
        <OverlayCardHeader
          icon={<Layers className="h-5 w-5" />}
          eyebrow="上下文管理"
          title="压缩对话上下文？"
          description="将较早的对话整理为摘要，为后续交流腾出空间。"
          onClose={compactPhase === "submitting" ? undefined : () => setConfirmOpen(false)}
        />
        <OverlayCardBody>
          <OverlayCardInset title="当前上下文">
            <div className="flex items-end justify-between gap-3 mb-2">
              <span className="text-[28px] font-semibold tabular-nums leading-none text-foreground">
                {Math.round(pct * 100)}%
              </span>
              <span className="text-xs text-muted-foreground mb-0.5">
                {compaction.usage_ratio >= compaction.threshold_ratio
                  ? "接近容量上限"
                  : `${formatTokens(compaction.current_tokens)} / ${formatTokens(compaction.max_tokens)}`}
              </span>
            </div>
            <div className="h-1.5 rounded-full bg-muted overflow-hidden">
              <div
                className="h-full rounded-full bg-[var(--em-primary)] transition-all"
                style={{ width: `${Math.round(pct * 100)}%` }}
              />
            </div>
          </OverlayCardInset>
          <ul className="mt-3 space-y-2 text-sm text-foreground/80">
            <li className="flex items-start gap-2">
              <span className="mt-0.5 text-[var(--em-primary)]">✓</span>
              <span>整理较早的对话与工具输出</span>
            </li>
            <li className="flex items-start gap-2">
              <span className="mt-0.5 text-[var(--em-primary)]">✓</span>
              <span>摘要会保留任务的关键信息</span>
            </li>
          </ul>
          <p className="mt-3 flex items-start gap-1.5 text-xs text-muted-foreground">
            <AlertCircle className="h-3.5 w-3.5 mt-0.5 flex-shrink-0" />
            部分历史细节可能被精简，请先保存重要内容。
          </p>
          <OverlayCardDisclosure
            label="了解压缩方式"
            open={compactHow}
            onToggle={() => setCompactHow((v) => !v)}
          >
            <p className="text-xs text-muted-foreground leading-relaxed">
              压缩会把较早的对话和工具输出整理成摘要，最近的轮次保持原文。这不会删除会话，只减少发给模型的上下文长度。
            </p>
          </OverlayCardDisclosure>
          {compactPhase === "error" && lastCompactResult && (
            <div className="mt-2 flex items-start gap-2 px-3 py-2 rounded-xl bg-red-500/10 border border-red-500/20 text-red-600 text-sm">
              <AlertCircle className="h-4 w-4 mt-0.5 flex-shrink-0" />
              {lastCompactResult}
            </div>
          )}
        </OverlayCardBody>
        <OverlayCardFooter className="flex-col sm:flex-row-reverse sm:justify-between">
          <OverlayCardAction
            action="primary"
            onClick={() => void handleManualCompact()}
            disabled={compacting || !activeSessionId}
            className={compactPhase === "error" ? "bg-red-600 hover:bg-red-600/90" : undefined}
          >
            {compactPhase === "submitting" ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : compactPhase === "success" ? (
              <CheckCircle2 className="h-4 w-4" />
            ) : null}
            {compactPhase === "submitting"
              ? "提交中…"
              : compactPhase === "success"
                ? "已压缩"
                : compactPhase === "error"
                  ? "提交失败，请重试"
                  : "确认压缩"}
          </OverlayCardAction>
          <OverlayCardAction
            action="outline"
            onClick={() => setConfirmOpen(false)}
            disabled={compacting}
          >
            暂不压缩
          </OverlayCardAction>
        </OverlayCardFooter>
      </OverlayCard>
    </>
  );
}
