"use client";

import { useState, useRef, useEffect } from "react";
import {
  Bot,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Loader2,
  XCircle,
  AlertTriangle,
  ClipboardList,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import type { SubagentToolCall } from "@/lib/types";

const TOOL_TIMELINE_COLLAPSE_THRESHOLD = 5;

const DISPLAY_NAMES: Record<string, string> = {
  subagent: "通用子代理",
  explorer: "探索器",
  verifier: "验证器",
};

function getDisplayName(name: string): string {
  return DISPLAY_NAMES[name] || name;
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
  const [reasonExpanded, setReasonExpanded] = useState(false);
  const timelineEndRef = useRef<HTMLDivElement>(null);

  const isDone = status === "done";
  const isFailed = isDone && success === false;

  // 完成后自动折叠
  useEffect(() => {
    if (isDone && !isFailed) {
      setExpanded(false);
    }
  }, [isDone, isFailed]);

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

  // 渲染 summary：支持换行和简单 markdown 列表
  const renderSummary = (text: string) => {
    const lines = text.split("\n");
    return lines.map((line, i) => {
      const trimmed = line.trim();
      if (!trimmed) return <br key={i} />;
      if (trimmed.startsWith("- ") || trimmed.startsWith("* ")) {
        return (
          <span key={i} className="block pl-3">
            {"• "}{trimmed.slice(2)}
          </span>
        );
      }
      if (/^\d+\.\s/.test(trimmed)) {
        return <span key={i} className="block pl-3">{trimmed}</span>;
      }
      return <span key={i} className="block">{trimmed}</span>;
    });
  };

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
          {getDisplayName(name)}
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
          {/* Reason — 可展开 */}
          {reason && (
            <div className="ml-1">
              <p
                className={`text-xs text-muted-foreground pl-3 border-l-2 border-violet-300/30 dark:border-violet-500/20 ${
                  reasonExpanded ? "" : "line-clamp-2"
                }`}
              >
                {reason}
              </p>
              {reason.length > 120 && (
                <button
                  type="button"
                  onClick={() => setReasonExpanded((v) => !v)}
                  className="text-[10px] text-violet-600 dark:text-violet-400 hover:underline cursor-pointer pl-3 mt-0.5"
                >
                  {reasonExpanded ? "收起" : "展开全部"}
                </button>
              )}
            </div>
          )}

          {/* 失败时显示错误提示 */}
          {isFailed && !summary && (
            <div className="flex items-center gap-1.5 ml-1 px-2 py-1.5 rounded bg-red-500/5 dark:bg-red-500/10">
              <AlertTriangle className="h-3 w-3 text-red-500 shrink-0" />
              <span className="text-xs text-red-600 dark:text-red-400">子代理执行失败</span>
            </div>
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
                          <pre className="text-[11px] bg-emerald-500/5 dark:bg-emerald-500/10 rounded p-1.5 overflow-x-auto max-h-24 text-foreground/70 whitespace-pre-wrap break-words">
                            {tool.result}
                          </pre>
                        )}
                        {tool.error && (
                          <pre className="text-[11px] bg-red-500/5 dark:bg-red-500/10 rounded p-1.5 overflow-x-auto max-h-24 text-red-600 dark:text-red-400 whitespace-pre-wrap break-words">
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

          {/* Progress indicator (indeterminate while running) */}
          {status === "running" && (
            <div className="flex items-center gap-2 ml-1">
              <div className="flex-1 h-1 rounded-full bg-violet-200/40 dark:bg-violet-500/20 overflow-hidden">
                <div className="h-full w-1/3 rounded-full bg-violet-500 dark:bg-violet-400 animate-[indeterminate-progress_1.5s_ease-in-out_infinite]" />
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
              <div className="text-xs text-muted-foreground leading-relaxed min-w-0">
                {renderSummary(summary)}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
