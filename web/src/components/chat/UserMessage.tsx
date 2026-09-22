"use client";

import React, { useState, useRef, useEffect, useCallback, useLayoutEffect } from "react";
import { Check, X, Download, Pencil, Image as ImageIcon, Plus, FolderOpen, ChevronDown, ChevronUp, FileSpreadsheet, FileText } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { useExcelStore } from "@/stores/excel-store";
import { useSessionStore } from "@/stores/session-store";
import { useIsMobile } from "@/hooks/use-mobile";
import {
  formatFileMention,
  insertTokensIntoText,
  scheduleTextareaCursor,
  toDisplayMentionTokens,
  trackRecentExcelFile,
} from "./chat-input-insert";
import { MentionHighlighter } from "./MentionHighlighter";
import { apiFetch, downloadFile, buildApiUrl, getAuthHeaders } from "@/lib/api";
import { classifyWorkspaceFile } from "@/lib/file-kind";
import { displayFilePath } from "@/lib/file-identity";
import { openWorkspaceFile } from "@/lib/open-workspace-file";
import type { FileAttachment } from "@/lib/types";

const MAX_COLLAPSED_HEIGHT = 200; // px

const ACCEPTED_EDIT_EXTENSIONS = ".xlsx,.xls,.xlsm,.xlsb,.csv,.png,.jpg,.jpeg";

/** 提交前把编辑区短标签还原为发送协议。 */
export function restoreFullMentions(
  text: string,
  map: Map<string, string>,
): string {
  let next = text;
  const entries = [...map.entries()].sort((a, b) => b[0].length - a[0].length);
  const slots: string[] = [];
  for (const [display, full] of entries) {
    if (!next.includes(display)) continue;
    const mark = `\0${slots.length}\0`;
    next = next.replaceAll(display, mark);
    slots.push(full);
  }
  for (let i = 0; i < slots.length; i++) {
    next = next.replaceAll(`\0${i}\0`, slots[i]);
  }
  return next;
}

export function insertEditMentionTokens(
  text: string,
  cursorPos: number,
  fullTokens: string[],
  tokenMap: Map<string, string>,
): { newText: string; newCursorPos: number; displayTokens: string[] } {
  const displayTokens = toDisplayMentionTokens(fullTokens, tokenMap);
  const inserted = insertTokensIntoText(text, cursorPos, displayTokens);
  return { ...inserted, displayTokens };
}

export function findAtomicMentionDeletion(
  text: string,
  cursor: number,
  key: "Backspace" | "Delete",
  confirmedTokens: Iterable<string>,
): { newText: string; token: string; newCursor: number } | null {
  for (const token of confirmedTokens) {
    const idx = text.indexOf(token);
    if (idx < 0) continue;
    const tokenEnd = idx + token.length;
    const hit =
      key === "Backspace"
        ? cursor > idx && cursor <= tokenEnd
        : cursor >= idx && cursor < tokenEnd;
    if (!hit) continue;
    const trailSpace = text[tokenEnd] === " " ? 1 : 0;
    return {
      newText: text.slice(0, idx) + text.slice(tokenEnd + trailSpace),
      token,
      newCursor: idx,
    };
  }
  return null;
}

interface UserMessageProps {
  content: string;
  files?: FileAttachment[];
  onEditAndResend?: (newContent: string, newFiles?: File[], retainedFiles?: FileAttachment[]) => void;
  isStreaming?: boolean;
  timestamp?: number;
}

function formatClock(ts?: number): string | null {
  if (!ts) return null;
  return new Date(ts).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" });
}

