"use client";

import React, { useState, useEffect, useRef, useMemo } from "react";
import { ChevronDown } from "lucide-react";
import { toolIcon, toolStatusIconClass } from "@/lib/tool-icons";
import { useIsMobile } from "@/hooks/use-mobile";
import { useExcelStore } from "@/stores/excel-store";
import { useChatStore } from "@/stores/chat-store";
import { ExcelPreviewTable } from "@/components/excel/ExcelPreviewTable";
import { ExcelDiffTable } from "@/components/excel/ExcelDiffTable";
import { TextDiffView } from "./TextDiffView";
import { TextPreviewView } from "./TextPreviewView";
import StreamingTextPreview from "./StreamingTextPreview";
import { CodeBlock } from "./CodeBlock";
import { MergeResultCard } from "./MergeResultCard";
import {
  extractToolContext,
  formatToolContextLine,
  isWriteTool,
  toolActionTitle,
} from "@/lib/tool-labels";

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

function parseMergeResult(toolName: string, resultStr: string | undefined): {
  sourceFiles: string[]; outputFile: string;
  rowsMatched: number; rowsAdded: number; rowsUnmatched: number;
  keyColumns: string[]; joinType: string;
} | null {
  if (!resultStr) return null;
  if (!["run_code", "discover_file_relationships", "compare_excel"].includes(toolName)) return null;
  const trimmed = resultStr.trimStart();
  if (!trimmed.startsWith("{") && !trimmed.startsWith("[")) return null;
  try {
    const data = JSON.parse(trimmed);
    if (typeof data !== "object" || data === null) return null;
    const hasStats = typeof data.rows_matched === "number"
      || typeof data.matched_count === "number"
      || typeof data.merge_rows === "number";
    const hasMergeHint = typeof data.merge_result === "object" || typeof data.output_file === "string";
    if (!hasStats && !hasMergeHint) return null;
    const src = data.merge_result ?? data;
    return {
      sourceFiles: Array.isArray(src.source_files) ? src.source_files : [],
      outputFile: src.output_file ?? src.output ?? "",
      rowsMatched: src.rows_matched ?? src.matched_count ?? 0,
      rowsAdded: src.rows_added ?? src.added_count ?? 0,
      rowsUnmatched: src.rows_unmatched ?? src.unmatched_count ?? 0,
      keyColumns: Array.isArray(src.key_columns) ? src.key_columns : [],
      joinType: src.join_type ?? src.how ?? "",
    };
  } catch {
    return null;
  }
}

const EXCEL_READ_TOOLS = new Set([
  "inspect_spreadsheet",
  "analyze_spreadsheet",
  "compare_spreadsheets",
]);
const EXCEL_WRITE_TOOLS = new Set([
  "edit_spreadsheet",
  "format_spreadsheet",
  "manage_spreadsheet_objects",
]);
const EXCEL_DIFF_TOOLS = new Set([
  ...EXCEL_WRITE_TOOLS,
  "run_code", "finish_task", "compare_spreadsheets",
]);
const TEXT_DIFF_TOOLS = new Set([
  "write_text_file", "edit_text_file", "run_code", "write_plan",
]);
const TEXT_PREVIEW_TOOLS = new Set(["read_text_file"]);

interface ToolCallCardProps {
  toolCallId?: string;
  name: string;
  args: Record<string, unknown>;
  status: "running" | "success" | "error" | "pending" | "streaming";
  result?: string;
  error?: string;
  isLast?: boolean;
  parentCallId?: string;
  nested?: boolean;
}

