"use client";

import { useState } from "react";
import {
  CheckCircle2,
  ChevronDown,
  Clock,
  CircleStop,
  Loader2,
  XCircle,
} from "lucide-react";
import { ToolCallCard } from "./ToolCallCard";
import { activityGroupTitle, toolActionTitle } from "@/lib/tool-labels";
import { nestToolCallsByParent } from "@/lib/tool-call-tree";
import type { AssistantBlock } from "@/lib/types";

type ToolBlock = Extract<AssistantBlock, { type: "tool_call" }>;

export interface ActivityToolItem {
  block: ToolBlock;
  origIndex: number;
}

function groupStatus(tools: ToolBlock[]): "pending" | "queued" | "running" | "cancelled" | "error" | "success" {
  if (tools.some((t) => t.status === "pending" && t.executionState !== "queued")) return "pending";
  if (tools.some((t) => t.status === "running" || t.status === "streaming")) return "running";
  if (tools.some((t) => t.executionState === "queued")) return "queued";
  if (tools.some((t) => t.status === "error" && t.executionState !== "cancelled")) return "error";
  if (tools.some((t) => t.executionState === "cancelled")) return "cancelled";
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
  const nests = nestToolCallsByParent(tools.map((t) => t.block));
  const rootBlocks = nests.map((n) => n.item);
  const allBlocks = tools.map((t) => t.block);
  const status = groupStatus(allBlocks);
  const title = activityGroupTitle(rootBlocks.length > 0 ? rootBlocks : allBlocks);
  const titles = rootBlocks.map((b) => toolActionTitle(b.name, b.args));
  const stepCount = Math.max(rootBlocks.length, 1);

  if (collapsed) {
    const CollapsedIcon =
      status === "error" ? XCircle
      : status === "pending" || status === "queued" ? Clock
      : status === "running" ? Loader2
      : status === "cancelled" ? CircleStop
      : CheckCircle2;
    const collapsedLabel =
      status === "error" ? `已完成 ${stepCount} 个步骤，有失败`
      : status === "pending" ? `等待授权 · ${stepCount} 个步骤`
      : status === "queued" ? `等待执行 · ${stepCount} 个步骤`
      : status === "running" ? `进行中 · ${stepCount} 个步骤`
      : status === "cancelled" ? `已结束 · 有取消的步骤`
      : `已完成 ${stepCount} 个步骤`;
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
    status === "pending" || status === "queued" ? Clock
    : status === "running" ? Loader2
    : status === "cancelled" ? CircleStop
    : status === "error" ? XCircle
    : CheckCircle2;

  const badge =
    status === "pending" ? { text: "等待授权", cls: "bg-amber-500/10 text-amber-700 dark:text-amber-400" }
    : status === "queued" ? { text: "等待执行", cls: "bg-muted text-muted-foreground" }
    : status === "cancelled" ? { text: "有取消", cls: "bg-muted text-muted-foreground" }
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
        <span className="min-w-0 truncate text-[13px] font-semibold text-foreground">{title}</span>
        <span className={`text-[11px] font-medium px-1.5 py-px rounded-full whitespace-nowrap shrink-0 ${badge.cls}`}>
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
        {nests.map((node, i) => {
          const isLastRoot = i === nests.length - 1;
          const childCount = node.children.length;
          return (
            <div key={node.item.toolCallId || `${i}-${node.item.name}`}>
              <ToolCallCard
                toolCallId={node.item.toolCallId}
                executionId={node.item.executionId}
                executionState={node.item.executionState}
                name={node.item.name}
                args={node.item.args}
                status={node.item.status}
                result={node.item.result}
                error={node.item.error}
                parentCallId={node.item.parentCallId}
                isLast={isLastRoot && childCount === 0}
              />
              {node.children.map((child, j) => (
                <ToolCallCard
                  key={child.toolCallId || `${i}-${j}-${child.name}`}
                  toolCallId={child.toolCallId}
                  executionId={child.executionId}
                  executionState={child.executionState}
                  name={child.name}
                  args={child.args}
                  status={child.status}
                  result={child.result}
                  error={child.error}
                  parentCallId={child.parentCallId}
                  nested
                  isLast={isLastRoot && j === childCount - 1}
                />
              ))}
            </div>
          );
        })}
      </div>
    </div>
  );
}
