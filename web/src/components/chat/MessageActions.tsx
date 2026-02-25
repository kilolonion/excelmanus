"use client";

import React, { useCallback, useState } from "react";
import { Copy, Check, ThumbsUp, ThumbsDown } from "lucide-react";
import type { AssistantBlock } from "@/lib/types";

interface MessageActionsProps {
  blocks: AssistantBlock[];
}

/**
 * Extract plain text content from assistant message blocks.
 * Excludes tool calls, thinking, token stats, etc.
 */
function extractPlainText(blocks: AssistantBlock[]): string {
  return blocks
    .filter((b) => b.type === "text")
    .map((b) => (b as Extract<AssistantBlock, { type: "text" }>).content)
    .join("\n\n")
    .trim();
}

export const MessageActions = React.memo(function MessageActions({
  blocks,
}: MessageActionsProps) {
  const [copied, setCopied] = useState(false);
  const [feedback, setFeedback] = useState<"up" | "down" | null>(null);

  const handleCopy = useCallback(() => {
    const text = extractPlainText(blocks);
    if (!text) return;
    navigator.clipboard.writeText(text).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    }).catch(() => {});
  }, [blocks]);

  const handleFeedback = useCallback((type: "up" | "down") => {
    setFeedback((prev) => (prev === type ? null : type));
    // TODO: Send feedback to backend API when available
  }, []);

  const hasText = blocks.some((b) => b.type === "text");
  if (!hasText) return null;

  return (
    <div
      className="flex items-center gap-1 mt-1.5 opacity-0 group-hover/msg:opacity-100 transition-opacity duration-200 touch-show"
      role="toolbar"
      aria-label="消息操作"
    >
      <ActionButton
        onClick={handleCopy}
        active={copied}
        activeColor="text-emerald-500"
        label={copied ? "已复制" : "复制"}
      >
        {copied ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}
      </ActionButton>

      <ActionButton
        onClick={() => handleFeedback("up")}
        active={feedback === "up"}
        activeColor="text-[var(--em-primary)]"
        label="有用"
      >
        <ThumbsUp className="h-3.5 w-3.5" />
      </ActionButton>

      <ActionButton
        onClick={() => handleFeedback("down")}
        active={feedback === "down"}
        activeColor="text-[var(--em-error)]"
        label="无用"
      >
        <ThumbsDown className="h-3.5 w-3.5" />
      </ActionButton>
    </div>
  );
});

function ActionButton({
  onClick,
  active,
  activeColor,
  label,
  children,
}: {
  onClick: () => void;
  active: boolean;
  activeColor: string;
  label: string;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`h-8 w-8 inline-flex items-center justify-center rounded-md transition-colors ${
        active
          ? activeColor
          : "text-muted-foreground hover:text-foreground hover:bg-muted/60"
      }`}
      aria-label={label}
      title={label}
    >
      {children}
    </button>
  );
}
