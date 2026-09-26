"use client";

import React, { useState, useEffect, useRef, useMemo, useId } from "react";
import { ChevronDown } from "lucide-react";
import { toolIcon, toolStatusIconClass } from "@/lib/tool-icons";
import { useIsMobile } from "@/hooks/use-mobile";
import { useExcelStore } from "@/stores/excel-store";
import { useChatStore } from "@/stores/chat-store";
import { cancelToolCall } from "@/lib/api";
import type { AssistantBlock } from "@/lib/types";
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
  "observe_spreadsheet",
  "analyze_spreadsheet",
  "compare_spreadsheets",
]);
const EXCEL_WRITE_TOOLS = new Set([
  "apply_spreadsheet_changes",
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
  executionId?: string;
  executionState?: string;
  name: string;
  args: Record<string, unknown>;
  status: "running" | "success" | "error" | "pending" | "streaming";
  result?: string;
  error?: string;
  isLast?: boolean;
  parentCallId?: string;
  nested?: boolean;
}

export function ToolCallCancelButton({ executionId, executionState }: { executionId?: string; executionState?: string }) {
  const sessionId = useChatStore((s) => s.loadedSessionId);
  const [requested, setRequested] = useState(false);
  const [error, setError] = useState("");
  const currentId = useRef(executionId);
  useEffect(() => {
    currentId.current = executionId;
    return () => { currentId.current = undefined; };
  }, [executionId]);
  const active = executionState === "queued" || executionState === "running" || executionState === "cancelling";
  if (!executionId || !sessionId || !active) return null;
  const cancelling = requested || executionState === "cancelling";
  async function cancel() {
    if (!executionId || !sessionId) return;
    const id = executionId;
    setRequested(true);
    setError("");
    try {
      const result = await cancelToolCall(sessionId, id);
      const store = useChatStore.getState();
      if (currentId.current !== id || store.loadedSessionId !== sessionId) return;
      for (const message of store.messages) {
        if (message.role !== "assistant" || !message.blocks.some((b) => b.type === "tool_call" && b.executionId === id)) continue;
        store.updateAssistantMessage(message.id, (m) => ({ ...m, blocks: m.blocks.map((b) => {
          if (b.type !== "tool_call" || b.executionId !== id || b.status === "success" || b.status === "error") return b;
          return { ...b, executionState: result.status,
            status: result.status === "completed" ? "success" : ["cancelled", "failed"].includes(result.status) ? "error" : b.status } as AssistantBlock;
        }) }));
      }
    } catch (err) {
      if (currentId.current === id) {
        setRequested(false);
        setError(err instanceof Error ? err.message : "取消失败，请重试");
      }
    }
  }
  return <div className="mt-1.5 text-xs text-muted-foreground">
    <button type="button" disabled={cancelling} onClick={cancel}
      className="rounded px-2 py-1 hover:bg-muted disabled:opacity-60">
      {cancelling ? "等待当前操作收尾…" : "取消此操作"}
    </button>
    {error && <span className="ml-2 text-red-600" role="alert">{error}</span>}
  </div>;
}

export const ToolCallCard = React.memo(function ToolCallCard({
  toolCallId, name, args, status, result, error, isLast = true,
  parentCallId, nested = false, executionId, executionState,
}: ToolCallCardProps) {
  const isMobile = useIsMobile();
  const [open, setOpen] = useState(false);
  const [hasOpened, setHasOpened] = useState(false);
  const detailsId = useId();

  const startRef = useRef<number | null>(null);
  const [elapsed, setElapsed] = useState(0);
  const isStreaming = (status as string) === "streaming";
  const isRunning = status === "running" || isStreaming;
  const [wasRunning, setWasRunning] = useState(isRunning);
  if (wasRunning !== isRunning) {
    setWasRunning(isRunning);
    setElapsed(0);
  }
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

  const isCancelled = executionState === "cancelled";
  const isError = status === "error" && !isCancelled;
  const isPending = status === "pending" && executionState !== "queued";
  const title = toolActionTitle(name, args);
  const ctx = extractToolContext(args);
  const contextLine = formatToolContextLine(ctx);

  const node = React.createElement(toolIcon(name), {
    className: `h-4 w-4 ${isCancelled ? "text-muted-foreground" : toolStatusIconClass(isStreaming ? "running" : status)}`,
  });

  const elapsedLabel = isRunning && elapsed > 0 ? `${elapsed}s` : null;
  const showApprovalCta = isPending && isWriteTool(name);
  const hasArgs = Object.keys(args).length > 0;

  function toggleDetails() {
    setHasOpened(true);
    setOpen((value) => !value);
  }

  return (
    <div
      className={`flex gap-2.5 ${nested ? "ml-3 pl-2 border-l border-[var(--em-hairline)]" : ""}`}
      data-tool-call-id={toolCallId || undefined}
      data-parent-call-id={parentCallId || undefined}
      data-nested-tool={nested ? "true" : undefined}
    >
      <div className="flex w-4 flex-col items-center flex-shrink-0">
        <div className="mt-2">{node}</div>
        {!isLast && <div className="mt-1 w-px flex-1 bg-[#C9D1CB] dark:bg-muted-foreground/30 min-h-[12px]" />}
      </div>

      <div className="tool-call-card-content min-w-0 flex-1 pb-1">
        <button
          type="button"
          onClick={toggleDetails}
          aria-expanded={open}
          aria-controls={detailsId}
          className="tool-call-toggle group/step flex w-full items-start gap-2 rounded-lg px-2 py-1.5 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--em-primary-alpha-30)]"
        >
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5">
              <span className="min-w-0 break-words text-[13px] font-medium text-foreground leading-5">{title}</span>
              {nested && (
                <span className="text-[10px] font-medium text-muted-foreground">子调用</span>
              )}
              {executionState === "queued" && <span className="text-[10px] text-muted-foreground">等待执行</span>}
              {isCancelled && <span className="text-[10px] text-muted-foreground">已取消</span>}
              {isPending && (
                <span className="text-[10px] font-medium text-amber-700 dark:text-amber-400">等待授权</span>
              )}
              {isError && (
                <span className="text-[10px] font-medium text-red-600">失败</span>
              )}
            </div>
            {contextLine && (
              <p className="text-[12px] text-muted-foreground mt-0.5 leading-4 [overflow-wrap:anywhere]">
                {contextLine}
              </p>
            )}
            {isRunning && toolProgress?.message && (
              <p className="text-[11px] text-muted-foreground mt-0.5 truncate">{toolProgress.message}</p>
            )}
            {isError && error && (
              <p className="mt-1 line-clamp-2 text-[12px] text-red-600 dark:text-red-400 leading-5 [overflow-wrap:anywhere]">
                {error}
              </p>
            )}
          </div>
          <span className="flex items-center gap-1.5 flex-shrink-0 -my-0.5">
            {elapsedLabel && (
              <span className="text-[11px] tabular-nums text-muted-foreground">{elapsedLabel}</span>
            )}
            <span className="tool-call-chevron-wrap flex h-6 w-6 items-center justify-center rounded-md">
              <ChevronDown aria-hidden="true" className="tool-call-chevron h-3.5 w-3.5" />
            </span>
          </span>
        </button>

        <ToolCallCancelButton key={executionId} executionId={executionId} executionState={executionState} />

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

        {/* Keep the zero-height shell mounted so the first click transitions too.
            Only the clipping wrapper participates in the grid animation. */}
        <div
          id={detailsId}
          role="region"
          aria-label={`${title}执行详情`}
          aria-hidden={!open}
          inert={!open}
          className={`tool-call-details ${open ? "tool-call-details-open" : ""}`}
        >
          <div className="tool-call-details-clip">
            {hasOpened && (
              <div className="tool-call-details-panel">
                <div className="flex min-w-0 items-center justify-between gap-3 border-b border-[var(--em-hairline)] pb-3">
                  <p className="shrink-0 text-[11px] font-medium text-muted-foreground">执行详情</p>
                  <code title={name} className="min-w-0 truncate rounded-md bg-[var(--em-fill)] px-2 py-1 font-mono text-[10px] text-muted-foreground">{name}</code>
                </div>
                <div className="grid min-w-0 gap-3 pt-3">
                  {hasArgs && (
                    <CodeBlock
                      label="输入参数"
                      language="json"
                      code={JSON.stringify(args, null, isMobile ? 1 : 2)}
                      maxHeightClass="max-h-56"
                    />
                  )}
                  {result && (() => {
                    const lang = detectResultLanguage(name, result);
                    return lang ? (
                      <CodeBlock label="执行结果" language={lang} code={result} maxHeightClass="max-h-64" />
                    ) : (
                      <div className="tool-call-output">
                        <p className="tool-call-output-label">执行结果</p>
                        <pre className="tool-call-plain-result max-h-64">{result}</pre>
                      </div>
                    );
                  })()}
                  {error && (
                    <div className="tool-call-output tool-call-output-error">
                      <p className="tool-call-output-label">错误信息</p>
                      <pre className="tool-call-plain-result max-h-56">{error}</pre>
                    </div>
                  )}
                  {!hasArgs && !result && !error && (
                    <p className="px-1 py-2 text-[12px] leading-5 text-muted-foreground">
                      {isRunning ? "正在执行，结果将在完成后显示。" : status === "pending" ? "等待执行，暂时没有详情。" : "本次操作没有返回详细内容。"}
                    </p>
                  )}
                </div>
              </div>
            )}
          </div>
        </div>

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
