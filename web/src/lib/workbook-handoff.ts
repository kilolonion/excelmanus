import { sendMessage } from "./chat-actions";
import { acknowledgedSelectionVersion, cellRefFromIndex, flushWorkbookEdits, hasPendingWorkbookEdits, isWorkbookEditPaused } from "./excel-cell-edit";
import { formatWorkbookMessage, assertWorkbookRequestCurrent, type PreparedWorkbookRequest } from "./workbook-conversation";
import { useChatStore } from "@/stores/chat-store";
import { useUIStore } from "@/stores/ui-store";
import type { WorkbookHandoff } from "@/stores/workbook-workflow-store";
import type { WorkbookSheetContext } from "./workbook-context";
import { prepareWorkbookMergeSources } from "./workbook-group-actions";

export interface WorkbookActionContext extends WorkbookSheetContext {
  operation: string;
  parameters: Record<string, unknown>;
  instruction: string;
}

export const WORKBOOK_OPERATION_LABELS: Record<string, string> = {
  "merge-workbooks": "合并表格",
  filter: "筛选", chart: "图表", pivot: "透视表", "conditional-format": "条件格式",
  "data-validation": "数据验证", "generate-formula": "生成公式", dedupe: "去重", sort: "排序",
  "conflict-replan": "重新规划冲突修改", "clean-selection": "清洗选区", complex: "复杂表格操作",
};

export function workbookOperationKind(command: string): string {
  return Object.keys(WORKBOOK_OPERATION_LABELS).find((kind) => command.includes(kind)) ?? "complex";
}

/** Copy JSON data only; engine identities are meaningless outside the captured workbook. */
export function captureWorkbookParameters(parameters: unknown): Record<string, unknown> {
  const encoded = JSON.stringify(parameters ?? {}, (key, value) => ["unitId", "subUnitId"].includes(key) ? undefined : value);
  if (encoded.length > 64000) throw new Error("操作参数较多，请缩小范围后再交给 Agent");
  const parsed = JSON.parse(encoded);
  return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed : {};
}

export function workbookCommandRange(parameters: Record<string, unknown> | undefined, fallback?: string): string | undefined {
  const ranges = parameters?.ranges ?? (parameters?.range ? [parameters.range] : []);
  if (!Array.isArray(ranges) || !ranges.length || ranges.length > 64) return fallback;
  const addresses: string[] = [];
  for (const range of ranges) {
    if (!range || ![range.startRow, range.startColumn, range.endRow, range.endColumn].every((v) => Number.isInteger(v) && v >= 0)) return fallback;
    addresses.push(`${cellRefFromIndex(range.startRow, range.startColumn)}:${cellRefFromIndex(range.endRow, range.endColumn)}`);
  }
  return addresses.join(",");
}

export async function sendWorkbookHandoff(handoff: WorkbookHandoff, instruction: string, parameters: Record<string, unknown>) {
  const chat = useChatStore.getState();
  if (chat.isStreaming || chat.pendingApproval || chat.pendingQuestion) throw new Error("请先完成当前任务或待确认事项，再生成新方案");
  if (useUIStore.getState().configReady !== true) throw new Error("请先完成模型配置");
  if (!handoff.file.workspaceId) throw new Error("请从已绑定工作区的对话中打开表格");
  const request: PreparedWorkbookRequest = { text: "", sessionId: handoff.sessionId, workspaceKey: handoff.file.workspaceKey, chatMode: "plan" };
  assertWorkbookRequestCurrent(request);
  let version = handoff.version;
  if (handoff.operation === "merge-workbooks") {
    parameters = { ...parameters, sources: await prepareWorkbookMergeSources(parameters.sources, handoff.sessionId, handoff.file) };
  }
  if (handoff.operation !== "conflict-replan") {
    await flushWorkbookEdits(handoff.file);
    if (isWorkbookEditPaused(handoff.file) || hasPendingWorkbookEdits(handoff.file)) throw new Error("请先处理未保存的修改，再生成操作方案");
    version = acknowledgedSelectionVersion(handoff.file, version);
  }
  assertWorkbookRequestCurrent(request);
  const context: WorkbookActionContext = {
    workspace_id: handoff.file.workspaceId, path: handoff.file.relative,
    sheet: handoff.sheet ?? "", range: handoff.range ?? "", observed_version: version,
    operation: handoff.operation, instruction: instruction.trim(), parameters: captureWorkbookParameters(parameters),
  };
  request.workbookAction = context;
  request.sheetContext = { workspace_id: context.workspace_id, path: context.path, sheet: context.sheet, range: context.range, observed_version: version };
  request.text = formatWorkbookMessage(`请为“${WORKBOOK_OPERATION_LABELS[handoff.operation] ?? "表格操作"}”生成可确认的执行方案。${instruction.trim() ? `\n要求：${instruction.trim()}` : ""}\n操作参数随本次请求附带。先核对最新文件，展示方案并等待确认。`,
    { file: handoff.file, sheet: handoff.sheet, showSheet: false, layout: "embedded" }, version);
  const accepted = await sendMessage(request.text, undefined, handoff.sessionId, undefined, request);
  if (!accepted) throw new Error("当前有消息正在提交，请稍后重试");
  return true;
}
