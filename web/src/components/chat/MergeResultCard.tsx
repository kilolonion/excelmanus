"use client";

import { ArrowLeftRight, FileSpreadsheet, ArrowRight, Check, X } from "lucide-react";
import { useExcelStore } from "@/stores/excel-store";

interface MergeResultCardProps {
  sourceFiles: string[];
  outputFile: string;
  rowsMatched: number;
  rowsAdded: number;
  rowsUnmatched: number;
  keyColumns: string[];
  joinType: string;
}

export function MergeResultCard({
  sourceFiles,
  outputFile,
  rowsMatched,
  rowsAdded,
  rowsUnmatched,
  keyColumns,
  joinType,
}: MergeResultCardProps) {
  const openCompare = useExcelStore((s) => s.openCompare);
  const openPanel = useExcelStore((s) => s.openPanel);

  const totalRows = rowsMatched + rowsAdded + rowsUnmatched;
  const matchedPct = totalRows > 0 ? (rowsMatched / totalRows) * 100 : 0;
  const addedPct = totalRows > 0 ? (rowsAdded / totalRows) * 100 : 0;
  const unmatchedPct = totalRows > 0 ? (rowsUnmatched / totalRows) * 100 : 0;

  const fileNameA = sourceFiles[0]?.split("/").pop() || "";
  const fileNameB = sourceFiles[1]?.split("/").pop() || "";
  const outputName = outputFile.split("/").pop() || outputFile;

  const joinLabel: Record<string, string> = {
    left: "左连接",
    right: "右连接",
    inner: "内连接",
    outer: "全外连接",
  };

  return (
    <div className="my-2 rounded-lg border border-border/80 overflow-hidden text-xs shadow-sm">
      {/* 标题栏 */}
      <div className="flex items-center gap-2 px-3 py-2 bg-muted/30 border-b border-border">
        <FileSpreadsheet className="h-4 w-4 text-emerald-600 dark:text-emerald-400" />
        <span className="font-medium text-foreground/90">跨文件合并完成</span>
        {joinType && (
          <span className="text-[9px] px-1.5 py-px rounded-full bg-muted text-muted-foreground">
            {joinLabel[joinType] || joinType}
          </span>
        )}
      </div>

      {/* 文件流程图 */}
      <div className="px-3 py-2.5 flex items-center gap-2 flex-wrap text-[11px]">
        {fileNameA && (
          <span className="inline-flex items-center gap-1 px-2 py-1 rounded-md bg-blue-50 dark:bg-blue-950/30 text-blue-700 dark:text-blue-300 border border-blue-100 dark:border-blue-900/40">
            <FileSpreadsheet className="h-3 w-3" />
            {fileNameA}
          </span>
        )}
        {fileNameB && (
          <>
            <span className="text-muted-foreground/50">+</span>
            <span className="inline-flex items-center gap-1 px-2 py-1 rounded-md bg-green-50 dark:bg-green-950/30 text-green-700 dark:text-green-300 border border-green-100 dark:border-green-900/40">
              <FileSpreadsheet className="h-3 w-3" />
              {fileNameB}
            </span>
          </>
        )}
        <ArrowRight className="h-3.5 w-3.5 text-muted-foreground/40" />
        <span className="inline-flex items-center gap-1 px-2 py-1 rounded-md font-medium border border-border"
          style={{ backgroundColor: "var(--em-primary-alpha-10)", color: "var(--em-primary)" }}>
          <FileSpreadsheet className="h-3 w-3" />
          {outputName}
        </span>
      </div>

      {/* 关键列 */}
      {keyColumns.length > 0 && (
        <div className="px-3 pb-1.5 text-[10px] text-muted-foreground/70">
          关键列: <span className="font-mono text-foreground/60">{keyColumns.join(", ")}</span>
        </div>
      )}

      {/* 统计进度条 */}
      {totalRows > 0 && (
        <div className="px-3 pb-2.5">
          <div className="flex items-center gap-1.5 mb-1">
            <div className="flex-1 h-2 rounded-full bg-muted/60 overflow-hidden flex">
              {rowsMatched > 0 && (
                <div className="h-full bg-green-500 transition-all" style={{ width: `${matchedPct}%` }} />
              )}
              {rowsAdded > 0 && (
                <div className="h-full bg-blue-500 transition-all" style={{ width: `${addedPct}%` }} />
              )}
              {rowsUnmatched > 0 && (
                <div className="h-full bg-amber-400 transition-all" style={{ width: `${unmatchedPct}%` }} />
              )}
            </div>
          </div>
          <div className="flex items-center gap-3 text-[10px]">
            {rowsMatched > 0 && (
              <span className="flex items-center gap-1">
                <Check className="h-3 w-3 text-green-500" />
                <span className="text-green-600 dark:text-green-400 font-medium">{rowsMatched.toLocaleString()}</span>
                <span className="text-muted-foreground/60">匹配</span>
              </span>
            )}
            {rowsAdded > 0 && (
              <span className="flex items-center gap-1">
                <span className="w-1.5 h-1.5 rounded-full bg-blue-500" />
                <span className="text-blue-600 dark:text-blue-400 font-medium">{rowsAdded.toLocaleString()}</span>
                <span className="text-muted-foreground/60">新增</span>
              </span>
            )}
            {rowsUnmatched > 0 && (
              <span className="flex items-center gap-1">
                <X className="h-3 w-3 text-amber-500" />
                <span className="text-amber-600 dark:text-amber-400 font-medium">{rowsUnmatched.toLocaleString()}</span>
                <span className="text-muted-foreground/60">未匹配</span>
              </span>
            )}
          </div>
        </div>
      )}

      {/* 操作按钮 */}
      <div className="flex items-center gap-2 px-3 py-2 border-t border-border bg-muted/20">
        {sourceFiles.length >= 2 && (
          <button
            onClick={() => openCompare(sourceFiles[0], sourceFiles[1])}
            className="flex items-center gap-1 px-2.5 py-1 rounded text-[10px] font-medium transition-colors hover:opacity-80"
            style={{ backgroundColor: "var(--em-primary-alpha-10)", color: "var(--em-primary)" }}
          >
            <ArrowLeftRight className="h-3 w-3" />
            对比源文件
          </button>
        )}
        {outputFile && (
          <button
            onClick={() => openPanel(outputFile)}
            className="flex items-center gap-1 px-2.5 py-1 rounded text-[10px] font-medium text-muted-foreground hover:text-foreground hover:bg-muted transition-colors"
          >
            <FileSpreadsheet className="h-3 w-3" />
            查看结果
          </button>
        )}
      </div>
    </div>
  );
}
