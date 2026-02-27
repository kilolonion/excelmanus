"use client";

import { useState, useRef, useEffect } from "react";

/* ── InlineRenameInput ── */

export function InlineRenameInput({
  defaultValue,
  onConfirm,
  onCancel,
}: {
  defaultValue: string;
  onConfirm: (value: string) => void;
  onCancel: () => void;
}) {
  const [value, setValue] = useState(defaultValue);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    inputRef.current?.focus();
    // 选择不含扩展名的名称部分
    const dotIdx = defaultValue.lastIndexOf(".");
    inputRef.current?.setSelectionRange(0, dotIdx > 0 ? dotIdx : defaultValue.length);
  }, [defaultValue]);

  return (
    <input
      ref={inputRef}
      value={value}
      onChange={(e) => setValue(e.target.value)}
      onKeyDown={(e) => {
        if (e.key === "Enter") {
          e.preventDefault();
          const trimmed = value.trim();
          if (trimmed && trimmed !== defaultValue) onConfirm(trimmed);
          else onCancel();
        }
        if (e.key === "Escape") onCancel();
      }}
      onBlur={() => {
        const trimmed = value.trim();
        if (trimmed && trimmed !== defaultValue) onConfirm(trimmed);
        else onCancel();
      }}
      className="flex-1 min-w-0 bg-accent/60 text-xs text-foreground rounded px-1 py-0.5 outline-none ring-1 ring-[var(--em-primary)]"
      onClick={(e) => e.stopPropagation()}
    />
  );
}

/* ── InlineCreateInput (for new file/folder creation) ── */

export function InlineCreateInput({
  placeholder,
  onConfirm,
  onCancel,
}: {
  placeholder: string;
  onConfirm: (value: string) => void;
  onCancel: () => void;
}) {
  const [value, setValue] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);
  useEffect(() => { inputRef.current?.focus(); }, []);

  return (
    <input
      ref={inputRef}
      value={value}
      onChange={(e) => setValue(e.target.value)}
      onKeyDown={(e) => {
        if (e.key === "Enter") {
          e.preventDefault();
          const trimmed = value.trim();
          if (trimmed) onConfirm(trimmed);
          else onCancel();
        }
        if (e.key === "Escape") onCancel();
      }}
      onBlur={() => {
        const trimmed = value.trim();
        if (trimmed) onConfirm(trimmed);
        else onCancel();
      }}
      placeholder={placeholder}
      className="w-full bg-accent/60 text-xs text-foreground rounded px-1 py-0.5 outline-none ring-1 ring-[var(--em-primary)] placeholder:text-muted-foreground/50"
      onClick={(e) => e.stopPropagation()}
    />
  );
}
