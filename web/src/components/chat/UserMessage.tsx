"use client";

import { useState, useRef, useEffect, useCallback } from "react";
import { User, Check, X } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { useExcelStore } from "@/stores/excel-store";
import { MentionHighlighter } from "./MentionHighlighter";
import type { FileAttachment } from "@/lib/types";

const EXCEL_EXTS = new Set([".xlsx", ".xls", ".csv"]);
function isExcelFile(filename: string): boolean {
  const ext = filename.slice(filename.lastIndexOf(".")).toLowerCase();
  return EXCEL_EXTS.has(ext);
}

interface UserMessageProps {
  content: string;
  files?: FileAttachment[];
  onEditAndResend?: (newContent: string) => void;
  isStreaming?: boolean;
}

export function UserMessage({ content, files, onEditAndResend, isStreaming }: UserMessageProps) {
  const [editing, setEditing] = useState(false);
  const [editText, setEditText] = useState(content);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

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
  }, [content]);

  const confirmEdit = useCallback(() => {
    const trimmed = editText.trim();
    if (!trimmed) return;
    setEditing(false);
    onEditAndResend?.(trimmed);
  }, [editText, onEditAndResend]);

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
    <div className="group flex gap-3 py-4">
      <div
        className="flex-shrink-0 h-7 w-7 rounded-full flex items-center justify-center text-white text-xs"
        style={{ backgroundColor: "var(--em-primary)" }}
      >
        <User className="h-4 w-4" />
      </div>
      <div className="flex-1 min-w-0">
        {editing ? (
          <div className="space-y-2">
            <textarea
              ref={textareaRef}
              value={editText}
              onChange={(e) => setEditText(e.target.value)}
              onKeyDown={handleKeyDown}
              className="w-full text-sm border border-border rounded-md px-3 py-2 bg-background resize-none focus:outline-none focus:ring-1 focus:ring-ring min-h-[60px]"
              rows={Math.min(editText.split("\n").length + 1, 8)}
            />
            <div className="flex gap-1.5">
              <button
                onClick={confirmEdit}
                className="inline-flex items-center gap-1 text-xs px-2 py-1 rounded-md bg-primary text-primary-foreground hover:bg-primary/90"
              >
                <Check className="h-3 w-3" />
                重发
              </button>
              <button
                onClick={cancelEdit}
                className="inline-flex items-center gap-1 text-xs px-2 py-1 rounded-md bg-muted text-muted-foreground hover:bg-muted/80"
              >
                <X className="h-3 w-3" />
                取消
              </button>
            </div>
          </div>
        ) : (
          <div
            className={`relative inline-block max-w-full rounded-2xl border border-[var(--em-primary-alpha-15)] bg-[var(--em-primary-alpha-06)] px-3.5 py-2.5 shadow-sm transition-colors ${
              onEditAndResend && !isStreaming
                ? "cursor-pointer hover:bg-[var(--em-primary-alpha-10)] hover:border-[var(--em-primary-alpha-20)]"
                : ""
            }`}
            onClick={onEditAndResend && !isStreaming ? startEdit : undefined}
          >
            <MentionHighlighter
              text={content}
              className="text-sm leading-6 whitespace-pre-wrap break-words"
            />
          </div>
        )}
        {files && files.length > 0 && (
          <div className="flex flex-wrap gap-1.5 mt-2">
            {files.map((f, i) => {
              const excel = isExcelFile(f.filename);
              return (
                <Badge
                  key={i}
                  variant="secondary"
                  className={`text-xs ${excel ? "cursor-pointer hover:bg-secondary/70" : ""}`}
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
                  {f.filename}
                </Badge>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
