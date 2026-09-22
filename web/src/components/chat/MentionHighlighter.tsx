"use client";

import { useCallback, type ReactNode } from "react";
import { FilePathLink, isFilePath } from "./FilePathLink";
import { classifyWorkspaceFile } from "@/lib/file-kind";
import { extractMentions, mentionCapsuleLabel } from "./mention-tokens";
import { openWorkspaceFile } from "@/lib/open-workspace-file";
import { splitWorkbookRangeSpec } from "@/lib/workbook-focus";

interface MentionHighlighterProps {
  text: string;
  className?: string;
}

/**
 * Renders text with @mention tokens as inline capsules.
 * Workspace file mentions all go through openWorkspaceFile.
 */
export function MentionHighlighter({ text, className }: MentionHighlighterProps) {
  const handleSpreadsheetClick = useCallback((value: string, rangeSpec?: string, version?: string) => {
    openWorkspaceFile(value, { ...splitWorkbookRangeSpec(rangeSpec), version });
  }, []);

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
    const kind = isFileMention ? classifyWorkspaceFile(token.value) : null;
    const label = mentionCapsuleLabel(token);

    if (isFileMention && kind === "spreadsheet") {
      parts.push(
        <span
          key={`m-${token.start}`}
          className="inline-flex items-center max-w-[220px] rounded-full px-1.5 py-0 text-[11px] font-medium leading-4 align-middle cursor-pointer hover:opacity-80"
          style={{
            backgroundColor: "color-mix(in srgb, var(--em-primary) 14%, transparent)",
            color: "var(--em-primary)",
          }}
          role="button"
          tabIndex={0}
          onClick={() => handleSpreadsheetClick(token.value, token.rangeSpec, token.version)}
          onKeyDown={(event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); handleSpreadsheetClick(token.value, token.rangeSpec, token.version); } }}
          title={token.rangeSpec ? "点击定位到表格中的引用区域" : "点击预览表格"}
        >
          <span className="truncate">{label}</span>
        </span>
      );
      cursor = token.end;
      continue;
    }

    if (isFileMention && kind && kind !== "binary") {
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
        className="inline-flex items-center max-w-[220px] rounded-full px-1.5 py-0 text-[11px] font-medium leading-4 align-middle"
        style={{
          backgroundColor: "color-mix(in srgb, var(--em-primary) 14%, transparent)",
          color: "var(--em-primary)",
        }}
        title={token.version ? `${label}（已钉版本）` : label}
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
