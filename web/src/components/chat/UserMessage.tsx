"use client";

import React, { useState, useRef, useEffect, useCallback } from "react";
import { User, Check, X, Download, Pencil, Image as ImageIcon, Plus, FolderOpen, ChevronDown, ChevronUp, FileSpreadsheet, FileText } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { useExcelStore } from "@/stores/excel-store";
import { useSessionStore } from "@/stores/session-store";
import { MentionHighlighter } from "./MentionHighlighter";
import { downloadFile, buildApiUrl } from "@/lib/api";
import { ImagePreviewModal } from "./ImagePreviewModal";
import { CodePreviewModal, isCodeFile } from "./CodePreviewModal";
import type { FileAttachment } from "@/lib/types";

const MAX_COLLAPSED_HEIGHT = 200; // px

const EXCEL_EXTS = new Set([".xlsx", ".xls", ".csv"]);
function isExcelFile(filename: string): boolean {
  const ext = filename.slice(filename.lastIndexOf(".")).toLowerCase();
  return EXCEL_EXTS.has(ext);
}

const IMAGE_EXTS = new Set([".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"]);
function isImageFile(filename: string): boolean {
  const ext = filename.slice(filename.lastIndexOf(".")).toLowerCase();
  return IMAGE_EXTS.has(ext);
}

const ACCEPTED_EDIT_EXTENSIONS = ".xlsx,.xls,.csv,.png,.jpg,.jpeg";

interface UserMessageProps {
  content: string;
  files?: FileAttachment[];
  onEditAndResend?: (newContent: string, newFiles?: File[], retainedFiles?: FileAttachment[]) => void;
  isStreaming?: boolean;
}

