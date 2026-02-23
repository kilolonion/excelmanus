"use client";

import { CheckCircle2, Circle, Loader2 } from "lucide-react";
import type { TaskItem } from "@/lib/types";

interface TaskListProps {
  items: TaskItem[];
}

function StatusIcon({ status }: { status: string }) {
  switch (status) {
    case "done":
    case "completed":
      return <CheckCircle2 className="h-4 w-4" style={{ color: "var(--em-primary)" }} />;
    case "running":
    case "in_progress":
      return <Loader2 className="h-4 w-4 animate-spin" style={{ color: "var(--em-primary-light)" }} />;
    default:
      return <Circle className="h-4 w-4 text-muted-foreground" />;
  }
}

export function TaskList({ items }: TaskListProps) {
  if (items.length === 0) return null;

  return (
    <div className="my-2 space-y-1">
      {items.map((item) => (
        <div key={item.index} className="flex items-center gap-2 text-sm">
          <StatusIcon status={item.status} />
          <span
            className={
              item.status === "done" || item.status === "completed"
                ? "text-muted-foreground line-through"
                : ""
            }
          >
            {item.content}
          </span>
        </div>
      ))}
    </div>
  );
}
