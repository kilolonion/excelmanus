"use client";

import { useMemo } from "react";
import { Settings, Loader2 } from "lucide-react";
import { ScrollablePreview } from "./ScrollablePreview";

interface StreamingTextPreviewProps {
  toolName: string;
  rawArgs: string;
}

// ── JSON 流式解析工具 ─────────────────────────────────────

/**
 * 从流式累积的 JSON 字符串中提取指定 key 的字符串值。
 * 处理 JSON 转义字符，支持值尚未闭合（流式截断）的情况。
 */
function extractJsonStringValue(rawArgs: string, key: string): string | null {
  const patterns = [`"${key}":"`, `"${key}": "`, `"${key}" : "`];
  let startIdx = -1;
  let patternLen = 0;
  for (const p of patterns) {
    const idx = rawArgs.indexOf(p);
    if (idx !== -1) {
      startIdx = idx;
      patternLen = p.length;
      break;
    }
  }
  if (startIdx === -1) return null;

  const afterQuote = rawArgs.slice(startIdx + patternLen);
  let result = "";
  let i = 0;
  while (i < afterQuote.length) {
    const ch = afterQuote[i];
    if (ch === '"') break;
    if (ch === "\\") {
      const next = afterQuote[i + 1];
      if (next === "n") { result += "\n"; i += 2; continue; }
      if (next === "t") { result += "\t"; i += 2; continue; }
      if (next === "r") { result += "\r"; i += 2; continue; }
      if (next === '"') { result += '"'; i += 2; continue; }
      if (next === "\\") { result += "\\"; i += 2; continue; }
      if (next === "/") { result += "/"; i += 2; continue; }
      result += ch;
      i += 1;
      continue;
    }
    result += ch;
    i += 1;
  }
  return result;
}

function extractFilePath(rawArgs: string): string | null {
  return extractJsonStringValue(rawArgs, "file_path");
}

// ── 通用：提取写入内容 ──────────────────────────────────

function extractWriteContent(rawArgs: string, toolName: string): string | null {
  const key = toolName === "edit_text_file" ? "new_string" : "content";
  return extractJsonStringValue(rawArgs, key);
}

// ── edit_text_file 专用：提取 old + new ─────────────────

interface EditDiffParts {
  oldString: string | null;
  newString: string | null;
}

function extractEditDiffParts(rawArgs: string): EditDiffParts {
  return {
    oldString: extractJsonStringValue(rawArgs, "old_string"),
    newString: extractJsonStringValue(rawArgs, "new_string"),
  };
}

// ── 子组件：流式 Diff 预览（edit_text_file） ──────────────

