"use client";

import { useMemo, useState } from "react";
import { FileText, ChevronDown, ChevronUp } from "lucide-react";
import type { TextPreviewEntry } from "@/stores/excel-store";
import { ScrollablePreview } from "./ScrollablePreview";

interface TextPreviewViewProps {
  data: TextPreviewEntry;
}

/** 从文件路径推断语言标识（用于语法高亮提示） */
function inferLanguage(filePath: string): string {
  const ext = filePath.split(".").pop()?.toLowerCase() || "";
  const map: Record<string, string> = {
    py: "Python",
    js: "JavaScript",
    ts: "TypeScript",
    tsx: "TSX",
    jsx: "JSX",
    json: "JSON",
    md: "Markdown",
    yaml: "YAML",
    yml: "YAML",
    toml: "TOML",
    csv: "CSV",
    txt: "Text",
    sh: "Shell",
    bash: "Shell",
    sql: "SQL",
    html: "HTML",
    css: "CSS",
    xml: "XML",
    ini: "INI",
    cfg: "Config",
    conf: "Config",
    log: "Log",
    env: "Env",
  };
  return map[ext] || "Text";
}

const MAX_DISPLAY_LINES = 60;

export function TextPreviewView({ data }: TextPreviewViewProps) {
  const lines = useMemo(() => data.content.split("\n"), [data.content]);
  const filename = data.filePath.split("/").pop() || data.filePath;
  const language = useMemo(() => inferLanguage(data.filePath), [data.filePath]);
  const [expanded, setExpanded] = useState(false);

  const hasMore = lines.length > MAX_DISPLAY_LINES;
  const displayLines = expanded ? lines : lines.slice(0, MAX_DISPLAY_LINES);
  const lineNoWidth = String(lines.length).length;

  return (
    <div className="mt-2 rounded-lg border border-border/60 bg-muted/30 overflow-hidden text-xs">
      {/* Header */}
      <div className="flex items-center gap-2 px-3 py-1.5 bg-muted/50 border-b border-border/40">
        <FileText className="h-3.5 w-3.5 text-muted-foreground" />
        <span className="font-medium text-foreground/80 truncate" title={data.filePath}>
          {filename}
        </span>
        <span className="text-[10px] text-muted-foreground/60 px-1.5 py-px rounded bg-muted/60 font-mono">
          {language}
        </span>
        <div className="ml-auto flex items-center gap-2 text-[11px] text-muted-foreground">
          <span>{data.lineCount} 行</span>
          {data.truncated && (
            <span className="text-amber-500 dark:text-amber-400 text-[10px]">已截断</span>
          )}
        </div>
      </div>

      {/* Content */}
      <ScrollablePreview collapsedHeight={160} expandedHeight={400}>
        <div className="overflow-x-auto">
          <table className="w-full font-mono text-[11px] leading-[18px] border-collapse">
            <tbody>
              {displayLines.map((line, i) => (
                <tr key={i} className="hover:bg-muted/40 transition-colors">
                  <td className="select-none text-right pr-2 pl-2 text-muted-foreground/40 border-r border-border/30 w-0 whitespace-nowrap">
                    {String(i + 1).padStart(lineNoWidth)}
                  </td>
                  <td className="pl-2 pr-3 py-0 text-foreground/80 whitespace-pre-wrap break-all">
                    {line || "\u00A0"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </ScrollablePreview>

      {/* Footer */}
      {(hasMore || data.truncated) && (
        <div className="flex items-center justify-between px-3 py-1 text-[10px] text-muted-foreground border-t border-border/30 bg-muted/30">
          <span>
            {hasMore && !expanded
              ? `显示前 ${MAX_DISPLAY_LINES} 行，共 ${lines.length} 行`
              : data.truncated
                ? `文件内容已截断（共读取 ${data.lineCount} 行）`
                : `共 ${lines.length} 行`}
          </span>
          {hasMore && (
            <button
              type="button"
              onClick={() => setExpanded((v) => !v)}
              className="flex items-center gap-0.5 text-[var(--em-primary)] hover:text-[var(--em-primary-dark)] transition-colors cursor-pointer font-medium"
            >
              {expanded ? (
                <>
                  <ChevronUp className="h-3 w-3" />
                  收起
                </>
              ) : (
                <>
                  <ChevronDown className="h-3 w-3" />
                  展开全部
                </>
              )}
            </button>
          )}
        </div>
      )}
    </div>
  );
}
