"use client";

import { useCallback } from "react";
import { FileSpreadsheet, FileText } from "lucide-react";
import {
  classifyWorkspaceFile,
  isWorkspaceFileHref,
} from "@/lib/file-kind";
import { openWorkspaceFile } from "@/lib/open-workspace-file";

/**
 * 检测一段文本是否是工作区文件路径。
 */
export function isFilePath(text: string): boolean {
  return isWorkspaceFileHref(text);
}

/**
 * 渲染文件路径为可点击链接：一律走 openWorkspaceFile。
 */
export function FilePathLink({
  filePath,
  children,
  variant = "code",
  sheet,
}: {
  filePath: string;
  children: React.ReactNode;
  variant?: "code" | "text";
  sheet?: string;
}) {
  const kind = classifyWorkspaceFile(filePath);
  const handleClick = useCallback(
    (e: React.MouseEvent) => {
      e.preventDefault();
      e.stopPropagation();
      openWorkspaceFile(filePath, { sheet });
    },
    [filePath, sheet],
  );

  const Icon = kind === "spreadsheet" ? FileSpreadsheet : FileText;
  const title =
    kind === "spreadsheet"
      ? "点击预览表格"
      : kind === "word"
        ? "点击打开文档"
        : kind === "binary"
          ? "点击下载文件"
          : "点击预览文件";

  if (variant === "code") {
    return (
      <code
        role="button"
        tabIndex={0}
        onClick={handleClick}
        onKeyDown={(e) => e.key === "Enter" && handleClick(e as unknown as React.MouseEvent)}
        className="inline rounded px-1 py-0.5 text-[12.5px] font-mono cursor-pointer transition-colors bg-[var(--em-primary-alpha-10)] text-[var(--em-primary)] hover:bg-[var(--em-primary-alpha-20)] hover:underline"
        style={{ boxDecorationBreak: "clone", WebkitBoxDecorationBreak: "clone" }}
        title={title}
      >
        <Icon className="h-3 w-3 inline align-[-0.125em] mr-1" />
        {children}
      </code>
    );
  }

  return (
    <span
      role="button"
      tabIndex={0}
      onClick={handleClick}
      onKeyDown={(e) => e.key === "Enter" && handleClick(e as unknown as React.MouseEvent)}
      className="inline cursor-pointer transition-colors text-[var(--em-primary)] hover:underline font-medium"
      title={title}
    >
      <Icon className="h-3 w-3 inline align-[-0.125em] mr-0.5" />
      {children}
    </span>
  );
}
