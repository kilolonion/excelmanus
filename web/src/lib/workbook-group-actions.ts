import { useExcelStore } from "@/stores/excel-store";
import { useSessionStore } from "@/stores/session-store";
import { useWorkbookConversationStore, workbookViewKey } from "@/stores/workbook-conversation-store";
import { useWorkbookWorkflowStore } from "@/stores/workbook-workflow-store";
import { fileRefFromSession, workspaceKeyFromSession, type WorkspaceFileRef } from "./workspace-file-ref";
import { acknowledgedWorkbookVersion, acknowledgedSelectionVersion, flushWorkbookEdits, hasPendingWorkbookEdits, isWorkbookEditPaused } from "./excel-cell-edit";
import { fileBaseName } from "./revision-display";
import { isSpreadsheetFile } from "./file-kind";

/** Reused for the initial merge plan and its retry; every source must be saved. */
export async function prepareWorkbookMergeSources(sources: unknown, sessionId: string, primary: WorkspaceFileRef) {
  const state = useSessionStore.getState();
  const session = state.sessions.find((entry) => entry.id === sessionId);
  if (state.activeSessionId !== sessionId || workspaceKeyFromSession(session) !== primary.workspaceKey) throw new Error("已切换对话，请重新选择合并来源");
  if (!Array.isArray(sources) || sources.length < 2 || sources.length > 3) throw new Error("合并需要两到三张来源表格");
  const seen = new Set<string>();
  const saved = [];
  for (const source of sources) {
    if (!source || source.workspace_id !== primary.workspaceId || typeof source.path !== "string" || !isSpreadsheetFile(source.path)) throw new Error("合并来源不属于当前工作区");
    const file = fileRefFromSession(source.path, session);
    if (seen.has(file.relative)) throw new Error("合并来源不能重复");
    seen.add(file.relative);
    await flushWorkbookEdits(file);
    if (isWorkbookEditPaused(file) || hasPendingWorkbookEdits(file)) throw new Error(`${source.path} 还有未保存的修改`);
    saved.push({ ...source, path: file.relative, observed_version: source.range
      ? acknowledgedSelectionVersion(file, source.observed_version) : acknowledgedWorkbookVersion(file, source.observed_version) });
  }
  if (!seen.has(primary.relative)) throw new Error("合并来源缺少主表");
  return saved;
}

/** Capture every source before awaiting saves, then retain those exact sources in the workflow. */
export async function prepareWorkbookGroupAction(kind: "compare" | "merge" | "reference", paths: string[]) {
  const state = useSessionStore.getState();
  const session = state.sessions.find((entry) => entry.id === state.activeSessionId);
  if (!session) throw new Error("请先打开当前对话的表格");
  const files = [...new Set(paths)].filter(isSpreadsheetFile).slice(0, 3).map((path) => fileRefFromSession(path, session));
  const views = useWorkbookConversationStore.getState().views;
  if (!files.length || (kind !== "reference" && files.length < 2)) throw new Error("请先选择至少两张表格");
  for (const file of files) {
    await flushWorkbookEdits(file);
    if (isWorkbookEditPaused(file) || hasPendingWorkbookEdits(file)) throw new Error(`${fileBaseName(file.relative)} 尚有未保存的修改，请先处理`);
  }
  const current = useSessionStore.getState();
  if (current.activeSessionId !== session.id || workspaceKeyFromSession(current.sessions.find((item) => item.id === session.id)) !== files[0].workspaceKey) {
    throw new Error("已切换对话，请在当前工作区重新操作");
  }
  if (kind === "reference") {
    useExcelStore.getState().mentionFilesToInput(files.map((file) => ({ path: file.relative, filename: fileBaseName(file.relative) })));
  } else if (kind === "compare") {
    useExcelStore.getState().openCompare(files[0].relative, files[1].relative);
  } else {
    const sources = files.map((file) => {
      const view = views[workbookViewKey(session.id, file)];
      return { workspace_id: file.workspaceId, path: file.relative, sheet: view?.sheet ?? "", range: "",
        observed_version: acknowledgedWorkbookVersion(file, view?.version) };
    });
    useWorkbookWorkflowStore.getState().openHandoff({ sessionId: session.id, file: files[0], operation: "merge-workbooks",
      sheet: sources[0].sheet, version: sources[0].observed_version,
      parameters: { sources, primary_path: files[0].relative, output: "new_workbook" } });
  }
}
