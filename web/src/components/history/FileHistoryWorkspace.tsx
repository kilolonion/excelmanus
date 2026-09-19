"use client";

import type { ReactNode } from "react";
import { Clock, History } from "lucide-react";
import { OperationTimeline } from "@/components/excel/OperationTimeline";
import { RevisionTimelinePanel } from "@/components/chat/CheckpointTimeline";
import type { ExcelDiffEntry, HistorySubview } from "@/stores/excel-store";

function SegmentButton({
  active,
  onClick,
  icon,
  label,
  count,
}: {
  active: boolean;
  onClick: () => void;
  icon: ReactNode;
  label: string;
  count?: number;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`flex-1 flex items-center justify-center gap-1.5 h-8 rounded-lg text-xs font-medium transition-colors ${
        active
          ? "bg-background text-foreground shadow-sm"
          : "text-muted-foreground hover:text-foreground"
      }`}
    >
      {icon}
      {label}
      {count != null && count > 0 && (
        <span
          className={`min-w-4 px-1 rounded-full text-[10px] leading-4 ${
            active ? "bg-muted text-foreground" : "bg-muted/70"
          }`}
        >
          {count > 99 ? "99+" : count}
        </span>
      )}
    </button>
  );
}

export function FileHistoryWorkspace({
  filePath,
  active,
  view,
  onViewChange,
  revisionCount,
  operationCount,
  cellDiffs,
}: {
  filePath: string | null;
  active: boolean;
  view: HistorySubview;
  onViewChange: (view: HistorySubview) => void;
  revisionCount?: number;
  operationCount?: number;
  cellDiffs?: ExcelDiffEntry[];
}) {
  const hint =
    view === "revisions"
      ? "整份文件的写入快照。恢复会覆盖当前文件，不影响对话本身。"
      : "本会话里 AI 执行过的每一步。可按条撤销，不一定只改当前文件。";

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="shrink-0 px-3 pt-3 pb-2 space-y-2 border-b border-border/70">
        <div className="flex rounded-xl bg-muted/60 p-0.5">
          <SegmentButton
            active={view === "revisions"}
            onClick={() => onViewChange("revisions")}
            icon={<History className="h-3 w-3" />}
            label="文件版本"
            count={revisionCount}
          />
          <SegmentButton
            active={view === "operations"}
            onClick={() => onViewChange("operations")}
            icon={<Clock className="h-3 w-3" />}
            label="操作记录"
            count={operationCount}
          />
        </div>
        <p className="text-[11px] leading-4 text-muted-foreground">{hint}</p>
      </div>
      {view === "operations" && cellDiffs && cellDiffs.length > 0 && (
        <div className="shrink-0 border-b border-border bg-muted/20 max-h-[88px] overflow-y-auto">
          <div className="px-3 py-1.5 text-[10px] text-muted-foreground font-medium">
            本次会话单元格改动
          </div>
          {cellDiffs.map((d, i) => {
            const time = new Date(d.timestamp).toLocaleTimeString("zh-CN", {
              hour: "2-digit",
              minute: "2-digit",
              second: "2-digit",
            });
            return (
              <div key={`${d.toolCallId}-${i}`} className="px-3 py-0.5 text-[10px] text-muted-foreground">
                <span className="text-foreground/70">{time}</span>{" "}
                <span>{d.affectedRange}</span>{" "}
                <span>({d.changes.length} 格)</span>
              </div>
            );
          })}
        </div>
      )}
      <div className="flex-1 min-h-0 overflow-hidden">
        <div className={`h-full ${view === "revisions" ? "" : "hidden"}`}>
          <RevisionTimelinePanel filePath={filePath} active={active && view === "revisions"} />
        </div>
        <div className={`h-full ${view === "operations" ? "" : "hidden"}`}>
          <OperationTimeline filePath={filePath} />
        </div>
      </div>
    </div>
  );
}
