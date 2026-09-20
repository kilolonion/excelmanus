import type { SubagentRun, SubagentRunStatus } from "@/lib/types";

export function isSubagentActive(status: SubagentRunStatus): boolean {
  return status === "queued" || status === "running" || status === "waiting_input";
}

export function subagentChangedFiles(run: SubagentRun): string[] {
  return [...new Set([
    ...(run.changed_files ?? []),
    ...(run.result?.structured_changes.map((change) => change.path) ?? []),
  ])];
}

/** 只在新收到终态时刷新文件视图，避免每次轮询都重载已打开的工作簿。 */
export function completedSubagentFiles(previous: SubagentRun[], next: SubagentRun[]): string[] {
  const byId = new Map(previous.map((run) => [run.run_id, run]));
  return [...new Set(next.flatMap((run) => {
    const prior = byId.get(run.run_id);
    if (!run.background || isSubagentActive(run.status)
      || (prior?.status === run.status && prior.finished_at === run.finished_at)) return [];
    return subagentChangedFiles(run);
  }))];
}

export const SUBAGENT_STATUS_LABELS: Record<SubagentRunStatus, string> = {
  queued: "排队中",
  running: "进行中",
  waiting_input: "等待回答",
  completed: "已完成",
  paused: "已暂停",
  interrupted: "已中断",
  aborted: "已取消",
  error: "执行失败",
  "max-tokens": "达到执行上限",
  refusal: "未执行",
};
