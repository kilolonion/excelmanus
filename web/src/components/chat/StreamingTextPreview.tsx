"use client";

import { useMemo } from "react";
import { FileCode2, Loader2 } from "lucide-react";

interface StreamingTextPreviewProps {
  toolName: string;
  rawArgs: string;
}

/**
 * 从流式累积的 JSON 参数字符串中提取文本内容字段。
 * 针对 write_text_file/write_plan 提取 "content"，
 * 针对 edit_text_file 提取 "new_string"。
 */
function extractStreamingContent(rawArgs: string, toolName: string): string | null {
  const key = toolName === "edit_text_file" ? "new_string" : "content";
  // 查找 "key": " 或 "key":" 的位置
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

  // 从引号后开始，解码 JSON 转义字符
  const afterQuote = rawArgs.slice(startIdx + patternLen);
  let result = "";
  let i = 0;
  while (i < afterQuote.length) {
    const ch = afterQuote[i];
    if (ch === '"') break; // 遇到闭合引号，内容结束
    if (ch === "\\") {
      const next = afterQuote[i + 1];
      if (next === "n") { result += "\n"; i += 2; continue; }
      if (next === "t") { result += "\t"; i += 2; continue; }
      if (next === "r") { result += "\r"; i += 2; continue; }
      if (next === '"') { result += '"'; i += 2; continue; }
      if (next === "\\") { result += "\\"; i += 2; continue; }
      if (next === "/") { result += "/"; i += 2; continue; }
      // 不完整的转义序列 — 可能在流中截断
      result += ch;
      i += 1;
      continue;
    }
    result += ch;
    i += 1;
  }
  return result;
}

/**
 * 从流式参数中提取文件路径。
 */
function extractFilePath(rawArgs: string): string | null {
  const patterns = [`"file_path":"`, `"file_path": "`, `"file_path" : "`];
  for (const p of patterns) {
    const idx = rawArgs.indexOf(p);
    if (idx === -1) continue;
    const after = rawArgs.slice(idx + p.length);
    const endQuote = after.indexOf('"');
    if (endQuote !== -1) return after.slice(0, endQuote);
  }
  return null;
}

export default function StreamingTextPreview({ toolName, rawArgs }: StreamingTextPreviewProps) {
  const content = useMemo(() => extractStreamingContent(rawArgs, toolName), [rawArgs, toolName]);
  const filePath = useMemo(() => extractFilePath(rawArgs), [rawArgs]);

  if (!content) {
    return (
      <div className="flex items-center gap-2 px-3 py-2 text-xs text-muted-foreground">
        <Loader2 className="h-3 w-3 animate-spin" />
        <span>正在生成内容...</span>
      </div>
    );
  }

  const lines = content.split("\n");
  const lineNoWidth = String(lines.length).length;

  return (
    <div className="mt-2 rounded-md border border-green-200 dark:border-green-900 overflow-hidden text-xs">
      {/* 文件名标题栏 */}
      {filePath && (
        <div className="flex items-center gap-1.5 px-3 py-1.5 bg-green-50 dark:bg-green-950/40 border-b border-green-200 dark:border-green-900 text-green-700 dark:text-green-400">
          <FileCode2 className="h-3 w-3 flex-shrink-0" />
          <span className="font-mono truncate">{filePath}</span>
          <Loader2 className="h-3 w-3 animate-spin ml-auto flex-shrink-0" />
        </div>
      )}
      {/* 内容区域 */}
      <div className="max-h-[400px] overflow-auto bg-green-50/50 dark:bg-green-950/20">
        <table className="w-full border-collapse font-mono text-[11px] leading-[18px]">
          <tbody>
            {lines.map((line, i) => (
              <tr key={i} className="bg-green-50/80 dark:bg-green-950/30">
                <td className="select-none text-right pr-2 pl-2 text-green-400 dark:text-green-600 border-r border-green-200 dark:border-green-900 w-0 whitespace-nowrap">
                  {String(i + 1).padStart(lineNoWidth)}
                </td>
                <td className="pl-1 pr-2 text-green-800 dark:text-green-300 whitespace-pre-wrap break-all">
                  <span className="select-none text-green-400 dark:text-green-600 mr-1">+</span>
                  {line}
                  {/* 最后一行显示闪烁光标 */}
                  {i === lines.length - 1 && (
                    <span className="inline-block w-[6px] h-[13px] bg-green-500 dark:bg-green-400 animate-pulse ml-0.5 align-middle" />
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
