"use client";

import { useMemo, useState, useCallback } from "react";
import { Settings, ChevronDown, ChevronUp } from "lucide-react";
import type { TextDiffEntry } from "@/stores/excel-store";

interface TextDiffViewProps {
  data: TextDiffEntry;
}

interface DiffLine {
  type: "header" | "hunk" | "added" | "deleted" | "context";
  content: string;
  oldLineNo?: number;
  newLineNo?: number;
}

/**
 * 三态展示模式：
 * - collapsed: 仅文件头（文件名 + 增删统计）
 * - preview:   文件头 + 前 N 行预览 + 渐变遮罩
 * - expanded:  完整 diff，长上下文折叠为 "N hidden lines"
 */
type DisplayMode = "collapsed" | "preview" | "expanded";

/** preview 模式最多展示的 item 数 */
const PREVIEW_ITEM_COUNT = 8;
/** 连续 context 行超过此值时折叠 */
const CONTEXT_COLLAPSE_THRESHOLD = 4;

// ── diff 解析 ──────────────────────────────────────────

function parseDiffLines(hunks: string[]): DiffLine[] {
  const lines: DiffLine[] = [];
  let oldLine = 0;
  let newLine = 0;

  for (const raw of hunks) {
    if (raw.startsWith("---") || raw.startsWith("+++")) {
      continue;
    } else if (raw.startsWith("@@")) {
      const match = raw.match(/@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/);
      if (match) {
        oldLine = parseInt(match[1], 10);
        newLine = parseInt(match[2], 10);
      }
      lines.push({ type: "hunk", content: raw });
    } else if (raw.startsWith("+")) {
      lines.push({ type: "added", content: raw.slice(1), newLineNo: newLine });
      newLine++;
    } else if (raw.startsWith("-")) {
      lines.push({ type: "deleted", content: raw.slice(1), oldLineNo: oldLine });
      oldLine++;
    } else {
      const content = raw.startsWith(" ") ? raw.slice(1) : raw;
      lines.push({ type: "context", content, oldLineNo: oldLine, newLineNo: newLine });
      oldLine++;
      newLine++;
    }
  }
  return lines;
}

// ── 展示项：行 | 折叠标记 ──────────────────────────────

type DisplayItem =
  | { kind: "line"; line: DiffLine }
  | { kind: "hidden"; count: number };

function buildDisplayItems(diffLines: DiffLine[]): DisplayItem[] {
  const items: DisplayItem[] = [];
  let i = 0;

  while (i < diffLines.length) {
    const line = diffLines[i];

    if (line.type === "context") {
      const start = i;
      while (i < diffLines.length && diffLines[i].type === "context") i++;
      const count = i - start;

      if (count <= CONTEXT_COLLAPSE_THRESHOLD) {
        for (let j = start; j < i; j++) {
          items.push({ kind: "line", line: diffLines[j] });
        }
      } else {
        items.push({ kind: "line", line: diffLines[start] });
        items.push({ kind: "hidden", count: count - 2 });
        items.push({ kind: "line", line: diffLines[i - 1] });
      }
    } else {
      items.push({ kind: "line", line });
      i++;
    }
  }
  return items;
}

// ── 组件 ────────────────────────────────────────────────