export const ToolCallCard = React.memo(function ToolCallCard({
  toolCallId, name, args, status, result, error, isLast = true,
  parentCallId, nested = false,
}: ToolCallCardProps) {
  const isMobile = useIsMobile();
  const [open, setOpen] = useState(false);

  const startRef = useRef<number | null>(null);
  const [elapsed, setElapsed] = useState(0);
  const isStreaming = (status as string) === "streaming";
  const streamingRawArgs = useExcelStore((s) =>
    toolCallId && (isStreaming || status === "running") && TEXT_DIFF_TOOLS.has(name)
      ? s.streamingToolContent[toolCallId] ?? null
      : null
  );

  const toolProgress = useChatStore((s) =>
    toolCallId ? s.toolProgress[toolCallId] ?? null : null
  );
  const pendingApproval = useChatStore((s) => s.pendingApproval);

  useEffect(() => {
    if (status !== "running" && !isStreaming) {
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
  }, [status, isStreaming]);

  const isExcelRead = EXCEL_READ_TOOLS.has(name);

  const preview = useExcelStore((s) =>
    toolCallId && isExcelRead ? s.previews[toolCallId] : undefined
  );
  const canHaveDiff = EXCEL_DIFF_TOOLS.has(name);
  const canHaveTextDiff = TEXT_DIFF_TOOLS.has(name);
  const canHaveTextPreview = TEXT_PREVIEW_TOOLS.has(name);

  const allDiffs = useExcelStore((s) => s.diffs);
  const diffs = useMemo(() => {
    if (!toolCallId || !canHaveDiff) return [];
    return allDiffs.filter((d) => d.toolCallId === toolCallId);
  }, [toolCallId, canHaveDiff, allDiffs]);

  const allTextDiffs = useExcelStore((s) => s.textDiffs);
  const textDiffs = useMemo(() => {
    if (!toolCallId || !canHaveTextDiff) return [];
    return allTextDiffs.filter((d) => d.toolCallId === toolCallId);
  }, [toolCallId, canHaveTextDiff, allTextDiffs]);

  const textPreview = useExcelStore((s) =>
    toolCallId && canHaveTextPreview ? s.textPreviews[toolCallId] : undefined
  );

  const isError = status === "error";
  const isPending = status === "pending";
  const isRunning = status === "running" || isStreaming;
  const title = toolActionTitle(name, args);
  const ctx = extractToolContext(args);
  const contextLine = formatToolContextLine(ctx);

  const Icon = toolIcon(name);
  const node = (
    <Icon className={`h-4 w-4 ${toolStatusIconClass(isStreaming ? "running" : status)}`} />
  );

  const elapsedLabel = isRunning && elapsed > 0 ? `${elapsed}s` : null;
  const showApprovalCta = isPending && isWriteTool(name);

  return (
    <div
      className={`flex gap-2.5 ${nested ? "ml-3 pl-2 border-l border-[var(--em-hairline)]" : ""}`}
      data-tool-call-id={toolCallId || undefined}
      data-parent-call-id={parentCallId || undefined}
      data-nested-tool={nested ? "true" : undefined}
    >
      <div className="flex w-4 flex-col items-center flex-shrink-0">
        <div className="mt-0.5">{node}</div>
        {!isLast && <div className="mt-1 w-px flex-1 bg-[#C9D1CB] dark:bg-muted-foreground/30 min-h-[12px]" />}
      </div>

      <div className={`min-w-0 flex-1 ${isLast ? "pb-1" : "pb-3"}`}>
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          className="flex w-full items-start gap-2 text-left group/step"
        >
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2">
              <span className="text-[13px] font-medium text-foreground leading-5">{title}</span>
              {nested && (
                <span className="text-[10px] font-medium text-muted-foreground">子调用</span>
              )}
              {isPending && (
                <span className="text-[10px] font-medium text-amber-700 dark:text-amber-400">等待授权</span>
              )}
              {isError && (
                <span className="text-[10px] font-medium text-red-600">失败</span>
              )}
            </div>
            {contextLine && (
              <p className="text-[12px] text-muted-foreground mt-0.5 leading-4 break-words">
                {contextLine}
              </p>
            )}
            {isRunning && toolProgress?.message && (
              <p className="text-[11px] text-muted-foreground mt-0.5 truncate">{toolProgress.message}</p>
            )}
          </div>
          <span className="flex items-center gap-1 flex-shrink-0 pt-0.5">
            {elapsedLabel && (
              <span className="text-[11px] tabular-nums text-muted-foreground">{elapsedLabel}</span>
            )}
            <ChevronDown className={`h-3.5 w-3.5 text-muted-foreground/40 transition-transform ${open ? "rotate-180" : ""}`} />
          </span>
        </button>

        {showApprovalCta && (
          <div className="mt-2 rounded-xl border border-amber-200/80 dark:border-amber-500/25 bg-amber-50/80 dark:bg-amber-500/10 px-3 py-2.5">
            <div className="flex flex-col sm:flex-row sm:items-center gap-2">
              <div className="min-w-0 flex-1">
                <p className="text-[13px] font-medium text-amber-900 dark:text-amber-200">需要你的授权</p>
                <p className="text-[12px] text-amber-800/80 dark:text-amber-200/70 mt-0.5">
                  {ctx.cellCount
                    ? `本次操作将修改原文件中的 ${ctx.cellCount} 个单元格。`
                    : "本次操作将修改原文件，需要你确认后继续。"}
                </p>
              </div>
              <button
                type="button"
                onClick={(e) => {
                  e.stopPropagation();
                  const overlay = document.querySelector<HTMLElement>("[data-slot='overlay-card']");
                  overlay?.focus();
                }}
                className="inline-flex h-11 sm:h-8 items-center justify-center rounded-lg px-3 text-[13px] font-semibold text-white bg-[var(--em-primary)] sm:flex-shrink-0 w-full sm:w-auto"
              >
                {pendingApproval ? "查看并授权" : "等待授权"}
              </button>
            </div>
          </div>
        )}

        {isError && error && !open && (
          <p className="mt-1.5 text-[12px] text-red-600 dark:text-red-400 leading-5 break-words">
            {error}
          </p>
        )}

        {open && (
          <div className="mt-2 rounded-xl border border-[var(--em-hairline)] bg-[var(--em-fill)] dark:bg-muted/20 px-3 py-2.5 space-y-2">
            <div className="flex items-center justify-between gap-2">
              <p className="text-[11px] font-medium text-muted-foreground">执行详情</p>
              <code className="font-mono text-[10px] text-muted-foreground">{name}</code>
            </div>
            {Object.keys(args).length > 0 && (
              <div>
                <p className="text-[11px] font-medium text-muted-foreground mb-1">参数</p>
                <CodeBlock
                  language="json"
                  code={JSON.stringify(args, null, isMobile ? 1 : 2)}
                  maxHeightClass="max-h-48"
                />
              </div>
            )}
            {result && (() => {
              const lang = detectResultLanguage(name, result);
              return (
                <div>
                  <p className="text-[11px] font-medium text-muted-foreground mb-1">结果</p>
                  {lang ? (
                    <CodeBlock language={lang} code={result} maxHeightClass="max-h-48" />
                  ) : (
                    <pre className="bg-background/70 rounded p-2 overflow-auto whitespace-pre-wrap max-h-48 text-[11px]">
                      {result}
                    </pre>
                  )}
                </div>
              );
            })()}
            {error && (
              <div>
                <p className="text-[11px] font-medium text-red-600 mb-1">错误</p>
                <pre className="bg-red-50/80 dark:bg-red-950/20 rounded-md p-2 overflow-auto whitespace-pre-wrap max-h-48 text-red-700 dark:text-red-300 text-[11px]">
                  {error}
                </pre>
              </div>
            )}
          </div>
        )}

        {streamingRawArgs && textDiffs.length === 0 && (
          <StreamingTextPreview toolName={name} rawArgs={streamingRawArgs} />
        )}

        {textDiffs.length > 0 && (
          <div>
            {textDiffs.map((d, i) => (
              <TextDiffView key={`${d.toolCallId}-${d.filePath}-${i}`} data={d} />
            ))}
          </div>
        )}

        {textPreview && <TextPreviewView data={textPreview} />}
        {preview && <ExcelPreviewTable data={preview} />}
        {diffs.length > 0 && (
          <div className="relative">
            {diffs.map((d, i) => (
              <ExcelDiffTable key={`${d.toolCallId}-${d.sheet}-${i}`} data={d} />
            ))}
          </div>
        )}
        {(() => {
          const mr = parseMergeResult(name, result);
          if (!mr || (!mr.outputFile && mr.sourceFiles.length === 0)) return null;
          return (
            <MergeResultCard
              sourceFiles={mr.sourceFiles}
              outputFile={mr.outputFile}
              rowsMatched={mr.rowsMatched}
              rowsAdded={mr.rowsAdded}
              rowsUnmatched={mr.rowsUnmatched}
              keyColumns={mr.keyColumns}
              joinType={mr.joinType}
            />
          );
        })()}
      </div>
    </div>
  );
});
