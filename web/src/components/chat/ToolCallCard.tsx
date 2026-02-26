"use client";

import React, { useState, useCallback, useEffect, useRef, useMemo } from "react";
import {
  Wrench,
  CheckCircle2,
  XCircle,
  Loader2,
  ShieldAlert,
  ChevronDown,
  ChevronRight,
  Upload,
  Check,
  BookOpen,
  PenLine,
  Code,
  Table2,
  ListChecks,
  Search,
  FileText,
  Timer,
  type LucideIcon,
} from "lucide-react";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import { useExcelStore } from "@/stores/excel-store";
import { useSessionStore } from "@/stores/session-store";
import { ExcelPreviewTable } from "@/components/excel/ExcelPreviewTable";
import { ExcelDiffTable } from "@/components/excel/ExcelDiffTable";
import { CodeBlock } from "./CodeBlock";

// 工具分类 → 图标映射
const TOOL_ICON_MAP: Record<string, LucideIcon> = {
  read_excel: BookOpen,
  list_sheets: Search,
  write_cells: PenLine,
  insert_rows: Table2,
  insert_columns: Table2,
  create_sheet: Table2,
  delete_sheet: Table2,
  run_code: Code,
  finish_task: ListChecks,
  read_text_file: FileText,
  sleep: Timer,
};

function getToolIcon(name: string): LucideIcon {
  return TOOL_ICON_MAP[name] || Wrench;
}

/** 检测工具结果的语法高亮语言。 */
function detectResultLanguage(toolName: string, text: string): string | undefined {
  if (toolName === "run_code") return "python";
  const trimmed = text.trimStart();
  if (trimmed.startsWith("{") || trimmed.startsWith("[")) {
    try {
      JSON.parse(trimmed);
      return "json";
    } catch { /* 非 JSON */ }
  }
  return undefined;
}

// 为折叠预览构建工具参数简短摘要
function argsSummary(name: string, args: Record<string, unknown>): string | null {
  const parts: string[] = [];
  if (args.sheet) parts.push(`sheet: ${args.sheet}`);
  if (args.range) parts.push(`range: ${args.range}`);
  if (args.path) {
    const p = String(args.path);
    parts.push(p.split("/").pop() || p);
  }
  if (name === "run_code" && typeof args.code === "string") {
    const firstLine = args.code.split("\n")[0].slice(0, 50);
    parts.push(firstLine + (args.code.length > 50 ? "…" : ""));
  }
  if (name === "sleep") {
    if (args.seconds) parts.push(`${args.seconds}s`);
    if (args.reason) parts.push(String(args.reason).slice(0, 40));
  }
  return parts.length > 0 ? parts.join(" · ") : null;
}

// 工具分类 → 绿色系主题
type ToolCategoryTheme = {
  bar: string;          // 左侧强调条
  iconBg: string;       // 圆形图标背景
  iconColor: string;    // 图标前景色
  pillBg: string;       // 工具名胶囊背景
  pillText: string;     // 工具名胶囊文字
  cardBg: string;       // 卡片背景
  cardHover: string;    // 卡片悬停背景
  border: string;       // 卡片边框
  label: string;        // 人类可读的分类标签
};

