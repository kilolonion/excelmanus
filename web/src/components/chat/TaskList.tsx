"use client";

import { useMemo } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { CheckCircle2, Circle, Loader2, ListChecks } from "lucide-react";
import type { TaskItem } from "@/lib/types";

interface TaskListProps {
  items: TaskItem[];
}

const iconPop = {
  initial: { scale: 0, opacity: 0 },
  animate: { scale: 1, opacity: 1, transition: { type: "spring" as const, stiffness: 500, damping: 25 } },
};

function StatusIcon({ status }: { status: string }) {
  switch (status) {
    case "done":
    case "completed":
      return (
        <motion.span className="flex-shrink-0" variants={iconPop} initial="initial" animate="animate">
          <CheckCircle2 className="h-4 w-4" style={{ color: "var(--em-primary)" }} />
        </motion.span>
      );
    case "running":
    case "in_progress":
      return <Loader2 className="h-4 w-4 flex-shrink-0 animate-spin" style={{ color: "var(--em-primary-light)" }} />;
    case "failed":
      return <Circle className="h-4 w-4 flex-shrink-0" style={{ color: "var(--em-error)" }} />;
    default:
      return <Circle className="h-4 w-4 flex-shrink-0 text-muted-foreground/50" />;
  }
}

const itemVariants = {
  hidden: { opacity: 0, x: -8 },
  show: { opacity: 1, x: 0, transition: { duration: 0.2, ease: "easeOut" as const } },
};

const listVariants = {
  hidden: {},
  show: { transition: { staggerChildren: 0.04 } },
};

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
        <AnimatePresence>
          {pct === 100 && (
            <motion.span
              className="text-[10px] font-medium"
              style={{ color: "var(--em-primary)" }}
              initial={{ scale: 0, opacity: 0 }}
              animate={{ scale: 1, opacity: 1 }}
              transition={{ type: "spring", stiffness: 500, damping: 25, delay: 0.15 }}
            >
              全部完成 ✓
            </motion.span>
          )}
        </AnimatePresence>
      </div>

      {/* Progress bar */}
      <div className="h-[2px] bg-muted/40">
        <div
          className="h-full transition-all duration-500 ease-out rounded-r-full"
          style={{ width: `${pct}%`, backgroundColor: "var(--em-primary)" }}
        />
      </div>

      {/* Task items */}
      <motion.div
        className="px-3 py-2 space-y-1.5"
        variants={listVariants}
        initial="hidden"
        animate="show"
      >
        {items.map((item) => {
          const isDone = item.status === "done" || item.status === "completed";
          return (
            <motion.div key={item.index} className="flex items-start gap-2 text-sm" variants={itemVariants}>
              <StatusIcon status={item.status} />
              <div className="flex flex-col min-w-0">
                <span className={`transition-colors duration-300 ${isDone ? "text-muted-foreground line-through decoration-muted-foreground/40" : ""}`}>
                  {item.content}
                </span>
                {item.verification && (
                  <span className="text-[10px] text-muted-foreground/70 mt-0.5">
                    验证: {item.verification}
                  </span>
                )}
              </div>
            </motion.div>
          );
        })}
      </motion.div>
    </div>
  );
}