export const UserMessage = React.memo(function UserMessage({ content, files, onEditAndResend, isStreaming }: UserMessageProps) {
  const [editing, setEditing] = useState(false);
  const [editText, setEditText] = useState(content);
  const [editFiles, setEditFiles] = useState<File[]>([]);
  const [retainedFiles, setRetainedFiles] = useState<FileAttachment[]>([]);
  const [expanded, setExpanded] = useState(false);
  const [needsExpand, setNeedsExpand] = useState(false);
  const [wsPickerOpen, setWsPickerOpen] = useState(false);
  const [wsFiles, setWsFiles] = useState<string[]>([]);
  const [wsFilter, setWsFilter] = useState("");
  const contentRef = useRef<HTMLDivElement>(null);
  const activeSessionId = useSessionStore((s) => s.activeSessionId);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const editFileInputRef = useRef<HTMLInputElement>(null);
  const wsPickerRef = useRef<HTMLDivElement>(null);

  // 监听来自 excel-store 的已确认 Excel 范围选择（在编辑模式下）
  const pendingSelection = useExcelStore((s) => s.pendingSelection);
  const clearPendingSelection = useExcelStore((s) => s.clearPendingSelection);

  useEffect(() => {
    if (contentRef.current) {
      setNeedsExpand(contentRef.current.scrollHeight > MAX_COLLAPSED_HEIGHT);
    }
  }, [content]);

  useEffect(() => {
    if (editing && textareaRef.current) {
      textareaRef.current.focus();
      textareaRef.current.setSelectionRange(editText.length, editText.length);
    }
  }, [editing, editText.length]);

  const startEdit = useCallback(() => {
    if (isStreaming) return;
    setEditText(content);
    setRetainedFiles(files ?? []);
    setEditing(true);
  }, [content, files, isStreaming]);

  const cancelEdit = useCallback(() => {
    setEditing(false);
    setEditText(content);
    setEditFiles([]);
    setRetainedFiles([]);
    setWsPickerOpen(false);
    setWsFiles([]);
    setWsFilter("");
  }, [content]);

  // 监听来自 excel-store 的已确认 Excel 范围选择（在编辑模式下）
  useEffect(() => {
    // 只有在编辑模式下才处理选择
    if (!editing || !pendingSelection) return;

    const { filePath, sheet, range } = pendingSelection;
    const filename = filePath.split("/").pop() || filePath;
    const token = `@file:${filename}[${sheet}!${range}]`;

    const textarea = textareaRef.current;
    // 使用 textarea 的当前值，而不是 editText 状态（避免闭包问题）
    const currentText = textarea?.value ?? "";
    const cursorPos = textarea?.selectionStart ?? currentText.length;
    const before = currentText.slice(0, cursorPos);
    const after = currentText.slice(cursorPos);
    const needsSpace = before.length > 0 && !before.endsWith(" ") && !before.endsWith("\n");
    const prefix = needsSpace ? " " : "";
    const newText = before + prefix + token + " " + after;
    setEditText(newText);

    // 将光标移动到插入内容之后
    const newCursorPos = (before + prefix + token + " ").length;
    requestAnimationFrame(() => {
      textarea?.focus();
      textarea?.setSelectionRange(newCursorPos, newCursorPos);
    });

    // 记录到最近文件
    const extLower = filename.slice(filename.lastIndexOf(".")).toLowerCase();
    if (EXCEL_EXTS.has(extLower)) {
      useExcelStore.getState().addRecentFile({
        path: filePath,
        filename,
      });
    }

    clearPendingSelection();
  }, [editing, pendingSelection, clearPendingSelection]);

  const fetchWorkspaceFiles = useCallback(async () => {
    try {
      const res = await fetch(buildApiUrl("/mentions"));
      if (res.ok) {
        const data = await res.json();
        setWsFiles((data.files as string[]) || []);
      }
    } catch { /* 后端不可用 */ }
  }, []);

  const toggleWsPicker = useCallback(() => {
    if (!wsPickerOpen) {
      fetchWorkspaceFiles();
    }
    setWsPickerOpen((v) => !v);
    setWsFilter("");
  }, [wsPickerOpen, fetchWorkspaceFiles]);

  const selectWsFile = useCallback((filename: string) => {
    const mention = `@file:${filename}`;
    const textarea = textareaRef.current;
    const cursorPos = textarea?.selectionStart ?? editText.length;
    const before = editText.slice(0, cursorPos);
    const after = editText.slice(cursorPos);
    const needsSpace = before.length > 0 && !before.endsWith(" ") && !before.endsWith("\n");
    const prefix = needsSpace ? " " : "";
    setEditText(before + prefix + mention + " " + after);
    setWsPickerOpen(false);
    setWsFilter("");
    requestAnimationFrame(() => textarea?.focus());
  }, [editText]);

  // 点击外部时关闭工作区选择器
  useEffect(() => {
    if (!wsPickerOpen) return;
    const handler = (e: MouseEvent) => {
      if (wsPickerRef.current && !wsPickerRef.current.contains(e.target as Node)) {
        setWsPickerOpen(false);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [wsPickerOpen]);

  const confirmEdit = useCallback(() => {
    const trimmed = editText.trim();
    if (!trimmed && editFiles.length === 0 && retainedFiles.length === 0) return;
    setEditing(false);
    onEditAndResend?.(
      trimmed,
      editFiles.length > 0 ? editFiles : undefined,
      retainedFiles.length > 0 ? retainedFiles : undefined,
    );
    setEditFiles([]);
    setRetainedFiles([]);
  }, [editText, editFiles, retainedFiles, onEditAndResend]);

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === "Escape") {
        cancelEdit();
      } else if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        confirmEdit();
      }
    },
    [cancelEdit, confirmEdit]
  );

  return (
    <div className="group flex gap-2.5 py-2.5">
      <div
        className="flex-shrink-0 h-6 w-6 rounded-full flex items-center justify-center text-white text-[10px]"
        style={{ backgroundColor: "var(--em-primary)" }}
      >
        <User className="h-3.5 w-3.5" />
      </div>
      <div className="flex-1 min-w-0">
        {editing ? (
          <div className="space-y-2">
            <textarea
              ref={textareaRef}
              value={editText}
              onChange={(e) => setEditText(e.target.value)}
              onKeyDown={handleKeyDown}
              className="w-full text-[13px] leading-relaxed rounded-2xl border border-[var(--em-primary-alpha-20)] bg-[var(--em-primary-alpha-10)] px-3 py-2 resize-none focus:outline-none focus:ring-2 focus:ring-[var(--em-primary-alpha-25)] focus:border-[var(--em-primary-alpha-25)] min-h-[60px] shadow-sm transition-colors"
              rows={Math.min(editText.split("\n").length + 1, 8)}
            />
            {(retainedFiles.length > 0 || editFiles.length > 0) && (
              <div className="flex flex-wrap gap-1">
                {retainedFiles.map((f, i) => {
                  const image = isImageFile(f.filename);
                  return (
                    <Badge key={`retained-${i}`} variant="secondary" className="text-xs gap-1 pr-1 max-w-[200px]">
                      {image && <ImageIcon className="h-3 w-3 flex-shrink-0" />}
                      <span className="truncate">{f.filename}</span>
                      <button
                        type="button"
                        className="rounded p-0.5 hover:bg-foreground/10 transition-colors flex-shrink-0"
                        title="移除"
                        onClick={() => setRetainedFiles((prev) => prev.filter((_, idx) => idx !== i))}
                      >
                        <X className="h-3 w-3" />
                      </button>
                    </Badge>
                  );
                })}
                {editFiles.map((f, i) => (
                  <Badge key={`new-${i}`} variant="secondary" className="text-xs gap-1 pr-1 max-w-[200px]">
                    {isImageFile(f.name) && <ImageIcon className="h-3 w-3 flex-shrink-0" />}
                    <span className="truncate">{f.name}</span>
                    <button
                      type="button"
                      className="rounded p-0.5 hover:bg-foreground/10 transition-colors flex-shrink-0"
                      title="移除"
                      onClick={() => setEditFiles((prev) => prev.filter((_, idx) => idx !== i))}
                    >
                      <X className="h-3 w-3" />
                    </button>
                  </Badge>
                ))}
              </div>
            )}
            <input
              ref={editFileInputRef}
              type="file"
              accept={ACCEPTED_EDIT_EXTENSIONS}
              multiple
              className="hidden"
              onChange={(e) => {
                const selected = e.target.files;
                if (selected && selected.length > 0) {
                  setEditFiles((prev) => [...prev, ...Array.from(selected)]);
                }
                e.target.value = "";
              }}
            />
            <div className="flex flex-wrap gap-1.5 relative">
              <button
                onClick={() => editFileInputRef.current?.click()}
                className="inline-flex items-center gap-1 text-xs px-3 py-1.5 rounded-lg bg-muted text-muted-foreground hover:bg-muted/80 transition-colors font-medium"
                title="添加附件"
              >
                <Plus className="h-3 w-3" />
                附件
              </button>
              <button
                onClick={toggleWsPicker}
                className={`inline-flex items-center gap-1 text-xs px-3 py-1.5 rounded-lg transition-colors font-medium ${
                  wsPickerOpen
                    ? "bg-[var(--em-primary-alpha-15)] text-[var(--em-primary)]"
                    : "bg-muted text-muted-foreground hover:bg-muted/80"
                }`}
                title="从工作区选取文件"
              >
                <FolderOpen className="h-3 w-3" />
                工作区
              </button>
              {wsPickerOpen && (
                <div
                  ref={wsPickerRef}
                  className="absolute bottom-full left-0 mb-1 w-64 max-w-[calc(100vw-3rem)] max-h-48 overflow-y-auto rounded-lg border bg-popover shadow-lg z-50"
                >
                  <div className="sticky top-0 bg-popover border-b px-2 py-1.5">
                    <input
                      type="text"
                      placeholder="搜索文件..."
                      value={wsFilter}
                      onChange={(e) => setWsFilter(e.target.value)}
                      className="w-full text-xs bg-transparent outline-none placeholder:text-muted-foreground/50"
                      autoFocus
                    />
                  </div>
                  {wsFiles
                    .filter((f) => !wsFilter || f.toLowerCase().includes(wsFilter.toLowerCase()))
                    .map((f) => (
                      <button
                        key={f}
                        type="button"
                        onClick={() => selectWsFile(f)}
                        className="w-full flex items-center gap-2 px-2 py-1.5 text-xs text-left hover:bg-accent transition-colors"
                      >
                        <FileSpreadsheet className="h-3 w-3 flex-shrink-0 text-muted-foreground" />
                        <span className="truncate">{f}</span>
                      </button>
                    ))}
                  {wsFiles.length === 0 && (
                    <div className="px-2 py-3 text-xs text-muted-foreground text-center">
                      加载中...
                    </div>
                  )}
                  {wsFiles.length > 0 && wsFiles.filter((f) => !wsFilter || f.toLowerCase().includes(wsFilter.toLowerCase())).length === 0 && (
                    <div className="px-2 py-3 text-xs text-muted-foreground text-center">
                      无匹配文件
                    </div>
                  )}
                </div>
              )}
              <button
                onClick={confirmEdit}
                className="inline-flex items-center gap-1 text-xs px-3 py-1.5 rounded-lg bg-[var(--em-primary)] text-white hover:bg-[var(--em-primary-dark)] transition-colors font-medium shadow-sm"
              >
                <Check className="h-3 w-3" />
                重发
              </button>
              <button
                onClick={cancelEdit}
                className="inline-flex items-center gap-1 text-xs px-3 py-1.5 rounded-lg bg-muted text-muted-foreground hover:bg-muted/80 transition-colors font-medium"
              >
                <X className="h-3 w-3" />
                取消
              </button>
            </div>
          </div>
        ) : (
          <div
            className={`group/bubble relative inline-block max-w-full rounded-2xl border border-[var(--em-primary-alpha-20)] bg-[var(--em-primary-alpha-10)] px-3 py-2 user-bubble ${
              onEditAndResend && !isStreaming
                ? "cursor-pointer hover:bg-[var(--em-primary-alpha-15)] hover:border-[var(--em-primary-alpha-25)]"
                : ""
            }`}
            onClick={onEditAndResend && !isStreaming ? startEdit : undefined}
          >
            <div
              ref={contentRef}
              className="overflow-hidden transition-[max-height] duration-300"
              style={{
                maxHeight: needsExpand && !expanded ? `${MAX_COLLAPSED_HEIGHT}px` : undefined,
              }}
            >
              <MentionHighlighter
                text={content}
                className="text-[13px] leading-relaxed whitespace-pre-wrap break-words"
              />
            </div>
            {needsExpand && !expanded && (
              <div className="relative -mt-6 pt-6 bg-gradient-to-t from-[var(--em-primary-alpha-10)] to-transparent">
                <button
                  type="button"
                  onClick={(e) => { e.stopPropagation(); setExpanded(true); }}
                  className="flex items-center gap-1 text-[11px] text-[var(--em-primary)] hover:text-[var(--em-primary-dark)] transition-colors cursor-pointer"
                >
                  <ChevronDown className="h-3 w-3" />
                  展开全部
                </button>
              </div>
            )}
            {needsExpand && expanded && (
              <button
                type="button"
                onClick={(e) => { e.stopPropagation(); setExpanded(false); }}
                className="flex items-center gap-1 mt-1 text-[11px] text-[var(--em-primary)] hover:text-[var(--em-primary-dark)] transition-colors cursor-pointer"
              >
                <ChevronUp className="h-3 w-3" />
                收起
              </button>
            )}
            {onEditAndResend && !isStreaming && (
              <span
                className="absolute -top-2 -right-2 h-6 w-6 rounded-full bg-background border border-border shadow-sm flex items-center justify-center opacity-0 group-hover/bubble:opacity-100 touch-show transition-opacity"
                aria-label="编辑消息"
              >
                <Pencil className="h-3 w-3 text-muted-foreground" />
              </span>
            )}
          </div>
        )}
        {!editing && files && files.length > 0 && (
          <div className="flex flex-wrap gap-1 mt-1.5">
            {files.map((f, i) => {
              const excel = isExcelFile(f.filename);
              const image = isImageFile(f.filename);
              const code = isCodeFile(f.filename);
              
              // 直接渲染，不使用 DialogTrigger，让移动端也能点击
              const attachment = (
                <Badge
                  key={i}
                  variant="secondary"
                  className={`text-xs gap-1 pr-1 max-w-[200px] touch-show ${
                    excel ? "cursor-pointer hover:bg-secondary/70" : ""
                  }`}
                  onClick={
                    excel
                      ? () => {
                          useExcelStore.getState().addRecentFile({
                            path: f.path,
                            filename: f.filename,
                          });
                          useExcelStore.getState().openPanel(f.path);
                        }
                      : undefined
                  }
                >
                  {image ? (
                    <ImageIcon className="h-3 w-3 flex-shrink-0" />
                  ) : code ? (
                    <FileText className="h-3 w-3 flex-shrink-0" />
                  ) : null}
                  <span className="truncate">{f.filename}</span>
                  <button
                    type="button"
                    className="rounded p-0.5 hover:bg-foreground/10 transition-colors flex-shrink-0"
                    title="下载"
                    onClick={(e) => {
                      e.stopPropagation();
                      downloadFile(
                        f.path,
                        f.filename,
                        activeSessionId ?? undefined
                      ).catch(() => {});
                    }}
                  >
                    <Download className="h-3 w-3" />
                  </button>
                </Badge>
              );

              // 移动端直接渲染预览组件，让 onClick 触发
              if (image) {
                return (
                  <ImagePreviewModal
                    key={i}
                    imagePath={f.path}
                    filename={f.filename}
                    trigger={attachment}
                  />
                );
              }

              if (code) {
                return (
                  <CodePreviewModal
                    key={i}
                    filePath={f.path}
                    filename={f.filename}
                    trigger={attachment}
                  />
                );
              }

              return attachment;
            })}
          </div>
        )}
      </div>
    </div>
  );
});
