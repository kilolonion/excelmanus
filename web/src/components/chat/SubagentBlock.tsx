"use client";

import { useState, useRef, useEffect } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  Bot,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Loader2,
  XCircle,
  AlertTriangle,
  ClipboardList,
  HelpCircle,
  Shield,
} from "lucide-react";

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

interface VerificationReport {
  verdict: "pass" | "fail" | "unknown";
  confidence: "high" | "medium" | "low";
  checks: string[];
  issues: string[];
  mode: "advisory" | "blocking";
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
  verificationReport?: VerificationReport;
}

const verificationVerdictConfig = {
  pass: {
    icon: CheckCircle2,
    label: "验证通过",
    iconColor: "text-emerald-600 dark:text-emerald-400",
    labelColor: "text-emerald-700 dark:text-emerald-300",
  },
  fail: {
    icon: AlertTriangle,
    label: "验证未通过",
    iconColor: "text-amber-600 dark:text-amber-400",
    labelColor: "text-amber-700 dark:text-amber-300",
  },
  unknown: {
    icon: HelpCircle,
    label: "验证不确定",
    iconColor: "text-slate-500 dark:text-slate-400",
    labelColor: "text-slate-600 dark:text-slate-300",
  },
};

const verificationConfidenceBadge: Record<string, string> = {
  high: "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300",
  medium: "bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-300",
  low: "bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-400",
};

const confidenceLabelMap: Record<string, string> = {
  high: "高",
  medium: "中",
  low: "低",
};

export function SubagentBlock({
  name,
  reason,
  iterations,
  toolCalls,
  status,
  summary,
  success,
  tools = [],
  verificationReport,
}: SubagentBlockProps) {
  const [expanded, setExpanded] = useState(true);
  const [showAllTools, setShowAllTools] = useState(false);
  const [expandedToolIdx, setExpandedToolIdx] = useState<number | null>(null);
  const [reasonExpanded, setReasonExpanded] = useState(false);
  const timelineEndRef = useRef<HTMLDivElement>(null);

  const isDone = status === "done";
  const isFailed = isDone && success === false;

  // 完成后自动折叠（有验证报告时保持展开以显示结果）
  useEffect(() => {
    if (isDone && !isFailed && !verificationReport) {
      setExpanded(false);
    }
  }, [isDone, isFailed, verificationReport]);

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
      className={`my-1 rounded-lg border transition-colors ${
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
        className="group/card flex w-full items-center gap-0 text-sm text-left cursor-pointer hover:bg-violet-500/[0.05] dark:hover:bg-violet-500/[0.08] rounded-t-lg transition-colors overflow-hidden"
      >
        {/* 左侧强调条 */}
        <div className={`w-[3px] self-stretch rounded-l-lg shrink-0 ${barColor}`} />

        <div className="flex items-center gap-2 flex-1 min-w-0 px-2.5 py-1.5">
          {/* 圆形图标徽章 */}
          <div
            className={`flex items-center justify-center h-5 w-5 rounded-full shrink-0 ${
              isDone
                ? isFailed
                  ? "bg-red-500/10 dark:bg-red-400/15"
                  : "bg-emerald-500/10 dark:bg-emerald-400/15"
                : "bg-violet-500/10 dark:bg-violet-400/15"
            }`}
          >
            <Bot
              className={`h-3 w-3 ${
                isDone
                  ? isFailed
                    ? "text-red-600 dark:text-red-400"
                    : "text-emerald-600 dark:text-emerald-400"
                  : "text-violet-600 dark:text-violet-400"
              }`}
            />
          </div>

          {/* 名称胶囊 */}
          <span className="inline-flex items-center rounded-md px-1.5 py-px text-[11px] font-medium font-mono flex-shrink-0 bg-violet-100/60 dark:bg-violet-500/15 text-violet-700 dark:text-violet-300">
            {getDisplayName(name)}
          </span>

          {/* 状态图标 */}
          {status === "running" ? (
            <Loader2 className="h-3 w-3 animate-spin text-violet-500 dark:text-violet-400 shrink-0" />
          ) : isFailed ? (
            <XCircle className="h-3 w-3 text-red-500 dark:text-red-400 shrink-0" />
          ) : (
            <CheckCircle2 className="h-3 w-3 text-emerald-500 dark:text-emerald-400 shrink-0" />
          )}

          {/* 右侧：统计 + 箭头 */}
          <span className="ml-auto flex items-center gap-1.5 flex-shrink-0 pl-2">
            <span className="text-[10px] text-muted-foreground whitespace-nowrap">
              {iterations} 轮 · {toolCalls} 调用
            </span>
            <ChevronRight
              className={`h-3 w-3 text-muted-foreground/50 shrink-0 transition-all duration-300 ${
                expanded ? "rotate-90" : "group-hover/card:translate-x-0.5 group-hover/card:text-muted-foreground/70"
              }`}
            />
          </span>
        </div>
      </button>

      {/* ── Expanded content ── */}
      <AnimatePresence initial={false}>
        {expanded && (
          <motion.div
            key="subagent-content"
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.25, ease: [0.4, 0, 0.2, 1] }}
            className="overflow-hidden"
          >
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

          {/* Inline Verification Report */}
          {verificationReport && isDone && (() => {
            const vCfg = verificationVerdictConfig[verificationReport.verdict] || verificationVerdictConfig.unknown;
            const VIcon = vCfg.icon;
            return (
              <div className="ml-1 pt-1.5 border-t border-border/50 space-y-1">
                <div className="flex items-center gap-2">
                  <VIcon className={`h-3.5 w-3.5 ${vCfg.iconColor}`} />
                  <span className={`text-xs font-medium ${vCfg.labelColor}`}>{vCfg.label}</span>
                  {confidenceLabelMap[verificationReport.confidence] && (
                    <span className={`rounded-full px-1.5 py-px text-[10px] font-medium ${verificationConfidenceBadge[verificationReport.confidence] || verificationConfidenceBadge.low}`}>
                      {confidenceLabelMap[verificationReport.confidence]}
                    </span>
                  )}
                  {verificationReport.mode === "blocking" && (
                    <span className="flex items-center gap-0.5 rounded-full bg-red-100 px-1.5 py-px text-[10px] font-medium text-red-700 dark:bg-red-900/40 dark:text-red-300">
                      <Shield className="h-2.5 w-2.5" />
                      阻断
                    </span>
                  )}
                </div>
                {verificationReport.checks.length > 0 && (
                  <div className="space-y-0.5">
                    {verificationReport.checks.map((check, i) => (
                      <div key={i} className="flex items-start gap-1.5 text-[11px] text-slate-600 dark:text-slate-400">
                        <span className="mt-px text-emerald-500">✓</span>
                        <span>{check}</span>
                      </div>
                    ))}
                  </div>
                )}
                {verificationReport.issues.length > 0 && (
                  <div className="space-y-0.5">
                    {verificationReport.issues.map((issue, i) => (
                      <div key={i} className="flex items-start gap-1.5 text-[11px] text-amber-700 dark:text-amber-400">
                        <span className="mt-px">⚠</span>
                        <span>{issue}</span>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            );
          })()}
        </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
