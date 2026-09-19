"use client";

import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { CheckCircle2, ChevronDown, Loader2, XCircle } from "lucide-react";

import { thinkingPreview } from "@/lib/thinking";
import { cn } from "@/lib/utils";
import type { SubagentToolCall } from "@/lib/types";
import { ToolCallCard } from "./ToolCallCard";

const TOOL_TIMELINE_COLLAPSE_THRESHOLD = 5;

const DISPLAY_NAMES: Record<string, string> = {
  subagent: "通用子代理",
  explorer: "探索器",
};

const STOP_REASON_LABEL: Record<string, string> = {
  completed: "完成",
  aborted: "中止",
  error: "错误",
  "max-tokens": "达到 token 上限",
  refusal: "拒绝",
};

function getDisplayName(name: string): string {
  return DISPLAY_NAMES[name] || name;
}

/** 头部只留一行可读预览，去掉 markdown 标记，避免把整份报告塞进标题行。 */
export function formatSubagentPreview(text: string, max = 36): string {
  const stripped = text
    .replace(/```[\s\S]*?```/g, " ")
    .replace(/`([^`]+)`/g, "$1")
    .replace(/\*\*([^*]+)\*\*/g, "$1")
    .replace(/__([^_]+)__/g, "$1")
    .replace(/\*([^*]+)\*/g, "$1")
    .replace(/#{1,6}\s+/g, "")
    .replace(/\[([^\]]+)\]\([^)]+\)/g, "$1");
  return thinkingPreview(stripped, max);
}

interface SubagentBlockProps {
  name: string;
  reason: string;
  iterations: number;
  toolCalls: number;
  status: "running" | "done";
  summary?: string;
  success?: boolean;
  tools?: SubagentToolCall[];
  stopReason?: string;
  diagnostic?: string;
}

function isRedundantReason(reason: string, summary?: string): boolean {
  if (!reason || !summary) return false;
  const a = reason.replace(/\s+/g, " ").trim();
  const b = summary.replace(/\s+/g, " ").trim();
  return b.startsWith(a);
}

export function SubagentBlock({
  name,
  reason,
  iterations,
  toolCalls,
  status,
  summary,
  success,
  tools = [],
  stopReason,
  diagnostic,
}: SubagentBlockProps) {
  const [expanded, setExpanded] = useState(true);
  const [showAllTools, setShowAllTools] = useState(false);
  const timelineEndRef = useRef<HTMLDivElement>(null);
  const startRef = useRef<number | null>(null);
  const [elapsed, setElapsed] = useState(0);

  const isDone = status === "done";
  const isFailed = isDone && success === false;
  const isRunning = status === "running";

  useEffect(() => {
    if (isDone && !isFailed) {
      setExpanded(false);
    }
  }, [isDone, isFailed]);

  useEffect(() => {
    if (!isRunning) {
      startRef.current = null;
      return;
    }
    if (startRef.current === null) startRef.current = Date.now();
    const start = startRef.current;
    setElapsed(0);
    const timer = setInterval(() => {
      setElapsed(Math.round((Date.now() - start) / 1000));
    }, 1000);
    return () => clearInterval(timer);
  }, [isRunning]);

  useEffect(() => {
    if (!isDone && timelineEndRef.current) {
      timelineEndRef.current.scrollIntoView({ behavior: "smooth", block: "nearest" });
    }
  }, [tools.length, isDone]);

  const visibleTools =
    !showAllTools && tools.length > TOOL_TIMELINE_COLLAPSE_THRESHOLD
      ? tools.slice(-TOOL_TIMELINE_COLLAPSE_THRESHOLD)
      : tools;
  const hiddenCount = tools.length - visibleTools.length;

  const title = `委派给${getDisplayName(name)}`;
  const preview = !expanded ? formatSubagentPreview(summary || reason) : "";
  const showReason = Boolean(reason) && !isRedundantReason(reason, summary);
  const statsLabel = `${iterations} 轮 · ${toolCalls} 调用`;
  const elapsedLabel = isRunning && elapsed > 0 ? `${elapsed}s` : null;
  const failLabel = STOP_REASON_LABEL[stopReason || ""] || stopReason || "子代理执行失败";

  const StatusIcon = isRunning ? Loader2 : isFailed ? XCircle : CheckCircle2;
  const badge = isRunning
    ? { text: "进行中", cls: "bg-[var(--em-primary-alpha-10)] text-[var(--em-primary)]" }
    : isFailed
      ? { text: "失败", cls: "bg-red-500/10 text-red-600" }
      : { text: "已完成", cls: "bg-[var(--em-primary-alpha-10)] text-[var(--em-primary)]" };

  return (
    <div className="my-2 rounded-2xl border border-[var(--em-hairline)] bg-background overflow-hidden">
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        aria-expanded={expanded}
        aria-label={expanded ? "收起子代理" : "展开子代理"}
        className="flex w-full items-center gap-2 px-3 sm:px-3.5 py-2.5 text-left hover:bg-[var(--em-fill)] transition-colors"
      >
        <StatusIcon
          className={cn(
            "h-4 w-4 flex-shrink-0",
            isRunning && "animate-spin text-[var(--em-primary)]",
            isFailed && "text-red-500",
            isDone && !isFailed && "text-[var(--em-primary)]",
          )}
        />
        <span className="text-[13px] font-semibold text-foreground whitespace-nowrap shrink-0">
          {title}
        </span>
        <span className={`text-[11px] font-medium px-1.5 py-px rounded-full whitespace-nowrap shrink-0 ${badge.cls}`}>
          {badge.text}
        </span>
        {preview && (
          <span className="hidden sm:inline min-w-0 truncate text-[12px] text-muted-foreground">
            {preview}
          </span>
        )}
        <span className="ml-auto flex items-center gap-1.5 flex-shrink-0">
          <span className="text-[11px] text-muted-foreground whitespace-nowrap">{statsLabel}</span>
          {elapsedLabel && (
            <span className="text-[11px] tabular-nums text-muted-foreground">{elapsedLabel}</span>
          )}
          <ChevronDown
            className={cn(
              "h-4 w-4 text-muted-foreground/60 transition-transform",
              expanded && "rotate-180",
            )}
          />
        </span>
      </button>

      {isFailed && !expanded && (
        <p className="px-3 sm:px-3.5 pb-2.5 text-[12px] text-red-600 dark:text-red-400 leading-5 break-words">
          {failLabel}
          {diagnostic ? `：${diagnostic}` : ""}
        </p>
      )}

      {expanded && (
        <div className="px-3 sm:px-3.5 pb-2 space-y-2">
          {showReason && (
            <p className="text-[12px] text-muted-foreground leading-5 break-words">{reason}</p>
          )}

          {isFailed && (
            <p className="text-[12px] text-red-600 dark:text-red-400 leading-5 break-words">
              {failLabel}
              {diagnostic ? `：${diagnostic}` : ""}
            </p>
          )}

          {tools.length > 0 && (
            <div>
              {hiddenCount > 0 && (
                <button
                  type="button"
                  onClick={() => setShowAllTools(true)}
                  className="mb-1 text-[12px] text-muted-foreground hover:text-foreground transition-colors"
                >
                  显示全部 {tools.length} 条（已隐藏 {hiddenCount} 条）
                </button>
              )}
              {visibleTools.map((tool, i) => (
                <ToolCallCard
                  key={`${tool.name}-${tool.index}-${i}`}
                  name={tool.name}
                  args={tool.args ?? {}}
                  status={tool.status}
                  result={tool.result}
                  error={tool.error}
                  isLast={i === visibleTools.length - 1}
                />
              ))}
              <div ref={timelineEndRef} />
            </div>
          )}

          {summary && isDone && (
            <div className="pt-1 border-t border-[var(--em-hairline)]">
              <p className="text-[11px] font-medium text-muted-foreground mb-1">结果摘要</p>
              <div className="text-[12px] text-muted-foreground leading-relaxed [&_p]:my-1 [&_p:first-child]:mt-0 [&_strong]:font-medium [&_code]:rounded [&_code]:bg-[var(--em-fill)] [&_code]:px-1 [&_code]:text-[11px]">
                <ReactMarkdown remarkPlugins={[remarkGfm]}>{summary}</ReactMarkdown>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
