import { apiFetch, buildApiUrl, getAuthHeaders, invalidateWorkbookCaches } from "./api";
import { discardWorkbookEdits, flushWorkbookEdits, isWorkbookEditPaused, workbookEditDraft } from "./excel-cell-edit";
import { useExcelStore } from "@/stores/excel-store";
import { assertWorkbookRequestCurrent } from "./workbook-conversation";
import type { WorkbookWorkflowScope } from "@/stores/workbook-workflow-store";

export interface WorkbookMergeCell {
  id: string; sheet: string; cell: string; field: string;
  base: unknown; local: unknown; remote: unknown; conflict: boolean;
}
export interface WorkbookMergeReview {
  status: "review" | "replan" | "merged";
  content_version?: string;
  reason?: string;
  cells?: WorkbookMergeCell[];
  safe_count?: number;
  conflict_count?: number;
}

function assertScope(scope: WorkbookWorkflowScope) {
  assertWorkbookRequestCurrent({ text: "", sessionId: scope.sessionId, workspaceKey: scope.file.workspaceKey });
}

export async function captureWorkbookDraft(scope: WorkbookWorkflowScope) {
  assertScope(scope);
  await flushWorkbookEdits(scope.file);
  assertScope(scope);
  if (!isWorkbookEditPaused(scope.file)) throw new Error("当前文件已没有待处理的保存冲突");
  return workbookEditDraft(scope.file);
}

export async function reviewWorkbookMerge(scope: WorkbookWorkflowScope, draft: string, apply?: {
  version: string; choices: Record<string, "local" | "remote">; operationId: string;
}): Promise<WorkbookMergeReview> {
  assertScope(scope);
  if (workbookEditDraft(scope.file) !== draft) throw new Error("草稿已改变，请重新核对");
  const payload = JSON.parse(draft);
  if (!payload.batches?.length) throw new Error("没有可合并的草稿");
  const response = await apiFetch(buildApiUrl("/workbooks/merge-review"), {
    signal: AbortSignal.timeout(60_000),
    method: "POST", headers: { "Content-Type": "application/json", ...getAuthHeaders() },
    body: JSON.stringify({ path: scope.file.relative, session_id: scope.sessionId, workspace_id: scope.file.workspaceId,
      batches: payload.batches, apply: Boolean(apply), expected_version: apply?.version,
      choices: apply?.choices, operation_id: apply?.operationId }),
  });
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || result.detail || "无法核对冲突，请稍后重试");
  if (apply && result.status === "merged") {
    // Clear only the exact submitted draft, even if its surface/session has closed.
    if (workbookEditDraft(scope.file) === draft) discardWorkbookEdits(scope.file);
    invalidateWorkbookCaches(scope.file);
    useExcelStore.getState().notifyWorkbookChanged(scope.file.relative, scope.file.workspaceKey, result.content_version, "refresh");
    useExcelStore.getState().bumpWorkspaceFilesVersion();
  }
  return result;
}
