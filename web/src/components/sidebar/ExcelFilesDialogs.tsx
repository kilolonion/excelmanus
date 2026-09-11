"use client";

import { useState, useEffect } from "react";
import {
  FileSpreadsheet,
  X,
  Download,
  Trash2,
} from "lucide-react";
import {
  OverlayCard,
  OverlayCardAction,
  OverlayCardBody,
  OverlayCardFooter,
  OverlayCardHeader,
} from "@/components/ui/overlay-card";
import { downloadFile } from "@/lib/api";

/* ── ExcelFilesDialog ── */

export function ExcelFilesDialog({
  files,
  sessionId,
  onClose,
  onClickFile,
  onDoubleClickFile,
  onRemoveFile,
}: {
  files: { path: string; filename: string; lastUsedAt: number }[];
  sessionId?: string;
  onClose: () => void;
  onClickFile: (path: string) => void;
  onDoubleClickFile: (path: string) => void;
  onRemoveFile: (path: string) => void;
}) {
  const [search, setSearch] = useState("");

  const filtered = search.trim()
    ? files.filter(
        (f) =>
          f.filename.toLowerCase().includes(search.toLowerCase()) ||
          f.path.toLowerCase().includes(search.toLowerCase())
      )
    : files;

  return (
    <OverlayCard open onOpenChange={(v) => !v && onClose()} size="sm" tone="primary">
      <OverlayCardHeader
        icon={<FileSpreadsheet className="h-5 w-5" />}
        title={`工作区文件 (${files.length})`}
        onClose={onClose}
      />

      <OverlayCardBody className="pt-3 pb-4">
        <input
          type="text"
          placeholder="搜索文件..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="w-full mb-3 px-3 py-2 text-sm rounded-xl border border-border bg-muted/30 placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-[var(--em-primary)]"
          autoFocus
        />

        <div className="max-h-[min(50vh,360px)] overflow-y-auto -mx-1">
          {filtered.length === 0 ? (
            <div className="flex items-center justify-center py-8 text-sm text-muted-foreground">
              未找到匹配文件
            </div>
          ) : (
            filtered.map((file) => {
              const time = new Date(file.lastUsedAt).toLocaleDateString(
                "zh-CN",
                {
                  month: "short",
                  day: "numeric",
                  hour: "2-digit",
                  minute: "2-digit",
                }
              );
              return (
                <div
                  key={file.path}
                  className="group flex items-center gap-2 px-3 py-2 rounded-xl hover:bg-muted/50 cursor-pointer text-sm transition-colors"
                  onClick={() => {
                    onClickFile(file.path);
                    onClose();
                  }}
                  onDoubleClick={() => {
                    onDoubleClickFile(file.path);
                    onClose();
                  }}
                  title={file.path}
                >
                  <FileSpreadsheet
                    className="h-4 w-4 flex-shrink-0"
                    style={{ color: "var(--em-primary)" }}
                  />
                  <div className="flex-1 min-w-0">
                    <div className="truncate text-foreground/90">
                      {file.filename}
                    </div>
                    <div className="truncate text-[10px] text-muted-foreground">
                      {file.path}
                    </div>
                  </div>
                  <span className="text-[10px] text-muted-foreground whitespace-nowrap">
                    {time}
                  </span>
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      downloadFile(file.path, file.filename, sessionId).catch(() => {});
                    }}
                    className="p-1 rounded opacity-0 group-hover:opacity-100 hover:bg-muted text-muted-foreground hover:text-foreground transition-all"
                    title="下载"
                  >
                    <Download className="h-3 w-3" />
                  </button>
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      onRemoveFile(file.path);
                    }}
                    className="p-1 rounded opacity-0 group-hover:opacity-100 hover:bg-muted text-muted-foreground hover:text-foreground transition-all"
                    title="移除"
                  >
                    <X className="h-3 w-3" />
                  </button>
                </div>
              );
            })
          )}
        </div>
      </OverlayCardBody>
    </OverlayCard>
  );
}

/* ── RemoveConfirmDialog ── */

export function RemoveConfirmDialog({
  open,
  count,
  isDeleteAll,
  deleting,
  onConfirm,
  onCancel,
}: {
  open: boolean;
  count: number;
  isDeleteAll: boolean;
  deleting: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  useEffect(() => {
    if (!open || deleting) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Enter") {
        e.preventDefault();
        e.stopPropagation();
        onConfirm();
      }
    };
    window.addEventListener("keydown", handler, true);
    return () => window.removeEventListener("keydown", handler, true);
  }, [open, deleting, onConfirm]);

  const title = isDeleteAll
    ? "删除工作区所有文件？"
    : count === 1
      ? "删除此文件？"
      : `删除 ${count} 个文件？`;

  const description = isDeleteAll
    ? "此操作将永久删除工作区内所有文件，且无法撤销。"
    : "此操作将永久删除所选文件，且无法撤销。";

  return (
    <OverlayCard
      open={open}
      onOpenChange={(v) => !v && onCancel()}
      size="sm"
      tone="danger"
      onOpenAutoFocus={(e) => e.preventDefault()}
    >
      <OverlayCardHeader
        icon={<Trash2 className="h-5 w-5" />}
        title={title}
        description={description}
        onClose={onCancel}
      />
      <OverlayCardFooter>
        <OverlayCardAction action="ghost" onClick={onCancel} disabled={deleting}>
          取消
          <kbd className="ml-0.5 text-[10px] text-muted-foreground/60 font-normal hidden sm:inline">esc</kbd>
        </OverlayCardAction>
        <OverlayCardAction action="destructive" onClick={onConfirm} disabled={deleting}>
          {deleting ? "删除中…" : isDeleteAll ? "全部删除" : "确认删除"}
          <kbd className="ml-0.5 text-[10px] opacity-60 font-normal hidden sm:inline">↵</kbd>
        </OverlayCardAction>
      </OverlayCardFooter>
    </OverlayCard>
  );
}