export const UserMessage = React.memo(function UserMessage({ content, files, onEditAndResend, isStreaming, timestamp }: UserMessageProps) {
  const [editing, setEditing] = useState(false);
  const [editText, setEditText] = useState(content);
  const [editFiles, setEditFiles] = useState<File[]>([]);
  const [retainedFiles, setRetainedFiles] = useState<FileAttachment[]>([]);
  const [expanded, setExpanded] = useState(false);
  const [needsExpand, setNeedsExpand] = useState(false);
  const [wsPickerOpen, setWsPickerOpen] = useState(false);
  const [wsFiles, setWsFiles] = useState<string[]>([]);
  const [wsFilter, setWsFilter] = useState("");
  const [filesExpanded, setFilesExpanded] = useState(false);
  const [editWidth, setEditWidth] = useState<number | null>(null);
  const isMobile = useIsMobile();
  const MOBILE_FILE_LIMIT = 2;
  const contentRef = useRef<HTMLDivElement>(null);
  const columnRef = useRef<HTMLDivElement>(null);
  const activeSessionId = useSessionStore((s) => s.activeSessionId);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const editFileInputRef = useRef<HTMLInputElement>(null);
  const wsPickerRef = useRef<HTMLDivElement>(null);
  const editTokenMapRef = useRef<Map<string, string>>(new Map());
  const [editConfirmedTokens, setEditConfirmedTokens] = useState<Set<string>>(
    () => new Set(),
  );

  const rememberDisplayTokens = useCallback((tokens: string[]) => {
    setEditConfirmedTokens((prev) => {
      const next = new Set(prev);
      tokens.forEach((token) => next.add(token));
      return next;
    });
  }, []);

  const clearEditMentionState = useCallback(() => {
    editTokenMapRef.current.clear();
    setEditConfirmedTokens(new Set());
  }, []);

  // 监听来自 excel-store 的已确认 Excel 范围选择（在编辑模式下）
  const pendingSelection = useExcelStore((s) => s.pendingSelection);
  const clearPendingSelection = useExcelStore((s) => s.clearPendingSelection);

  useEffect(() => {
    if (contentRef.current) {
      setNeedsExpand(contentRef.current.scrollHeight > MAX_COLLAPSED_HEIGHT);
    }
  }, [content]);

  const resizeEditTextarea = useCallback(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    const next = Math.min(Math.max(el.scrollHeight, 60), 240);
    el.style.height = `${next}px`;
    el.style.overflowY = el.scrollHeight > 240 ? "auto" : "hidden";
  }, []);

  useLayoutEffect(() => {
    if (!editing) return;
    resizeEditTextarea();
  }, [editing, editText, editWidth, resizeEditTextarea]);

  useEffect(() => {
    if (!editing || !textareaRef.current) return;
    const el = textareaRef.current;
    el.focus();
    const len = el.value.length;
    el.setSelectionRange(len, len);
  }, [editing]);

  const startEdit = useCallback(() => {
    if (isStreaming) return;
    const bubbleEl = columnRef.current?.querySelector(".user-bubble") as HTMLElement | null;
    const measured = (bubbleEl ?? columnRef.current)?.getBoundingClientRect().width ?? 0;
    setEditWidth(Math.max(Math.ceil(measured), 260));
    clearEditMentionState();
    setEditText(content);
    setRetainedFiles(files ?? []);
    setFilesExpanded(false);
    setEditing(true);
  }, [content, files, isStreaming, clearEditMentionState]);

  const cancelEdit = useCallback(() => {
    setEditing(false);
    setEditWidth(null);
    setEditText(content);
    setEditFiles([]);
    setRetainedFiles([]);
    setWsPickerOpen(false);
    setWsFiles([]);
    setWsFilter("");
    clearEditMentionState();
  }, [content, clearEditMentionState]);

  // 监听来自 excel-store 的已确认 Excel 范围选择（在编辑模式下）
  useEffect(() => {
    // 只有在编辑模式下才处理选择
    if (!editing || !pendingSelection) return;

    const { filePath, sheet, range } = pendingSelection;
    const filename = filePath.split("/").pop() || filePath;
    const version = pendingSelection.contentVersion;

    const textarea = textareaRef.current;
    // 使用 textarea 的当前值，而不是 editText 状态（避免闭包问题）
    const currentText = textarea?.value ?? "";
    const cursorPos = textarea?.selectionStart ?? currentText.length;
    const { newText, newCursorPos, displayTokens } = insertEditMentionTokens(
      currentText,
      cursorPos,
      [formatFileMention({ path: filePath, sheet, range, version })],
      editTokenMapRef.current,
    );
    setEditText(newText);
    rememberDisplayTokens(displayTokens);
    scheduleTextareaCursor(textarea, newCursorPos);
    trackRecentExcelFile(filePath, filename);

    clearPendingSelection();
  }, [editing, pendingSelection, clearPendingSelection, rememberDisplayTokens]);

  const fetchWorkspaceFiles = useCallback(async () => {
    try {
      const params = new URLSearchParams();
      if (activeSessionId) params.set("session_id", activeSessionId);
      const qs = params.toString();
      const res = await apiFetch(buildApiUrl(`/mentions${qs ? `?${qs}` : ""}`), {
        headers: { ...getAuthHeaders() },
      });
      if (res.ok) {
        const data = await res.json();
        setWsFiles((data.files as string[]) || []);
      }
    } catch { /* 后端不可用 */ }
  }, [activeSessionId]);

  const toggleWsPicker = useCallback(() => {
    if (!wsPickerOpen) {
      fetchWorkspaceFiles();
    }
    setWsPickerOpen((v) => !v);
    setWsFilter("");
  }, [wsPickerOpen, fetchWorkspaceFiles]);

  const selectWsFile = useCallback((path: string) => {
    const textarea = textareaRef.current;
    const currentText = textarea?.value ?? editText;
    const cursorPos = textarea?.selectionStart ?? currentText.length;
    const { newText, newCursorPos, displayTokens } = insertEditMentionTokens(
      currentText,
      cursorPos,
      [formatFileMention({ path })],
      editTokenMapRef.current,
    );
    setEditText(newText);
    rememberDisplayTokens(displayTokens);
    setWsPickerOpen(false);
    setWsFilter("");
    scheduleTextareaCursor(textarea, newCursorPos);
  }, [editText, rememberDisplayTokens]);

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
    const trimmed = restoreFullMentions(editText, editTokenMapRef.current).trim();
    if (!trimmed && editFiles.length === 0 && retainedFiles.length === 0) return;
    setEditing(false);
    setEditWidth(null);
    clearEditMentionState();
    onEditAndResend?.(
      trimmed,
      editFiles.length > 0 ? editFiles : undefined,
      retainedFiles.length > 0 ? retainedFiles : undefined,
    );
    setEditFiles([]);
    setRetainedFiles([]);
  }, [editText, editFiles, retainedFiles, onEditAndResend, clearEditMentionState]);

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === "Escape") {
        cancelEdit();
        return;
      }
      if (e.key === "Backspace" || e.key === "Delete") {
        const textarea = textareaRef.current;
        if (textarea && textarea.selectionStart === textarea.selectionEnd) {
          const hit = findAtomicMentionDeletion(
            editText,
            textarea.selectionStart,
            e.key,
            editConfirmedTokens,
          );
          if (hit) {
            e.preventDefault();
            setEditText(hit.newText);
            setEditConfirmedTokens((prev) => {
              const next = new Set(prev);
              next.delete(hit.token);
              return next;
            });
            editTokenMapRef.current.delete(hit.token);
            scheduleTextareaCursor(textarea, hit.newCursor);
            return;
          }
        }
      }
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        confirmEdit();
      }
    },
    [cancelEdit, confirmEdit, editText, editConfirmedTokens]
  );

  const clock = formatClock(timestamp);

  return (
    <div className="group flex justify-end py-2.5">
      <div
        className={`flex gap-2 max-w-[88%] sm:max-w-[75%] min-w-0 ${
          editing ? "items-stretch" : "items-start"
        }`}
      >
        <div
          ref={columnRef}
          className={`min-w-0 max-w-full flex flex-col items-start ${
            editing ? "w-full" : "w-max"
          }`}
          style={
            editing && editWidth
              ? { width: editWidth, minWidth: editWidth, flexShrink: 0 }
              : undefined
          }
        >
        {editing ? (
          <div className="w-full min-w-0 space-y-2">
            <textarea
              ref={textareaRef}
              value={editText}
              onChange={(e) => setEditText(e.target.value)}
              onKeyDown={handleKeyDown}
              className="box-border block w-full min-w-0 text-[13px] leading-relaxed whitespace-pre-wrap break-words rounded-2xl border border-[var(--em-primary-alpha-20)] bg-[var(--em-primary-alpha-10)] px-3 py-2 resize-none focus:outline-none focus:ring-2 focus:ring-[var(--em-primary-alpha-25)] focus:border-[var(--em-primary-alpha-25)] min-h-[60px] shadow-sm transition-colors"
            />
            {(retainedFiles.length > 0 || editFiles.length > 0) && (() => {
              const allEditBadges = [
                ...retainedFiles.map((f, i) => ({ key: `retained-${i}`, filename: f.filename, isImage: classifyWorkspaceFile(f.filename) === "image", onRemove: () => setRetainedFiles((prev) => prev.filter((_, idx) => idx !== i)) })),
                ...editFiles.map((f, i) => ({ key: `new-${i}`, filename: f.name, isImage: classifyWorkspaceFile(f.name) === "image", onRemove: () => setEditFiles((prev) => prev.filter((_, idx) => idx !== i)) })),
              ];
              const shouldCollapse = isMobile && allEditBadges.length > MOBILE_FILE_LIMIT && !filesExpanded;
              const visible = shouldCollapse ? allEditBadges.slice(0, MOBILE_FILE_LIMIT) : allEditBadges;
              const hiddenCount = allEditBadges.length - visible.length;
              return (
                <div className="flex w-full flex-wrap gap-1">
                  {visible.map((b) => (
                    <Badge key={b.key} variant="secondary" className="text-[11px] leading-4 gap-0.5 pl-2 pr-0.5 py-0 max-w-[200px]">
                      {b.isImage && <ImageIcon className="h-3 w-3 flex-shrink-0" />}
                      <span className="truncate">{b.filename}</span>
                      <button
                        type="button"
                        className="touch-compact rounded p-0.5 hover:bg-foreground/10 transition-colors flex-shrink-0"
                        title="移除"
                        onClick={b.onRemove}
                      >
                        <X className="h-3 w-3" />
                      </button>
                    </Badge>
                  ))}
                  {hiddenCount > 0 && (
                    <button
                      type="button"
                      onClick={() => setFilesExpanded(true)}
                      className="touch-compact inline-flex items-center gap-0.5 rounded-full text-[11px] font-medium px-2 py-0.5 bg-muted text-muted-foreground hover:bg-muted/80 transition-colors"
                    >
                      +{hiddenCount} 个文件
                      <ChevronDown className="h-3 w-3" />
                    </button>
                  )}
                </div>
              );
            })()}
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
            <div className="flex w-full flex-wrap gap-1.5 relative items-center justify-end">
              <button
                onClick={() => editFileInputRef.current?.click()}
                className="inline-flex items-center gap-1 text-xs px-2 sm:px-3 py-2 sm:py-1.5 rounded-lg bg-muted text-muted-foreground hover:bg-muted/80 transition-colors font-medium"
                title="添加附件"
              >
                <Plus className="h-3 w-3" />
                <span className="hidden sm:inline">附件</span>
              </button>
              <button
                onClick={toggleWsPicker}
                className={`inline-flex items-center gap-1 text-xs px-2 sm:px-3 py-2 sm:py-1.5 rounded-lg transition-colors font-medium ${
                  wsPickerOpen
                    ? "bg-[var(--em-primary-alpha-15)] text-[var(--em-primary)]"
                    : "bg-muted text-muted-foreground hover:bg-muted/80"
                }`}
                title="从工作区选取文件"
              >
                <FolderOpen className="h-3 w-3" />
                <span className="hidden sm:inline">工作区</span>
              </button>
              <div className="flex-1 sm:hidden" />
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
                    .filter((f) => !wsFilter || displayFilePath(f).toLowerCase().includes(wsFilter.toLowerCase()))
                    .map((f) => (
                      <button
                        key={f}
                        type="button"
                        onClick={() => selectWsFile(f)}
                        className="w-full flex items-center gap-2 px-2 py-1.5 text-xs text-left hover:bg-accent transition-colors"
                      >
                        <FileSpreadsheet className="h-3 w-3 flex-shrink-0 text-muted-foreground" />
                        <span className="truncate">{displayFilePath(f)}</span>
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
                className="inline-flex items-center gap-1 text-xs px-2 sm:px-3 py-2 sm:py-1.5 rounded-lg bg-[var(--em-primary)] text-white hover:bg-[var(--em-primary-dark)] transition-colors font-medium shadow-sm"
              >
                <Check className="h-3 w-3" />
                <span className="hidden sm:inline">重发</span>
              </button>
              <button
                onClick={cancelEdit}
                className="inline-flex items-center gap-1 text-xs px-2 sm:px-3 py-2 sm:py-1.5 rounded-lg bg-muted text-muted-foreground hover:bg-muted/80 transition-colors font-medium"
              >
                <X className="h-3 w-3" />
                <span className="hidden sm:inline">取消</span>
              </button>
            </div>
          </div>
        ) : content ? (
          <div
            className={`group/bubble relative w-fit max-w-full rounded-2xl border border-[var(--em-primary-alpha-20)] bg-[var(--em-primary-alpha-10)] px-3 py-2 user-bubble ${
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
                className="absolute -top-2 -right-2 h-6 w-6 rounded-full bg-background border border-border shadow-sm flex items-center justify-center opacity-0 group-hover/bubble:opacity-100 touch-show-desktop transition-opacity"
                aria-label="编辑消息"
              >
                <Pencil className="h-3 w-3 text-muted-foreground" />
              </span>
            )}
          </div>
        ) : null}
        {!editing && files && files.length > 0 && (() => {
          const shouldCollapseView = isMobile && files.length > MOBILE_FILE_LIMIT && !filesExpanded;
          const visibleFiles = shouldCollapseView ? files.slice(0, MOBILE_FILE_LIMIT) : files;
          const hiddenFileCount = files.length - visibleFiles.length;
          return (
          <div className={`flex max-w-full flex-wrap gap-1 ${content ? "mt-1.5" : ""}`}>
            {visibleFiles.map((f, i) => {
              const kind = classifyWorkspaceFile(f.filename);
              return (
                <Badge
                  key={i}
                  variant="secondary"
                  className="text-[11px] leading-4 gap-0.5 pl-2 pr-0.5 py-0 max-w-[200px] touch-show cursor-pointer hover:bg-secondary/70"
                  onClick={() => openWorkspaceFile(f.path)}
                >
                  {kind === "image" ? (
                    <ImageIcon className="h-3 w-3 flex-shrink-0" />
                  ) : kind === "spreadsheet" ? (
                    <FileSpreadsheet className="h-3 w-3 flex-shrink-0" />
                  ) : kind === "text" || kind === "word" ? (
                    <FileText className="h-3 w-3 flex-shrink-0" />
                  ) : null}
                  <span className="truncate">{f.filename}</span>
                  <button
                    type="button"
                    className="touch-compact rounded p-0.5 hover:bg-foreground/10 transition-colors flex-shrink-0"
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
            })}
            {hiddenFileCount > 0 && (
              <button
                type="button"
                onClick={(e) => { e.stopPropagation(); setFilesExpanded(true); }}
                className="touch-compact inline-flex items-center gap-0.5 rounded-full text-[11px] font-medium px-2 py-0.5 bg-muted text-muted-foreground hover:bg-muted/80 transition-colors"
              >
                +{hiddenFileCount} 个文件
                <ChevronDown className="h-3 w-3" />
              </button>
            )}
          </div>
          );
        })()}
        </div>
        {!editing && clock && (
          <span className="text-[11px] text-muted-foreground/70 tabular-nums pt-2 flex-shrink-0">
            {clock}
          </span>
        )}
      </div>
    </div>
  );
});