export function TextDiffView({ data }: TextDiffViewProps) {
  const diffLines = useMemo(() => parseDiffLines(data.hunks), [data.hunks]);
  const displayItems = useMemo(() => buildDisplayItems(diffLines), [diffLines]);
  const filename = data.filePath.split("/").pop() || data.filePath;
  const [mode, setMode] = useState<DisplayMode>("collapsed");

  const previewItems = useMemo(
    () => displayItems.slice(0, PREVIEW_ITEM_COUNT),
    [displayItems],
  );
  const hasMoreThanPreview = displayItems.length > PREVIEW_ITEM_COUNT;

  const handleHeaderClick = useCallback(() => {
    setMode((m) => {
      if (m === "collapsed") return hasMoreThanPreview ? "preview" : "expanded";
      return "collapsed";
    });
  }, [hasMoreThanPreview]);

  const itemsToRender = mode === "preview" ? previewItems : displayItems;

  // ── 渲染单行 ──

  const renderItem = (item: DisplayItem, idx: number) => {
    if (item.kind === "hidden") {
      return (
        <tr key={`h-${idx}`}>
          <td
            colSpan={3}
            className="py-1.5 text-center text-[10px] text-muted-foreground/50 bg-muted/20 select-none italic border-y border-border/15"
          >
            {item.count} hidden lines
          </td>
        </tr>
      );
    }

    const line = item.line;

    if (line.type === "hunk") {
      return (
        <tr key={idx} className="bg-blue-50/40 dark:bg-blue-950/15">
          <td className="w-8 select-none" />
          <td className="w-8 select-none" />
          <td className="px-3 py-0.5 text-blue-500/60 dark:text-blue-400/40 text-[10px] whitespace-pre">
            {line.content}
          </td>
        </tr>
      );
    }

    const isAdded = line.type === "added";
    const isDeleted = line.type === "deleted";

    const rowBg = isAdded
      ? "bg-green-50 dark:bg-green-950/25"
      : isDeleted
        ? "bg-red-50 dark:bg-red-950/25"
        : "";

    const textColor = isAdded
      ? "text-green-900 dark:text-green-200"
      : isDeleted
        ? "text-red-900 dark:text-red-200"
        : "text-foreground/60";

    const lineNoBg = isAdded
      ? "bg-green-100/50 dark:bg-green-900/15"
      : isDeleted
        ? "bg-red-100/50 dark:bg-red-900/15"
        : "";

    return (
      <tr key={idx} className={rowBg}>
        <td className={`w-8 text-right pr-1.5 text-[10px] text-muted-foreground/25 select-none ${lineNoBg}`}>
          {!isAdded ? line.oldLineNo : ""}
        </td>
        <td className={`w-8 text-right pr-1.5 text-[10px] text-muted-foreground/25 select-none ${lineNoBg}`}>
          {!isDeleted ? line.newLineNo : ""}
        </td>
        <td className={`pl-2 pr-3 py-0 whitespace-pre ${textColor}`}>
          {line.content}
        </td>
      </tr>
    );
  };

  return (
    <div className="mt-2 rounded-lg border border-border/60 overflow-hidden text-xs">
      {/* ── 文件头 ── */}
      <div
        className="flex items-center gap-2 px-3 py-1.5 bg-muted/40 cursor-pointer select-none hover:bg-muted/60 transition-colors"
        onClick={handleHeaderClick}
      >
        <Settings className="h-3.5 w-3.5 text-muted-foreground/50 flex-shrink-0" />
        <span
          className="font-medium text-foreground/80 truncate text-[12px]"
          title={data.filePath}
        >
          {filename}
        </span>
        <div className="ml-auto flex items-center gap-1 text-[11px] font-semibold">
          {data.additions > 0 && (
            <span className="text-green-600 dark:text-green-400">+{data.additions}</span>
          )}
          {data.deletions > 0 && (
            <span className="text-red-600 dark:text-red-400 ml-0.5">-{data.deletions}</span>
          )}
        </div>
      </div>

      {/* ── Diff 内容区 ── */}
      {mode !== "collapsed" && (
        <>
          <div className="relative border-t border-border/30">
            <div className="overflow-x-auto">
              <table className="w-full font-mono text-[11px] leading-[1.6] border-collapse">
                <tbody>{itemsToRender.map(renderItem)}</tbody>
              </table>
            </div>

            {/* preview 模式：底部渐变遮罩 */}
            {mode === "preview" && hasMoreThanPreview && (
              <div className="absolute bottom-0 inset-x-0 pointer-events-none">
                <div className="h-10 bg-gradient-to-t from-background via-background/80 to-transparent" />
              </div>
            )}
          </div>

          {/* ── 底部控制栏 ── */}
          {mode === "preview" && hasMoreThanPreview && (
            <div
              className="flex items-center justify-center py-1 border-t border-border/20 bg-muted/20 cursor-pointer hover:bg-muted/40 transition-colors"
              onClick={() => setMode("expanded")}
            >
              <ChevronDown className="h-3.5 w-3.5 text-muted-foreground/40" />
            </div>
          )}

          {mode === "expanded" && (
            <div
              className="flex items-center justify-center py-1 border-t border-border/20 bg-muted/20 cursor-pointer hover:bg-muted/40 transition-colors"
              onClick={() => setMode("collapsed")}
            >
              <ChevronDown className="h-3.5 w-3.5 text-muted-foreground/40" />
            </div>
          )}
        </>
      )}
    </div>
  );
}
