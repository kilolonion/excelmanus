"use client";

import { useCallback } from "react";
import { useExcelStore } from "@/stores/excel-store";
import { normalizeExcelPath } from "@/lib/api";
import { FilePathLink, isFilePath } from "./FilePathLink";
import { isCodeFile } from "./CodePreviewModal";

const EXCEL_EXTS = new Set([".xlsx", ".xls", ".csv"]);

/**
 * Regex matching @type:value and @type:value[RangeSpec] mentions,
 * aligned with backend excelmanus/mentions/parser.py _MENTION_PATTERN.
 *
 * Also matches bare @filename style mentions (legacy / ChatInput shorthand).
 */
const MENTION_RE =
  /@(?:(file|folder|skill|mcp|tool):([^\s,;!?\[\]]+)(?:\[([^\]]+)\])?)(?=\s|$|[,;!?])/gi;

/**
 * Bare @filename.ext pattern (used by ChatInput when user drops/picks a file).
 * Only matches filenames with a dot-extension.
 */
const BARE_FILE_RE = /@([\w./-]+\.[\w]+)(?=\s|$|[,;!?])/g;

/**
 * Bare file path pattern (no @ prefix) — matches paths like:
 * output.xlsx, ./data/result.csv, path/to/file.pdf
 * Must have a recognizable extension and not be inside backticks (handled by MdCode).
 */
const BARE_PATH_RE =
  /(?:^|(?<=\s|[：:"'（(]))(\.{0,2}\/)?([\w\u4e00-\u9fff][\w\u4e00-\u9fff./\\~ -]*\.(?:xlsx|xls|csv|tsv|pdf|zip|tar|gz|docx|pptx|txt|json|xml|html|md))(?=\s|$|[,;!?。，；！？：:）)"'])/gi;

function isExcel(name: string): boolean {
  const dot = name.lastIndexOf(".");
  if (dot < 0) return false;
  return EXCEL_EXTS.has(name.slice(dot).toLowerCase());
}

interface MentionHighlighterProps {
  text: string;
  className?: string;
}

interface MentionToken {
  start: number;
  end: number;
  raw: string;
  kind: string;       // "file" | "folder" | "skill" | "mcp" | "tool" | "bare-file"
  value: string;       // 文件名 / 技能名等
  rangeSpec?: string;  // 例如 "Sheet1!A1:C10"
}

function extractMentions(text: string): MentionToken[] {
  const tokens: MentionToken[] = [];
  const seen = new Set<string>(); // 按起始位置去重

  // 类型化提及：@file:xxx、@skill:xxx 等
  MENTION_RE.lastIndex = 0;
  let m: RegExpExecArray | null;
  while ((m = MENTION_RE.exec(text)) !== null) {
    const key = `${m.index}`;
    if (seen.has(key)) continue;
    seen.add(key);
    tokens.push({
      start: m.index,
      end: m.index + m[0].length,
      raw: m[0],
      kind: m[1].toLowerCase(),
      value: m[2],
      rangeSpec: m[3] || undefined,
    });
  }

  // 裸 @文件名.扩展名 提及
  BARE_FILE_RE.lastIndex = 0;
  while ((m = BARE_FILE_RE.exec(text)) !== null) {
    const key = `${m.index}`;
    if (seen.has(key)) continue;
    seen.add(key);
    tokens.push({
      start: m.index,
      end: m.index + m[0].length,
      raw: m[0],
      kind: "bare-file",
      value: m[1],
    });
  }

  // 裸文件路径（无 @ 前缀），如 output.xlsx、./data/result.csv
  BARE_PATH_RE.lastIndex = 0;
  while ((m = BARE_PATH_RE.exec(text)) !== null) {
    // 完整匹配 = 可选前缀(m[1]) + 文件名(m[2])，但 m[0] 不含 lookbehind 捕获
    const fullPath = (m[1] || "") + m[2];
    const startIdx = m.index + m[0].length - fullPath.length;
    const key = `${startIdx}`;
    if (seen.has(key)) continue;
    // 跳过已经被 @ 提及覆盖的区间
    const overlaps = tokens.some((t) => startIdx < t.end && (startIdx + fullPath.length) > t.start);
    if (overlaps) continue;
    if (!isFilePath(fullPath)) continue;
    seen.add(key);
    tokens.push({
      start: startIdx,
      end: startIdx + fullPath.length,
      raw: fullPath,
      kind: "path",
      value: fullPath,
    });
  }

  tokens.sort((a, b) => a.start - b.start);
  return tokens;
}

/**
 * Renders text with blue-highlighted @mention tokens.
 * Excel file mentions are clickable and open the side panel.
 */
export function MentionHighlighter({ text, className }: MentionHighlighterProps) {
  const openPanel = useExcelStore((s) => s.openPanel);
  const addRecentFile = useExcelStore((s) => s.addRecentFile);

  const handleExcelClick = useCallback(
    (value: string, rangeSpec?: string) => {
      const normalized = normalizeExcelPath(value);
      const filename = normalized.split("/").pop() || normalized;

      // 按规范化路径查找已有文件，避免重复创建
      const recentFiles = useExcelStore.getState().recentFiles;
      const existing = recentFiles.find(
        (f) => normalizeExcelPath(f.path) === normalized,
      );
      const resolvedPath = existing ? existing.path : normalized;

      addRecentFile({ path: resolvedPath, filename });
      const sheet = rangeSpec?.split("!")[0];
      openPanel(resolvedPath, sheet);
    },
    [openPanel, addRecentFile],
  );

  const tokens = extractMentions(text);

  if (tokens.length === 0) {
    return <span className={className}>{text}</span>;
  }

  const parts: React.ReactNode[] = [];
  let cursor = 0;

  for (const token of tokens) {
    // 该 token 之前的文本
    if (token.start > cursor) {
      parts.push(
        <span key={`t-${cursor}`}>{text.slice(cursor, token.start)}</span>
      );
    }

    // 裸文件路径 → 渲染为 FilePathLink
    if (token.kind === "path") {
      parts.push(
        <FilePathLink key={`m-${token.start}`} filePath={token.value} variant="text">
          {token.raw}
        </FilePathLink>
      );
      cursor = token.end;
      continue;
    }

    const isFileMention = token.kind === "file" || token.kind === "bare-file";
    const isExcelMention = isFileMention && isExcel(token.value);
    const isPreviewable = isFileMention && !isExcelMention && isCodeFile(token.value);

    // 可预览的文本/代码文件 → 通过 FilePathLink 打开预览弹窗
    if (isPreviewable) {
      parts.push(
        <FilePathLink key={`m-${token.start}`} filePath={token.value} variant="text">
          {token.raw}
        </FilePathLink>
      );
      cursor = token.end;
      continue;
    }

    parts.push(
      <span
        key={`m-${token.start}`}
        className={`inline rounded px-0.5 -mx-0.5 font-semibold ${
          isExcelMention ? "cursor-pointer hover:underline" : ""
        }`}
        style={{
          backgroundColor: "color-mix(in srgb, var(--em-primary) 18%, transparent)",
          color: "var(--em-primary)",
        }}
        onClick={isExcelMention ? () => handleExcelClick(token.value, token.rangeSpec) : undefined}
        title={isExcelMention ? "点击预览表格" : undefined}
      >
        {token.raw}
      </span>
    );

    cursor = token.end;
  }

  // 最后一个 token 之后的剩余文本
  if (cursor < text.length) {
    parts.push(<span key={`t-${cursor}`}>{text.slice(cursor)}</span>);
  }

  return <span className={className}>{parts}</span>;
}
