"use client";

import { useCallback, type ReactNode } from "react";
import { useExcelStore } from "@/stores/excel-store";
import { normalizeExcelPath } from "@/lib/api";
import { FilePathLink, isFilePath } from "./FilePathLink";
import { isCodeFile } from "./CodePreviewModal";
import { extractMentions, mentionCapsuleLabel } from "./mention-tokens";

const EXCEL_EXTS = new Set([".xlsx", ".xls", ".xlsm", ".xlsb", ".csv"]);

function isExcel(name: string): boolean {
  const dot = name.lastIndexOf(".");
  if (dot < 0) return false;
  return EXCEL_EXTS.has(name.slice(dot).toLowerCase());
}

interface MentionHighlighterProps {
  text: string;
  className?: string;
}

/**
 * Renders text with @mention tokens as inline capsules.
 * Excel file mentions are clickable and open the side panel.
 */
export function MentionHighlighter({ text, className }: MentionHighlighterProps) {
  const openPanel = useExcelStore((s) => s.openPanel);
  const addRecentFile = useExcelStore((s) => s.addRecentFile);

  const handleExcelClick = useCallback(
    (value: string, rangeSpec?: string) => {
      const normalized = normalizeExcelPath(value);
      const filename = normalized.split("/").pop() || normalized;

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

  const tokens = extractMentions(text).filter(
    (token) => token.kind !== "path" || isFilePath(token.value),
  );

  if (tokens.length === 0) {
    return <span className={className}>{text}</span>;
  }

  const parts: ReactNode[] = [];
  let cursor = 0;

  for (const token of tokens) {
    if (token.start > cursor) {
      parts.push(
        <span key={`t-${cursor}`}>{text.slice(cursor, token.start)}</span>
      );
    }

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
    const label = mentionCapsuleLabel(token);

    if (isPreviewable) {
      parts.push(
        <FilePathLink key={`m-${token.start}`} filePath={token.value} variant="text">
          {label}
        </FilePathLink>
      );
      cursor = token.end;
      continue;
    }

    parts.push(
      <span
        key={`m-${token.start}`}
        className={`inline-flex items-center max-w-[220px] rounded-full px-1.5 py-0 text-[11px] font-medium leading-4 align-middle ${
          isExcelMention ? "cursor-pointer hover:opacity-80" : ""
        }`}
        style={{
          backgroundColor: "color-mix(in srgb, var(--em-primary) 14%, transparent)",
          color: "var(--em-primary)",
        }}
        onClick={isExcelMention ? () => handleExcelClick(token.value, token.rangeSpec) : undefined}
        title={isExcelMention ? "点击预览表格" : token.raw}
      >
        <span className="truncate">{label}</span>
      </span>
    );

    cursor = token.end;
  }

  if (cursor < text.length) {
    parts.push(<span key={`t-${cursor}`}>{text.slice(cursor)}</span>);
  }

  return <span className={className}>{parts}</span>;
}
