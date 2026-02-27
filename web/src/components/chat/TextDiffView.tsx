"use client";

import { useMemo, useState } from "react";
import { FileCode2 } from "lucide-react";
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

function parseDiffLines(hunks: string[]): DiffLine[] {
  const lines: DiffLine[] = [];
  let oldLine = 0;
  let newLine = 0;

  for (const raw of hunks) {
    if (raw.startsWith("---") || raw.startsWith("+++")) {
      lines.push({ type: "header", content: raw });
    } else if (raw.startsWith("@@")) {
      // Parse hunk header: @@ -oldStart,oldCount +newStart,newCount @@
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
      // Context line (may start with a space)
      const content = raw.startsWith(" ") ? raw.slice(1) : raw;
      lines.push({ type: "context", content, oldLineNo: oldLine, newLineNo: newLine });
      oldLine++;
      newLine++;
    }
  }
  return lines;
}

const MAX_DISPLAY_LINES = 80;

export function TextDiffView({ data }: TextDiffViewProps) {
  const diffLines = useMemo(() => parseDiffLines(data.hunks), [data.hunks]);
  const filename = data.filePath.split("/").pop() || data.filePath;
  const [expanded, setExpanded] = useState(false);

  const hasMore = diffLines.length > MAX_DISPLAY_LINES;
  const displayLines = expanded ? diffLines : diffLines.slice(0, MAX_DISPLAY_LINES);

  return (
    <div className="mt-2 rounded-lg border border-border/60 bg-muted/30 overflow-hidden text-xs">
      {/* Header */}
      <div className="flex items-center gap-2 px-3 py-1.5 bg-muted/50 border-b border-border/40">
        <FileCode2 className="h-3.5 w-3.5 text-muted-foreground" />
        <span className="font-medium text-foreground/80 truncate" title={data.filePath}>
          {filename}
        </span>
        <div className="ml-auto flex items-center gap-2 text-[11px]">
          {data.additions > 0 && (
            <span className="text-green-600 dark:text-green-400 font-medium">+{data.additions}</span>
          )}
          {data.deletions > 0 && (
            <span className="text-red-600 dark:text-red-400 font-medium">-{data.deletions}</span>
          )}
        </div>
      </div>

      {/* Diff body */}
      <div className="overflow-x-auto">
        <table className="w-full font-mono text-[11px] leading-[1.5] border-collapse">
          <tbody>
            {displayLines.map((line, idx) => {
              if (line.type === "header") {
                return (
                  <tr key={idx} className="bg-muted/40">
                    <td className="w-8 text-right pr-1 text-muted-foreground/50 select-none border-r border-border/30" />
                    <td className="w-8 text-right pr-1 text-muted-foreground/50 select-none border-r border-border/30" />
                    <td className="pl-2 pr-3 py-0 text-muted-foreground font-semibold whitespace-pre">
                      {line.content}
                    </td>
                  </tr>
                );
              }
              if (line.type === "hunk") {
                return (
                  <tr key={idx} className="bg-blue-50/60 dark:bg-blue-950/20">
                    <td className="w-8 text-right pr-1 text-muted-foreground/50 select-none border-r border-border/30" />
                    <td className="w-8 text-right pr-1 text-muted-foreground/50 select-none border-r border-border/30" />
                    <td className="pl-2 pr-3 py-0 text-blue-600 dark:text-blue-400 whitespace-pre">
                      {line.content}
                    </td>
                  </tr>
                );
              }

              const bgClass =
                line.type === "added"
                  ? "bg-green-50/80 dark:bg-green-950/20"
                  : line.type === "deleted"
                    ? "bg-red-50/80 dark:bg-red-950/20"
                    : "";

              const textClass =
                line.type === "added"
                  ? "text-green-800 dark:text-green-300"
                  : line.type === "deleted"
                    ? "text-red-800 dark:text-red-300"
                    : "text-foreground/70";

              const prefix =
                line.type === "added" ? "+" : line.type === "deleted" ? "-" : " ";

              return (
                <tr key={idx} className={bgClass}>
                  <td className="w-8 text-right pr-1 text-muted-foreground/40 select-none border-r border-border/30">
                    {line.type !== "added" ? line.oldLineNo : ""}
                  </td>
                  <td className="w-8 text-right pr-1 text-muted-foreground/40 select-none border-r border-border/30">
                    {line.type !== "deleted" ? line.newLineNo : ""}
                  </td>
                  <td className={`pl-1 pr-3 py-0 whitespace-pre ${textClass}`}>
                    <span className="inline-block w-3 text-center select-none opacity-60">{prefix}</span>
                    {line.content}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {/* Footer */}
      {(hasMore || data.truncated) && (
        <div className="flex items-center justify-between px-3 py-1 text-[10px] text-muted-foreground border-t border-border/30 bg-muted/30">
          <span>
            {hasMore && !expanded
              ? `显示前 ${MAX_DISPLAY_LINES} 行，共 ${diffLines.length} 行差异`
              : hasMore && expanded
                ? `共 ${diffLines.length} 行差异`
                : "差异内容已截断"}
          </span>
          {hasMore && (
            <button
              type="button"
              onClick={() => setExpanded((v) => !v)}
              className="text-[var(--em-primary)] hover:text-[var(--em-primary-dark)] transition-colors cursor-pointer font-medium"
            >
              {expanded ? "收起" : "展开全部"}
            </button>
          )}
        </div>
      )}
    </div>
  );
}
