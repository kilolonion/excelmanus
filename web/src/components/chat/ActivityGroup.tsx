"use client";

import { useState } from "react";
import {
  CheckCircle2,
  ChevronDown,
  Clock,
  Loader2,
  XCircle,
} from "lucide-react";
import { ToolCallCard } from "./ToolCallCard";
import { activityGroupTitle, toolActionTitle } from "@/lib/tool-labels";
import type { AssistantBlock } from "@/lib/types";

type ToolBlock = Extract<AssistantBlock, { type: "tool_call" }>;

export interface ActivityToolItem {
  block: ToolBlock;
  origIndex: number;
}

function groupStatus(tools: ToolBlock[]): "pending" | "running" | "error" | "success" {
  if (tools.some((t) => t.status === "pending")) return "pending";
  if (tools.some((t) => t.status === "running" || t.status === "streaming")) return "running";
  if (tools.some((t) => t.status === "error")) return "error";
  return "success";
}

export function ActivityGroup({
  tools,
  defaultCollapsed,
}: {
  tools: ActivityToolItem[];
  defaultCollapsed: boolean;
}) {
  const [collapsed, setCollapsed] = useState(defaultCollapsed);
  const blocks = tools.map((t) => t.block);
  const status = groupStatus(blocks);
  const title = activityGroupTitle(blocks);
  const titles = blocks.map((b) => toolActionTitle(b.name, b.args));

  if (collapsed) {
    const CollapsedIcon =
      status === "error" ? XCircle
      : status === "pending" ? Clock
      : CheckCircle2;
    const collapsedLabel =
      status === "error" ? `已完成 ${blocks.length} 个步骤，有失败`
      : status === "pending" ? `等待授权 · ${blocks.length} 个步骤`
      : `已完成 ${blocks.length} 个步骤`;
    return (
      <button
        type="button"
        onClick={() => setCollapsed(false)}
        className="group/act my-2 flex w-full items-center gap-2 rounded-2xl border border-[var(--em-hairline)] bg-background px-3 py-2 text-left hover:bg-[var(--em-fill)] transition-colors"
      >
        <CollapsedIcon
          className={`h-4 w-4 flex-shrink-0 ${
            status === "error" ? "text-red-500"
            : status === "pending" ? "text-amber-500"
            : "text-[var(--em-primary)]"
          }`}
        />
        <span className="text-[13px] font-medium text-foreground whitespace-nowrap">
          {collapsedLabel}
        </span>
        <span className="hidden sm:inline min-w-0 truncate text-[12px] text-muted-foreground">
          {titles.join(" · ")}
        </span>
        <ChevronDown className="ml-auto h-4 w-4 flex-shrink-0 text-muted-foreground/60 group-hover/act:text-foreground" />
      </button>
    );
  }

  const StatusIcon =
    status === "pending" ? Clock
    : status === "running" ? Loader2
    : status === "error" ? XCircle
    : CheckCircle2;

  const badge =
    status === "pending" ? { text: "等待授权", cls: "bg-amber-500/10 text-amber-700 dark:text-amber-400" }
    : status === "running" ? { text: "进行中", cls: "bg-[var(--em-primary-alpha-10)] text-[var(--em-primary)]" }
    : status === "error" ? { text: "有失败", cls: "bg-red-500/10 text-red-600" }
    : { text: "已完成", cls: "bg-[var(--em-primary-alpha-10)] text-[var(--em-primary)]" };

  return (
    <div className="my-2 rounded-2xl border border-[var(--em-hairline)] bg-background overflow-hidden">
      <div className="flex items-center gap-2 px-3 sm:px-3.5 py-2.5">
        <StatusIcon
          className={`h-4 w-4 flex-shrink-0 ${
            status === "pending" ? "text-amber-500"
            : status === "running" ? "animate-spin text-[var(--em-primary)]"
            : status === "error" ? "text-red-500"
            : "text-[var(--em-primary)]"
          }`}
        />
        <span className="text-[13px] font-semibold text-foreground truncate">{title}</span>
        <span className={`text-[11px] font-medium px-1.5 py-px rounded-full ${badge.cls}`}>
          {badge.text}
        </span>
        <button
          type="button"
          onClick={() => setCollapsed(true)}
          className="ml-auto text-muted-foreground/50 hover:text-foreground p-1 rounded-md"
          title="收起步骤"
          aria-label="收起步骤"
        >
          <ChevronDown className="h-4 w-4 rotate-180" />
        </button>
      </div>
      <div className="px-3 sm:px-3.5 pb-2">
        {tools.map((item, i) => (
          <ToolCallCard
            key={item.block.toolCallId || `${item.origIndex}-${item.block.name}`}
            toolCallId={item.block.toolCallId}
            name={item.block.name}
            args={item.block.args}
            status={item.block.status}
            result={item.block.result}
            error={item.block.error}
            isLast={i === tools.length - 1}
          />
        ))}
      </div>
    </div>
  );
}