function StreamingDiffBody({
  filePath,
  oldString,
  newString,
}: {
  filePath: string | null;
  oldString: string | null;
  newString: string | null;
}) {
  const oldLines = oldString ? oldString.split("\n") : [];
  const newLines = newString ? newString.split("\n") : [];
  const deletions = oldLines.length;
  const additions = newLines.length;
  const filename = filePath ? filePath.split("/").pop() || filePath : null;
  const isNewStringStreaming = newString !== null;
  const isWaiting = !oldString && !newString;

  if (isWaiting) {
    return (
      <div className="flex items-center gap-2 px-3 py-2 text-xs text-muted-foreground">
        <Loader2 className="h-3 w-3 animate-spin" />
        <span>正在生成内容...</span>
      </div>
    );
  }

  return (
    <div className="mt-2 rounded-lg border border-border/60 overflow-hidden text-xs">
      {/* ── 文件头（与 TextDiffView 风格一致） ── */}
      <div className="flex items-center gap-2 px-3 py-1.5 bg-muted/40 select-none">
        <Settings className="h-3.5 w-3.5 text-muted-foreground/50 flex-shrink-0" />
        {filename && (
          <span className="font-medium text-foreground/80 truncate text-[12px]" title={filePath ?? ""}>
            {filename}
          </span>
        )}
        <div className="ml-auto flex items-center gap-1 text-[11px] font-semibold">
          {additions > 0 && (
            <span className="text-green-600 dark:text-green-400">+{additions}</span>
          )}
          {deletions > 0 && (
            <span className="text-red-600 dark:text-red-400 ml-0.5">-{deletions}</span>
          )}
          <Loader2 className="h-3 w-3 animate-spin text-muted-foreground/40 ml-1" />
        </div>
      </div>

      {/* ── Diff 内容 ── */}
      <div className="border-t border-border/30">
        <ScrollablePreview collapsedHeight={200} expandedHeight={420} autoScroll>
          <table className="w-full font-mono text-[11px] leading-[1.6] border-collapse">
            <tbody>
              {/* 删除行（old_string） */}
              {oldLines.map((line, i) => (
                <tr key={`d-${i}`} className="bg-red-50 dark:bg-red-950/25">
                  <td className="w-8 text-right pr-1.5 text-[10px] text-muted-foreground/25 select-none bg-red-100/50 dark:bg-red-900/15">
                    {i + 1}
                  </td>
                  <td className="w-8 select-none" />
                  <td className="pl-2 pr-3 py-0 whitespace-pre text-red-900 dark:text-red-200">
                    {line}
                  </td>
                </tr>
              ))}

              {/* 新增行（new_string，流式追加） */}
              {newLines.map((line, i) => (
                <tr key={`a-${i}`} className="bg-green-50 dark:bg-green-950/25">
                  <td className="w-8 select-none" />
                  <td className="w-8 text-right pr-1.5 text-[10px] text-muted-foreground/25 select-none bg-green-100/50 dark:bg-green-900/15">
                    {i + 1}
                  </td>
                  <td className="pl-2 pr-3 py-0 whitespace-pre text-green-900 dark:text-green-200">
                    {line}
                    {/* 最后一行：闪烁光标 */}
                    {i === newLines.length - 1 && (
                      <span className="inline-block w-[6px] h-[13px] bg-green-500 dark:bg-green-400 animate-pulse ml-0.5 align-middle" />
                    )}
                  </td>
                </tr>
              ))}

              {/* old_string 已到但 new_string 尚未开始 → 等待提示 */}
              {oldLines.length > 0 && !isNewStringStreaming && (
                <tr>
                  <td colSpan={3} className="py-2 text-center text-[10px] text-muted-foreground/50 italic">
                    <span className="inline-flex items-center gap-1">
                      <Loader2 className="h-3 w-3 animate-spin" />
                      等待新内容...
                    </span>
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </ScrollablePreview>
      </div>
    </div>
  );
}

// ── 子组件：流式全绿预览（write_text_file / write_plan） ──

function StreamingWriteBody({
  filePath,
  content,
}: {
  filePath: string | null;
  content: string;
}) {
  const lines = content.split("\n");
  const filename = filePath ? filePath.split("/").pop() || filePath : null;

  return (
    <div className="mt-2 rounded-lg border border-border/60 overflow-hidden text-xs">
      {/* 文件头 */}
      <div className="flex items-center gap-2 px-3 py-1.5 bg-muted/40 select-none">
        <Settings className="h-3.5 w-3.5 text-muted-foreground/50 flex-shrink-0" />
        {filename && (
          <span className="font-medium text-foreground/80 truncate text-[12px]" title={filePath ?? ""}>
            {filename}
          </span>
        )}
        <div className="ml-auto flex items-center gap-1 text-[11px] font-semibold">
          <span className="text-green-600 dark:text-green-400">+{lines.length}</span>
          <Loader2 className="h-3 w-3 animate-spin text-muted-foreground/40 ml-1" />
        </div>
      </div>

      {/* 内容 */}
      <div className="border-t border-border/30">
        <ScrollablePreview collapsedHeight={160} expandedHeight={400} autoScroll>
          <table className="w-full font-mono text-[11px] leading-[1.6] border-collapse">
            <tbody>
              {lines.map((line, i) => (
                <tr key={i} className="bg-green-50 dark:bg-green-950/25">
                  <td className="w-8 text-right pr-1.5 text-[10px] text-muted-foreground/25 select-none bg-green-100/50 dark:bg-green-900/15">
                    {i + 1}
                  </td>
                  <td className="pl-2 pr-3 py-0 whitespace-pre text-green-900 dark:text-green-200">
                    {line}
                    {i === lines.length - 1 && (
                      <span className="inline-block w-[6px] h-[13px] bg-green-500 dark:bg-green-400 animate-pulse ml-0.5 align-middle" />
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </ScrollablePreview>
      </div>
    </div>
  );
}

// ── 主组件 ──────────────────────────────────────────────

export default function StreamingTextPreview({ toolName, rawArgs }: StreamingTextPreviewProps) {
  const filePath = useMemo(() => extractFilePath(rawArgs), [rawArgs]);

  // ── edit_text_file → 流式 diff 模式 ──
  if (toolName === "edit_text_file") {
    const { oldString, newString } = extractEditDiffParts(rawArgs);
    return (
      <StreamingDiffBody
        filePath={filePath}
        oldString={oldString}
        newString={newString}
      />
    );
  }

  // ── write_text_file / write_plan → 全绿模式 ──
  const content = extractWriteContent(rawArgs, toolName);

  if (!content) {
    return (
      <div className="flex items-center gap-2 px-3 py-2 text-xs text-muted-foreground">
        <Loader2 className="h-3 w-3 animate-spin" />
        <span>正在生成内容...</span>
      </div>
    );
  }

  return <StreamingWriteBody filePath={filePath} content={content} />;
}