const CATEGORY_THEMES: Record<string, ToolCategoryTheme> = {
  read: {
    bar: "#0d9488",
    iconBg: "bg-teal-500/10 dark:bg-teal-400/15",
    iconColor: "text-teal-600 dark:text-teal-400",
    pillBg: "bg-teal-500/8 dark:bg-teal-400/10",
    pillText: "text-teal-700 dark:text-teal-300",
    cardBg: "bg-teal-500/[0.02] dark:bg-teal-500/[0.03]",
    cardHover: "hover:bg-teal-500/[0.05] dark:hover:bg-teal-400/[0.06]",
    border: "border-teal-300/40 dark:border-teal-500/20",
    label: "读取",
  },
  write: {
    bar: "#217346",
    iconBg: "bg-emerald-500/10 dark:bg-emerald-400/15",
    iconColor: "text-emerald-600 dark:text-emerald-400",
    pillBg: "bg-emerald-500/8 dark:bg-emerald-400/10",
    pillText: "text-emerald-700 dark:text-emerald-300",
    cardBg: "bg-emerald-500/[0.02] dark:bg-emerald-500/[0.03]",
    cardHover: "hover:bg-emerald-500/[0.05] dark:hover:bg-emerald-400/[0.06]",
    border: "border-emerald-300/40 dark:border-emerald-500/20",
    label: "写入",
  },
  code: {
    bar: "#4d7c0f",
    iconBg: "bg-lime-500/10 dark:bg-lime-400/15",
    iconColor: "text-lime-700 dark:text-lime-400",
    pillBg: "bg-lime-500/8 dark:bg-lime-400/10",
    pillText: "text-lime-700 dark:text-lime-300",
    cardBg: "bg-lime-500/[0.02] dark:bg-lime-500/[0.03]",
    cardHover: "hover:bg-lime-500/[0.05] dark:hover:bg-lime-400/[0.06]",
    border: "border-lime-300/40 dark:border-lime-500/20",
    label: "代码",
  },
  finish: {
    bar: "#15803d",
    iconBg: "bg-green-500/10 dark:bg-green-400/15",
    iconColor: "text-green-600 dark:text-green-400",
    pillBg: "bg-green-500/8 dark:bg-green-400/10",
    pillText: "text-green-700 dark:text-green-300",
    cardBg: "bg-green-500/[0.02] dark:bg-green-500/[0.03]",
    cardHover: "hover:bg-green-500/[0.05] dark:hover:bg-green-400/[0.06]",
    border: "border-green-300/40 dark:border-green-500/20",
    label: "完成",
  },
  sleep: {
    bar: "#6366f1",
    iconBg: "bg-indigo-500/10 dark:bg-indigo-400/15",
    iconColor: "text-indigo-600 dark:text-indigo-400",
    pillBg: "bg-indigo-500/8 dark:bg-indigo-400/10",
    pillText: "text-indigo-700 dark:text-indigo-300",
    cardBg: "bg-indigo-500/[0.02] dark:bg-indigo-500/[0.03]",
    cardHover: "hover:bg-indigo-500/[0.05] dark:hover:bg-indigo-400/[0.06]",
    border: "border-indigo-300/40 dark:border-indigo-500/20",
    label: "等待",
  },
  default: {
    bar: "#6b7280",
    iconBg: "bg-slate-500/8 dark:bg-slate-400/10",
    iconColor: "text-slate-500 dark:text-slate-400",
    pillBg: "bg-slate-500/6 dark:bg-slate-400/8",
    pillText: "text-slate-600 dark:text-slate-400",
    cardBg: "bg-slate-500/[0.015] dark:bg-slate-400/[0.02]",
    cardHover: "hover:bg-slate-500/[0.04] dark:hover:bg-slate-400/[0.05]",
    border: "border-border",
    label: "工具",
  },
};

function getToolCategory(name: string): string {
  if (["read_excel", "list_sheets", "read_text_file"].includes(name)) return "read";
  if (["write_cells", "insert_rows", "insert_columns", "create_sheet", "delete_sheet"].includes(name)) return "write";
  if (name === "run_code") return "code";
  if (name === "finish_task") return "finish";
  if (name === "sleep") return "sleep";
  return "default";
}

function getTheme(name: string): ToolCategoryTheme {
  return CATEGORY_THEMES[getToolCategory(name)] || CATEGORY_THEMES.default;
}

const EXCEL_READ_TOOLS = new Set(["read_excel"]);
const EXCEL_WRITE_TOOLS = new Set([
  "write_cells", "insert_rows", "insert_columns",
  "create_sheet", "delete_sheet",
]);
const EXCEL_DIFF_TOOLS = new Set([
  ...EXCEL_WRITE_TOOLS,
  "run_code", "finish_task",
]);

interface ToolCallCardProps {
  toolCallId?: string;
  name: string;
  args: Record<string, unknown>;
  status: "running" | "success" | "error" | "pending";
  result?: string;
  error?: string;
}

