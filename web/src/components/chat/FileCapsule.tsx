"use client";

import { useState } from "react";
import { Download } from "lucide-react";
import { FileTypeIcon } from "@/components/ui/file-type-icon";
import { isSpreadsheetFile } from "@/lib/file-kind";
import { displayFilePath } from "@/lib/file-identity";

const COLLAPSED_COUNT = 4;

export function isExcelFilename(name: string): boolean {
  return isSpreadsheetFile(name);
}

export interface RelatedFileItem {
  key: string;
  filename: string;
  filePath?: string;
  onOpen: () => void;
  onDownload: () => void;
}

export function RelatedFilesCard({
  files,
  onReview,
}: {
  files: RelatedFileItem[];
  onReview?: () => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const [hoveredPath, setHoveredPath] = useState<string | null>(null);

  if (files.length === 0) return null;

  const visible = expanded ? files : files.slice(0, COLLAPSED_COUNT);
  const hiddenCount = files.length - visible.length;
  const tooltip = hoveredPath || files.find((f) => f.filePath)?.filePath;

  return (
    <div className="relative my-2">
      {tooltip && (
        <div
          className={`pointer-events-none absolute left-1/2 -top-8 z-10 max-w-[min(100%,28rem)] -translate-x-1/2 truncate rounded-full border border-[var(--em-hairline)] bg-background px-3 py-1 text-[12px] text-[var(--em-text-secondary)] shadow-sm ${
            hoveredPath ? "opacity-100" : "opacity-0"
          }`}
        >
          {tooltip}
        </div>
      )}
      <div className="overflow-hidden rounded-2xl border border-[var(--em-hairline)] bg-background">
        <div className="flex items-center justify-between gap-3 px-3 sm:px-3.5 py-2.5">
          <span className="text-[13px] text-[var(--em-text-secondary)]">
            {files.length} 个相关文件
          </span>
          {onReview && (
            <button
              type="button"
              onClick={onReview}
              className="rounded-md px-1.5 py-1 text-[13px] text-[var(--em-text-secondary)] hover:bg-[var(--em-fill)] hover:text-foreground"
            >
              查看
            </button>
          )}
        </div>
        <div className="px-1.5 sm:px-2 pb-2">
          {visible.map((file) => (
            <div
              key={file.key}
              className="group flex items-center gap-2.5 rounded-lg px-2 sm:px-1.5 py-1.5 hover:bg-[var(--em-fill)] transition-colors"
              onMouseEnter={() => setHoveredPath(file.filePath || file.filename)}
              onMouseLeave={() => setHoveredPath(null)}
            >
              <button
                type="button"
                onClick={file.onOpen}
                className="flex min-w-0 flex-1 items-center gap-2.5 text-left"
                title={file.filePath ? displayFilePath(file.filePath) : file.filename}
              >
                <FileTypeIcon filename={file.filename} className="h-4 w-4 flex-shrink-0" />
                <span className="min-w-0 truncate text-[13px] text-foreground">
                  {file.filename}
                </span>
              </button>
              <button
                type="button"
                onClick={file.onDownload}
                className="flex-shrink-0 p-1 text-[var(--em-text-secondary)] opacity-0 group-hover:opacity-100 hover:text-foreground"
                title="下载"
                aria-label={`下载 ${file.filename}`}
              >
                <Download className="h-3.5 w-3.5" />
              </button>
            </div>
          ))}
          {hiddenCount > 0 && (
            <button
              type="button"
              onClick={() => setExpanded(true)}
              className="flex w-full items-center gap-2.5 rounded-lg px-2 sm:px-1.5 py-1.5 text-[13px] text-[var(--em-text-secondary)] hover:bg-[var(--em-fill)] hover:text-foreground"
            >
              <span className="w-4 text-center leading-none">···</span>
              显示另外 {hiddenCount} 个
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

export function FileCapsule({
  filename,
  title,
  onOpen,
  onDownload,
}: {
  filename: string;
  title?: string;
  subtitle?: string;
  onOpen: () => void;
  onDownload: () => void;
}) {
  return (
    <RelatedFilesCard
      files={[
        {
          key: filename,
          filename,
          filePath: title,
          onOpen,
          onDownload,
        },
      ]}
    />
  );
}
