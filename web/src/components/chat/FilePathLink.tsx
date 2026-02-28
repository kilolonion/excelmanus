"use client";

import { useCallback } from "react";
import { FileSpreadsheet, FileText } from "lucide-react";
import { useExcelStore } from "@/stores/excel-store";
import { useSessionStore } from "@/stores/session-store";
import { normalizeExcelPath, downloadFile } from "@/lib/api";
import { CodePreviewModal, isCodeFile } from "./CodePreviewModal";

const EXCEL_EXTS = new Set([".xlsx", ".xls", ".csv", ".tsv"]);
const DOWNLOADABLE_EXTS =
  /\.(xlsx|xls|csv|tsv|pdf|zip|tar|gz|docx|pptx|txt|json|xml|html|md)$/i;

/**
 * 检测一段文本是否是文件路径（含扩展名）。
 * 匹配：`output.xlsx`、`./data/result.csv`、`path/to/file.pdf`
 */
const FILE_PATH_RE =
  /^(?:\.{0,2}\/)?[\w\u4e00-\u9fff][\w\u4e00-\u9fff./\\~ -]*\.[\w]{1,10}$/;

export function isFilePath(text: string): boolean {
  const trimmed = text.trim();
  if (!trimmed || trimmed.length > 260) return false;
  if (/[\n\r\t]/.test(trimmed)) return false;
  if (!DOWNLOADABLE_EXTS.test(trimmed)) return false;
  return FILE_PATH_RE.test(trimmed);
}

function isExcel(name: string): boolean {
  const dot = name.lastIndexOf(".");
  if (dot < 0) return false;
  return EXCEL_EXTS.has(name.slice(dot).toLowerCase());
}

/**
 * 渲染文件路径为可点击链接。
 * - Excel 文件：点击打开侧边预览面板
 * - 其他文件：点击下载
 */
export function FilePathLink({
  filePath,
  children,
  variant = "code",
}: {
  filePath: string;
  children: React.ReactNode;
  variant?: "code" | "text";
}) {
  const openPanel = useExcelStore((s) => s.openPanel);
  const addRecentFile = useExcelStore((s) => s.addRecentFile);
  const activeSessionId = useSessionStore((s) => s.activeSessionId);

  const excel = isExcel(filePath);
  const codePreviewable = !excel && isCodeFile(filePath);

  const handleClick = useCallback(
    (e: React.MouseEvent) => {
      e.preventDefault();
      e.stopPropagation();

      if (excel) {
        const normalized = normalizeExcelPath(filePath);
        const filename = normalized.split("/").pop() || normalized;

        const recentFiles = useExcelStore.getState().recentFiles;
        const existing = recentFiles.find(
          (f) => normalizeExcelPath(f.path) === normalized,
        );
        const resolvedPath = existing ? existing.path : normalized;

        addRecentFile({ path: resolvedPath, filename });
        openPanel(resolvedPath);
      } else {
        const filename = filePath.split("/").pop() || filePath;
        downloadFile(filePath, filename, activeSessionId ?? undefined).catch(
          () => {},
        );
      }
    },
    [filePath, excel, openPanel, addRecentFile, activeSessionId],
  );

  const Icon = excel ? FileSpreadsheet : FileText;
  const title = excel ? "点击预览表格" : codePreviewable ? "点击预览文件" : "点击下载文件";

  // 代码/文本文件：点击弹出预览弹窗
  if (codePreviewable) {
    const previewFilename = filePath.split("/").pop() || filePath;
    const trigger = variant === "code" ? (
      <code
        role="button"
        tabIndex={0}
        className="inline-flex items-center gap-1 rounded px-1 py-0.5 text-[12.5px] font-mono cursor-pointer transition-colors bg-[var(--em-primary-alpha-10)] text-[var(--em-primary)] hover:bg-[var(--em-primary-alpha-20)] hover:underline"
        title={title}
      >
        <Icon className="h-3 w-3 flex-shrink-0 inline" />
        {children}
      </code>
    ) : (
      <span
        role="button"
        tabIndex={0}
        className="inline-flex items-center gap-0.5 cursor-pointer transition-colors text-[var(--em-primary)] hover:underline font-medium"
        title={title}
      >
        <Icon className="h-3 w-3 flex-shrink-0 inline" />
        {children}
      </span>
    );
    return <CodePreviewModal filePath={filePath} filename={previewFilename} trigger={trigger} />;
  }

  if (variant === "code") {
    return (
      <code
        role="button"
        tabIndex={0}
        onClick={handleClick}
        onKeyDown={(e) => e.key === "Enter" && handleClick(e as unknown as React.MouseEvent)}
        className="inline-flex items-center gap-1 rounded px-1 py-0.5 text-[12.5px] font-mono cursor-pointer transition-colors bg-[var(--em-primary-alpha-10)] text-[var(--em-primary)] hover:bg-[var(--em-primary-alpha-20)] hover:underline"
        title={title}
      >
        <Icon className="h-3 w-3 flex-shrink-0 inline" />
        {children}
      </code>
    );
  }

  // text variant: 用于纯文本中的文件路径
  return (
    <span
      role="button"
      tabIndex={0}
      onClick={handleClick}
      onKeyDown={(e) => e.key === "Enter" && handleClick(e as unknown as React.MouseEvent)}
      className="inline-flex items-center gap-0.5 cursor-pointer transition-colors text-[var(--em-primary)] hover:underline font-medium"
      title={title}
    >
      <Icon className="h-3 w-3 flex-shrink-0 inline" />
      {children}
    </span>
  );
}
