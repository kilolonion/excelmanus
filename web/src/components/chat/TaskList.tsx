"use client";

import { useMemo } from "react";
import { CheckCircle2, Circle, Loader2, ListChecks } from "lucide-react";
import type { TaskItem } from "@/lib/types";

interface TaskListProps {
  items: TaskItem[];
}

function StatusIcon({ status }: { status: string }) {
  switch (status) {
    case "done":
    case "completed":
      return <CheckCircle2 className="h-4 w-4 flex-shrink-0" style={{ color: "var(--em-primary)" }} />;
    case "running":
    case "in_progress":
      return <Loader2 className="h-4 w-4 flex-shrink-0 animate-spin" style={{ color: "var(--em-primary-light)" }} />;
    case "failed":
      return <Circle className="h-4 w-4 flex-shrink-0" style={{ color: "var(--em-error)" }} />;
    default:
      return <Circle className="h-4 w-4 flex-shrink-0 text-muted-foreground/50" />;
  }
}

export function TaskList({ items }: TaskListProps) {
  const { doneCount, total, pct } = useMemo(() => {
    const done = items.filter(
      (i) => i.status === "done" || i.status === "completed",
    ).length;
    return { doneCount: done, total: items.length, pct: items.length > 0 ? (done / items.length) * 100 : 0 };
  }, [items]);

  if (items.length === 0) return null;

  return (
    <div className="my-2 rounded-xl border border-border/60 bg-card overflow-hidden">
      {/* Header with progress */}
      <div className="flex items-center gap-2 px-3 py-2 border-b border-border/40">
        <ListChecks className="h-4 w-4 flex-shrink-0" style={{ color: "var(--em-primary)" }} />
        <span className="text-xs font-medium">任务进度</span>
        <span className="text-[10px] text-muted-foreground">
          {doneCount}/{total}
        </span>
        <div className="flex-1" />
        {pct === 100 && (
          <span className="text-[10px] font-medium" style={{ color: "var(--em-primary)" }}>全部完成</span>
        )}
      </div>

      {/* Progress bar */}
      <div className="h-[2px] bg-muted/40">
        <div
          className="h-full transition-all duration-500 ease-out rounded-r-full"
          style={{ width: `${pct}%`, backgroundColor: "var(--em-primary)" }}
        />
      </div>

      {/* Task items */}
      <div className="px-3 py-2 space-y-1.5">
        {items.map((item) => {
          const isDone = item.status === "done" || item.status === "completed";
          return (
            <div key={item.index} className="flex items-start gap-2 text-sm">
              <StatusIcon status={item.status} />
              <div className="flex flex-col min-w-0">
                <span className={isDone ? "text-muted-foreground" : ""}>
                  {item.content}
                </span>
                {item.verification && (
                  <span className="text-[10px] text-muted-foreground/70 mt-0.5">
                    验证: {item.verification}
                  </span>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
