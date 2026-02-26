"use client";

import { useState, useRef, useEffect } from "react";
import {
  Bot,
  CheckCircle2,
  ChevronRight,
  Loader2,
  XCircle,
  ClipboardList,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import type { SubagentToolCall } from "@/lib/types";

const TOOL_TIMELINE_COLLAPSE_THRESHOLD = 5;

interface SubagentBlockProps {
  name: string;
  reason: string;
  iterations: number;
  toolCalls: number;
  status: "running" | "done";
  summary?: string;
  success?: boolean;
  tools?: SubagentToolCall[];
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
}: SubagentBlockProps) {
  const [expanded, setExpanded] = useState(true);
  const [showAllTools, setShowAllTools] = useState(false);
  const [expandedToolIdx, setExpandedToolIdx] = useState<number | null>(null);
  const timelineEndRef = useRef<HTMLDivElement>(null);

  const isDone = status === "done";
  const isFailed = isDone && success === false;

  // 自动滚动到最新工具
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

  // 左侧竖条颜色
  const barColor = isDone
    ? isFailed
      ? "bg-red-500"
      : "bg-emerald-500"
    : "bg-violet-500 animate-subagent-running-pulse";

  return (
    <div
      className={`my-2 rounded-lg border transition-colors ${
        isDone
          ? isFailed
            ? "border-red-300/40 dark:border-red-500/20 bg-red-500/[0.02] dark:bg-red-500/[0.03]"
            : "border-emerald-300/40 dark:border-emerald-500/20 bg-emerald-500/[0.02] dark:bg-emerald-500/[0.03]"
          : "border-violet-300/40 dark:border-violet-500/20 bg-violet-500/[0.02] dark:bg-violet-500/[0.03]"
      }`}
    >
      {/* ── Header ── */}
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        className="flex w-full items-center gap-2 px-3 py-2 text-sm text-left cursor-pointer hover:bg-violet-500/[0.05] dark:hover:bg-violet-500/[0.08] rounded-t-lg transition-colors"
      >
        <div className={`w-1 self-stretch rounded-full shrink-0 ${barColor}`} />
        <div
          className={`flex items-center justify-center h-6 w-6 rounded-md shrink-0 ${
            isDone
              ? isFailed
                ? "bg-red-500/10 dark:bg-red-400/15"
                : "bg-emerald-500/10 dark:bg-emerald-400/15"
              : "bg-violet-500/10 dark:bg-violet-400/15"
          }`}
        >
          <Bot
            className={`h-3.5 w-3.5 ${
              isDone
                ? isFailed
                  ? "text-red-600 dark:text-red-400"
                  : "text-emerald-600 dark:text-emerald-400"
                : "text-violet-600 dark:text-violet-400"
            }`}
          />
        </div>
        <Badge
          variant="secondary"
          className="text-xs font-mono truncate max-w-[140px] sm:max-w-none bg-violet-100/60 dark:bg-violet-500/15 text-violet-700 dark:text-violet-300 border-0"
        >
          {name}
        </Badge>
        {status === "running" ? (
          <Loader2 className="h-3.5 w-3.5 animate-spin text-violet-500 dark:text-violet-400 shrink-0" />
        ) : isFailed ? (
          <XCircle className="h-3.5 w-3.5 text-red-500 dark:text-red-400 shrink-0" />
        ) : (
          <CheckCircle2 className="h-3.5 w-3.5 text-emerald-500 dark:text-emerald-400 shrink-0" />
        )}
        <span className="text-xs text-muted-foreground ml-auto whitespace-nowrap hidden sm:inline">
          {iterations} 轮 · {toolCalls} 调用
        </span>
        <span className="text-xs text-muted-foreground ml-auto whitespace-nowrap sm:hidden">
          {iterations}轮·{toolCalls}调用
        </span>
        <ChevronRight
          className={`h-3.5 w-3.5 text-muted-foreground shrink-0 transition-transform duration-200 ${
            expanded ? "rotate-90" : ""
          }`}
        />
      </button>

      {/* ── Expanded content ── */}
      {expanded && (
        <div className="px-3 pb-2.5 pt-0 space-y-2">
          {/* Reason */}
          {reason && (
            <p className="text-xs text-muted-foreground pl-3 line-clamp-2 border-l-2 border-violet-300/30 dark:border-violet-500/20 ml-1">
              {reason}
            </p>
          )}

          {/* Tool Timeline */}
          {tools.length > 0 && (
            <div className="space-y-0.5 ml-1">
              {hiddenCount > 0 && (
                <button
                  type="button"
                  onClick={() => setShowAllTools(true)}
                  className="text-xs text-violet-600 dark:text-violet-400 hover:underline cursor-pointer pl-3 py-0.5"
                >
                  显示全部 {tools.length} 条（已隐藏 {hiddenCount} 条）
                </button>
              )}
              {visibleTools.map((tool, i) => {
                const realIdx = hiddenCount > 0 && !showAllTools ? tools.length - TOOL_TIMELINE_COLLAPSE_THRESHOLD + i : i;
                const isExpanded = expandedToolIdx === realIdx;
                return (
                  <div
                    key={`${tool.name}-${tool.index}-${i}`}
                    className="animate-subagent-tool-enter"
                  >
                    <button
                      type="button"
                      onClick={() => setExpandedToolIdx(isExpanded ? null : realIdx)}
                      className={`flex w-full items-center gap-1.5 text-xs rounded px-2 py-1 cursor-pointer transition-colors ${
                        tool.status === "running"
                          ? "bg-violet-500/[0.06] dark:bg-violet-500/[0.1]"
                          : "hover:bg-muted/50"
                      }`}
                    >
                      {/* Status icon */}
                      {tool.status === "running" ? (
                        <Loader2 className="h-3 w-3 animate-spin text-violet-500 shrink-0" />
                      ) : tool.status === "success" ? (
                        <CheckCircle2 className="h-3 w-3 text-emerald-500 shrink-0" />
                      ) : (
                        <XCircle className="h-3 w-3 text-red-500 shrink-0" />
                      )}
                      {/* Tool name */}
                      <span className="font-mono text-foreground/80 shrink-0">
                        {tool.name}
                      </span>
                      {/* Args summary */}
                      {tool.argsSummary && (
                        <span className="text-muted-foreground truncate">
                          {tool.argsSummary}
                        </span>
                      )}
                      {/* Expand indicator */}
                      {(tool.result || tool.error || (tool.args && Object.keys(tool.args).length > 0)) && (
                        <ChevronRight
                          className={`h-3 w-3 text-muted-foreground ml-auto shrink-0 transition-transform duration-150 ${
                            isExpanded ? "rotate-90" : ""
                          }`}
                        />
                      )}
                    </button>
                    {/* Expanded detail */}
                    {isExpanded && (
                      <div className="ml-5 mt-0.5 mb-1 space-y-1">
                        {tool.args && Object.keys(tool.args).length > 0 && (
                          <pre className="text-[11px] bg-muted/50 rounded p-1.5 overflow-x-auto max-h-32 text-muted-foreground">
                            {JSON.stringify(tool.args, null, 2)}
                          </pre>
                        )}
                        {tool.result && (
                          <pre className="text-[11px] bg-emerald-500/5 dark:bg-emerald-500/10 rounded p-1.5 overflow-x-auto max-h-24 text-foreground/70">
                            {tool.result}
                          </pre>
                        )}
                        {tool.error && (
                          <pre className="text-[11px] bg-red-500/5 dark:bg-red-500/10 rounded p-1.5 overflow-x-auto max-h-24 text-red-600 dark:text-red-400">
                            {tool.error}
                          </pre>
                        )}
                      </div>
                    )}
                  </div>
                );
              })}
              <div ref={timelineEndRef} />
            </div>
          )}

          {/* Progress bar (only while running) */}
          {status === "running" && iterations > 0 && (
            <div className="flex items-center gap-2 ml-1">
              <div className="flex-1 h-1 rounded-full bg-violet-200/40 dark:bg-violet-500/20 overflow-hidden">
                <div
                  className="h-full rounded-full bg-violet-500 dark:bg-violet-400 transition-all duration-500"
                  style={{ width: `${Math.min((iterations / 10) * 100, 95)}%` }}
                />
              </div>
              <span className="text-[10px] text-muted-foreground whitespace-nowrap">
                {iterations} 轮 · {toolCalls} 调用
              </span>
            </div>
          )}

          {/* Summary */}
          {summary && isDone && (
            <div className="flex items-start gap-1.5 ml-1 pt-1 border-t border-border/50">
              <ClipboardList className="h-3 w-3 text-muted-foreground mt-0.5 shrink-0" />
              <p className="text-xs text-muted-foreground leading-relaxed">{summary}</p>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
