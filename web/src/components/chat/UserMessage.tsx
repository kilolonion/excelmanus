"use client";

import React, { useState, useRef, useEffect, useCallback } from "react";
import { User, Check, X, Download, Pencil, Image as ImageIcon, Plus } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { useExcelStore } from "@/stores/excel-store";
import { useSessionStore } from "@/stores/session-store";
import { MentionHighlighter } from "./MentionHighlighter";
import { downloadFile } from "@/lib/api";
import type { FileAttachment } from "@/lib/types";

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
  onEditAndResend?: (newContent: string, files?: File[]) => void;
  isStreaming?: boolean;
}

export const UserMessage = React.memo(function UserMessage({ content, files, onEditAndResend, isStreaming }: UserMessageProps) {
  const [editing, setEditing] = useState(false);
  const [editText, setEditText] = useState(content);
  const [editFiles, setEditFiles] = useState<File[]>([]);
  const activeSessionId = useSessionStore((s) => s.activeSessionId);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const editFileInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (editing && textareaRef.current) {
      textareaRef.current.focus();
      textareaRef.current.setSelectionRange(editText.length, editText.length);
    }
  }, [editing, editText.length]);

  const startEdit = useCallback(() => {
    if (isStreaming) return;
    setEditText(content);
    setEditing(true);
  }, [content, isStreaming]);

  const cancelEdit = useCallback(() => {
    setEditing(false);
    setEditText(content);
    setEditFiles([]);
  }, [content]);

  const confirmEdit = useCallback(() => {
    const trimmed = editText.trim();
    if (!trimmed && editFiles.length === 0) return;
    setEditing(false);
    onEditAndResend?.(trimmed, editFiles.length > 0 ? editFiles : undefined);
    setEditFiles([]);
  }, [editText, editFiles, onEditAndResend]);

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
            {editFiles.length > 0 && (
              <div className="flex flex-wrap gap-1.5">
                {editFiles.map((f, i) => (
                  <Badge key={i} variant="secondary" className="text-xs gap-1 pr-1 max-w-[200px]">
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
            <div className="flex gap-1.5">
              <button
                onClick={() => editFileInputRef.current?.click()}
                className="inline-flex items-center gap-1 text-xs px-3 py-1.5 rounded-lg bg-muted text-muted-foreground hover:bg-muted/80 transition-colors font-medium"
                title="添加附件"
              >
                <Plus className="h-3 w-3" />
                附件
              </button>
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
            className={`group/bubble relative inline-block max-w-full rounded-2xl border border-[var(--em-primary-alpha-20)] bg-[var(--em-primary-alpha-10)] px-3 py-2 shadow-sm transition-colors ${
              onEditAndResend && !isStreaming
                ? "cursor-pointer hover:bg-[var(--em-primary-alpha-15)] hover:border-[var(--em-primary-alpha-25)]"
                : ""
            }`}
            onClick={onEditAndResend && !isStreaming ? startEdit : undefined}
          >
            <MentionHighlighter
              text={content}
              className="text-[13px] leading-relaxed whitespace-pre-wrap break-words"
            />
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
        {files && files.length > 0 && (
          <div className="flex flex-wrap gap-1.5 mt-2">
            {files.map((f, i) => {
              const excel = isExcelFile(f.filename);
              const image = isImageFile(f.filename);
              return (
                <Badge
                  key={i}
                  variant="secondary"
                  className={`text-xs gap-1 pr-1 max-w-[200px] ${
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
                  {image && <ImageIcon className="h-3 w-3 flex-shrink-0" />}
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
            })}
          </div>
        )}
      </div>
    </div>
  );
});