export const ToolCallCard = React.memo(function ToolCallCard({ toolCallId, name, args, status, result, error }: ToolCallCardProps) {
  const [open, setOpen] = useState(false);
  const [applyingInline, setApplyingInline] = useState(false);
  const [appliedInline, setAppliedInline] = useState(false);
  const [chevronClass, setChevronClass] = useState("");

  // 处理展开/收起箭头动画
  const handleOpenChange = useCallback((newOpen: boolean) => {
    setOpen(newOpen);
    // 触发动画类名
    const animationClass = newOpen ? "tool-chevron-expand" : "tool-chevron-collapse";
    setChevronClass(animationClass);
    // 动画结束后清除动画类（收起时超时稍长）
    setTimeout(() => setChevronClass(""), newOpen ? 250 : 350);
  }, []);

  // 运行中工具的已用时间
  const startRef = useRef<number | null>(null);
  const [elapsed, setElapsed] = useState(0);
  useEffect(() => {
    if (status !== "running") {
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
  }, [status]);

  const isExcelRead = EXCEL_READ_TOOLS.has(name);
  const isExcelWrite = EXCEL_WRITE_TOOLS.has(name);

  const activeSessionId = useSessionStore((s) => s.activeSessionId);
  const applyFile = useExcelStore((s) => s.applyFile);
  const pendingBackups = useExcelStore((s) => s.pendingBackups);

  const preview = useExcelStore((s) =>
    toolCallId && isExcelRead ? s.previews[toolCallId] : undefined
  );
  const canHaveDiff = EXCEL_DIFF_TOOLS.has(name);
  
  // 使用 useMemo 缓存 diffs 计算结果，避免无限循环
  const allDiffs = useExcelStore((s) => s.diffs);
  const diffs = useMemo(() => {
    if (!toolCallId || !canHaveDiff) return [];
    return allDiffs.filter((d) => d.toolCallId === toolCallId);
  }, [toolCallId, canHaveDiff, allDiffs]);

  // 按文件去重获取涉及的文件路径
  const diffFilePaths = Array.from(new Set(diffs.map((d) => d.filePath).filter(Boolean)));
  const hasPendingBackup = diffFilePaths.some((fp) =>
    pendingBackups.some((b) => b.original_path === fp)
  );

  const handleInlineApply = useCallback(async () => {
    if (!activeSessionId || diffFilePaths.length === 0) return;
    setApplyingInline(true);
    let anyOk = false;
    for (const fp of diffFilePaths) {
      const ok = await applyFile(activeSessionId, fp);
      if (ok) anyOk = true;
    }
    setApplyingInline(false);
    if (anyOk) setAppliedInline(true);
  }, [activeSessionId, diffFilePaths, applyFile]);

  const StatusIcon = {
    running: <Loader2 className="h-3 w-3 animate-spin" style={{ color: "var(--em-cyan)" }} />,
    success: <CheckCircle2 className="h-3 w-3" style={{ color: "var(--em-primary)" }} />,
    error: <XCircle className="h-3 w-3" style={{ color: "var(--em-error)" }} />,
    pending: <ShieldAlert className="h-3 w-3" style={{ color: "var(--em-gold)" }} />,
  }[status];

  const ToolIcon = getToolIcon(name);
  const summary = !open ? argsSummary(name, args) : null;
  const theme = getTheme(name);

  const isError = status === "error";
  const isPending = status === "pending";
  const isRunning = status === "running";
  const isSuccess = status === "success";

  const borderCls = isPending
    ? "border-amber-400/50 dark:border-amber-500/30"
    : isError
      ? "border-red-300/50 dark:border-red-500/25"
      : theme.border;

  const bgCls = isPending
    ? "bg-amber-500/[0.04] hover:bg-amber-500/[0.08]"
    : isError
      ? "bg-red-500/[0.03] hover:bg-red-500/[0.06]"
      : `${theme.cardBg} ${theme.cardHover}`;

  return (
    <div className={`my-1 rounded-lg ${isSuccess ? "animate-tool-success-flash" : ""}`}>
      <Collapsible open={open} onOpenChange={handleOpenChange}>
        <CollapsibleTrigger
          className={`group/card flex items-center gap-0 rounded-lg border transition-all duration-200 w-full text-left text-sm overflow-hidden hover:shadow-sm ${borderCls} ${bgCls}`}
        >
          {/* 左侧强调条 */}
          <div
            className={`self-stretch w-[3px] flex-shrink-0 rounded-l-lg transition-colors duration-500 ${isRunning ? "animate-tool-running-bar" : ""}`}
            style={{ backgroundColor: isError ? "var(--em-error)" : theme.bar, "--tool-cat-color": theme.bar } as React.CSSProperties}
          />

          <div className={`flex items-center gap-2 flex-1 min-w-0 px-2.5 py-1.5 ${isRunning ? "animate-tool-running-pulse" : ""}`}>
            {/* 圆形图标徽章 */}
            <span className={`flex items-center justify-center h-5 w-5 rounded-full flex-shrink-0 ${theme.iconBg}`}>
              <ToolIcon className={`h-3 w-3 ${theme.iconColor}`} />
            </span>

            {/* 工具名胶囊 */}
            <span className={`inline-flex items-center rounded-md px-1.5 py-px text-[11px] font-medium font-mono flex-shrink-0 ${theme.pillBg} ${theme.pillText}`}>
              {name}
            </span>

            {/* Args preview */}
            {summary && (
              <span className="text-[10px] text-muted-foreground/70 truncate min-w-0">
                {summary}
              </span>
            )}

            {/* Right side: status cluster */}
            <span className="ml-auto flex items-center gap-1.5 flex-shrink-0 pl-2">
              {isPending && (
                <span className="text-[10px] font-medium px-1.5 py-px rounded-full bg-amber-500/10 text-amber-600 dark:text-amber-400">待审批</span>
              )}
              {isRunning && name === "sleep" && typeof args.seconds === "number" && (
                <span className="flex items-center gap-1.5">
                  <span className="relative h-1 w-16 rounded-full bg-indigo-200/40 dark:bg-indigo-500/20 overflow-hidden">
                    <span
                      className="absolute inset-y-0 left-0 rounded-full bg-indigo-500 dark:bg-indigo-400 transition-all duration-1000 ease-linear"
                      style={{ width: `${Math.min((elapsed / (args.seconds as number)) * 100, 100)}%` }}
                    />
                  </span>
                  <span className="text-[10px] text-indigo-600 dark:text-indigo-400 tabular-nums font-medium">
                    {Math.max(Math.round((args.seconds as number) - elapsed), 0)}s
                  </span>
                </span>
              )}
              {isRunning && !(name === "sleep" && typeof args.seconds === "number") && elapsed > 0 && (
                <span className="text-[10px] text-muted-foreground tabular-nums">{elapsed}s</span>
              )}
              <span className="transition-transform duration-300" style={{ transform: isSuccess ? "scale(1.2)" : "scale(1)" }}>
                {StatusIcon}
              </span>
              <ChevronRight className={`h-3 w-3 text-muted-foreground/50 transition-all duration-300 ${chevronClass} ${
                !chevronClass && open 
                  ? "rotate-90" 
                  : !chevronClass && "group-hover/card:translate-x-0.5 group-hover/card:text-muted-foreground/70"
              }`} />
            </span>
          </div>
        </CollapsibleTrigger>

        <CollapsibleContent className="data-[state=open]:animate-collapsible-down data-[state=closed]:animate-collapsible-up overflow-hidden">
          <div className={`px-3 py-2 text-xs space-y-2 border border-t-0 rounded-b-lg ml-[3px] ${borderCls} ${theme.cardBg}`}>
            {Object.keys(args).length > 0 && (
              <div>
                <p className="font-semibold text-muted-foreground mb-1">参数</p>
                <CodeBlock language="json" code={JSON.stringify(args, null, 2)} />
              </div>
            )}
            {result && (() => {
              const lang = detectResultLanguage(name, result);
              return (
                <div>
                  <p className="font-semibold text-muted-foreground mb-1">结果</p>
                  {lang ? (
                    <CodeBlock language={lang} code={result} />
                  ) : (
                    <pre className="bg-muted/30 rounded p-2 overflow-x-auto whitespace-pre-wrap max-h-48">
                      {result}
                    </pre>
                  )}
                </div>
              );
            })()}
            {error && (
              <div>
                <p className="flex items-center gap-1 font-semibold mb-1" style={{ color: "var(--em-error)" }}>
                  <span className="inline-block h-1 w-1 rounded-full bg-red-500" />
                  错误
                </p>
                <pre className="bg-red-50/80 dark:bg-red-950/20 rounded-md p-2 overflow-x-auto whitespace-pre-wrap text-red-700 dark:text-red-300 border border-red-200/40 dark:border-red-500/20">
                  {error}
                </pre>
              </div>
            )}
          </div>
        </CollapsibleContent>
      </Collapsible>

      {/* Excel inline preview / diff — always visible when data available */}
      {preview && <ExcelPreviewTable data={preview} />}
      {diffs.length > 0 && (
        <div className="relative">
          {diffs.map((d, i) => (
            <ExcelDiffTable key={`${d.toolCallId}-${d.sheet}-${i}`} data={d} />
          ))}
          {/* Inline apply button for this diff's file */}
          {(hasPendingBackup || appliedInline) && (
            <div className="flex justify-end px-3 -mt-1 mb-2">
              {appliedInline ? (
                <span className="flex items-center gap-1 text-[11px] text-emerald-600 dark:text-emerald-400">
                  <Check className="h-3 w-3" />
                  已应用到原文件
                </span>
              ) : (
                <button
                  onClick={handleInlineApply}
                  disabled={applyingInline}
                  className="flex items-center gap-1 px-2 py-0.5 rounded text-[11px] font-medium transition-colors text-white"
                  style={{ backgroundColor: "var(--em-primary)" }}
                >
                  {applyingInline ? (
                    <Loader2 className="h-3 w-3 animate-spin" />
                  ) : (
                    <>
                      <Upload className="h-3 w-3" />
                      Apply 到原文件
                    </>
                  )}
                </button>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
});
