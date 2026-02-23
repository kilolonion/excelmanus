"use client";

import { useState } from "react";
import { Bot, CheckCircle2, ChevronRight, Loader2 } from "lucide-react";
import { Badge } from "@/components/ui/badge";

interface SubagentBlockProps {
  name: string;
  reason: string;
  iterations: number;
  toolCalls: number;
  status: "running" | "done";
  summary?: string;
}

export function SubagentBlock({
  name,
  reason,
  iterations,
  toolCalls,
  status,
  summary,
}: SubagentBlockProps) {
  const [expanded, setExpanded] = useState(false);

  return (
    <div className="my-2 ml-4 border-l-2 border-border pl-3">
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        className="flex w-full items-center gap-2 text-sm text-left cursor-pointer hover:bg-muted/40 rounded -ml-1 pl-1 py-0.5 transition-colors"
      >
        <ChevronRight
          className={`h-3 w-3 text-muted-foreground shrink-0 transition-transform ${expanded ? "rotate-90" : ""}`}
        />
        <Bot className="h-4 w-4 text-muted-foreground shrink-0" />
        <Badge variant="secondary" className="text-xs font-mono">
          {name}
        </Badge>
        {status === "running" ? (
          <Loader2 className="h-3 w-3 animate-spin text-muted-foreground shrink-0" />
        ) : (
          <CheckCircle2 className="h-3 w-3 shrink-0" style={{ color: "var(--em-primary)" }} />
        )}
        <span className="text-xs text-muted-foreground ml-auto whitespace-nowrap">
          {iterations} 轮 · {toolCalls} 工具调用
        </span>
      </button>
      {expanded && (
        <>
          {reason && (
            <p className="text-xs text-muted-foreground mt-1 ml-4">{reason}</p>
          )}
          {summary && status === "done" && (
            <p className="text-xs text-muted-foreground mt-1 ml-4 italic">{summary}</p>
          )}
        </>
      )}
    </div>
  );
}
