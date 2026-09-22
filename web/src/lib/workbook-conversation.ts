import { formatFileMention } from "@/components/chat/chat-input-insert";
import { fileRefKey, fileRefFromSession, workspaceKeyForSessionId, normalizeRelativePath } from "@/lib/workspace-file-ref";
import { acknowledgedWorkbookVersion, acknowledgedSelectionVersion, flushWorkbookEdits, hasPendingWorkbookEdits, isWorkbookEditPaused } from "@/lib/excel-cell-edit";
import { useWorkbookConversationStore, workbookViewKey, type WorkbookConversationTarget } from "@/stores/workbook-conversation-store";
import { useSessionStore } from "@/stores/session-store";
import { extractTypedFileMentions } from "@/lib/mention-existence";
import { extractMentions } from "@/components/chat/mention-tokens";
import { isSpreadsheetFile } from "./file-kind";
import { currentWorkbookSendContext, currentWorkbookGroupContexts, workbookSheetContext, type WorkbookSheetContext, type WorkbookMessageContext } from "./workbook-context";
import type { WorkbookActionContext } from "./workbook-handoff";

export function currentWorkbookTarget(sessionId: string | null): WorkbookConversationTarget | null {
  if (!sessionId) return null;
  const target = useWorkbookConversationStore.getState().targets[sessionId];
  return target?.file.workspaceKey === workspaceKeyForSessionId(sessionId) ? target : null;
}

export function formatWorkbookMessage(text: string, target: WorkbookConversationTarget, version?: string | null): string {
  // An explicit file/selection is already visible in the draft and takes precedence.
  if (/@file:/.test(text) || text.trimStart().startsWith("/")) return text;
  const { relative: path } = target.file;
  if (/[\s,;!?\[\]@]/.test(path)) {
    // Existing @file token grammar cannot represent these names. Preserve the
    // literal path instead of truncating it into a different file reference.
    return `当前讨论的表格：${JSON.stringify({ path, sheet: target.sheet, observed_version: version || undefined })}\n\n${text}`;
  }
  return `${formatFileMention({ path, version })}\n${target.sheet ? `当前工作表：${JSON.stringify(target.sheet)}\n` : ""}\n${text}`;
}

export interface PreparedWorkbookRequest {
  text: string;
  sheetContext?: WorkbookSheetContext;
  sheetContexts?: WorkbookSheetContext[];
  implicitGroupPaths?: string;
  sessionId: string | null;
  workspaceKey: string;
  implicitTargetKey?: string;
  chatMode?: "plan";
  workbookAction?: WorkbookActionContext;
}

export function assertWorkbookRequestCurrent(request: PreparedWorkbookRequest) {
  if (useSessionStore.getState().activeSessionId !== request.sessionId
    || workspaceKeyForSessionId(request.sessionId) !== request.workspaceKey) {
    throw new Error("已切换对话，草稿已保留，请确认后重新发送");
  }
  const current = currentWorkbookSendContext(request.sessionId);
  if (request.implicitTargetKey && (!current || fileRefKey(current.target.file) !== request.implicitTargetKey)) {
    throw new Error("当前表格已改变，草稿已保留，请确认后重新发送");
  }
  if (request.implicitGroupPaths && JSON.stringify(currentWorkbookGroupContexts(request.sessionId).map((view) => view.path)) !== request.implicitGroupPaths) {
    throw new Error("同屏表格已改变，草稿已保留，请确认后重新发送");
  }
}

/** Capture one snapshot before any await; flush every referenced workbook in its original workspace. */
export async function prepareWorkbookRequest(text: string, sessionId: string | null, replay?: WorkbookMessageContext): Promise<PreparedWorkbookRequest> {
  const request: PreparedWorkbookRequest = { text, sessionId, workspaceKey: workspaceKeyForSessionId(sessionId) };
  if (!sessionId || text.trimStart().startsWith("/")) return request;
  const session = useSessionStore.getState().sessions.find((item) => item.id === sessionId);
  const explicit = extractTypedFileMentions(text);
  const tokens = extractMentions(text).filter((token) => token.kind === "file" && isSpreadsheetFile(token.value));
  if (replay) {
    const references = replay.sheet_contexts;
    if (!Array.isArray(references) || references.length < 2 || references.length > 3
      || [...references, ...(replay.sheet_context ? [replay.sheet_context] : [])].some((view) => !view
        || view.workspace_id !== session?.workspaceId || typeof view.path !== "string" || !isSpreadsheetFile(view.path))) {
      throw new Error("原消息的表格引用不属于当前工作区，请重新选择表格");
    }
    const paths = references.map((view) => normalizeRelativePath(view.path));
    const focusedPath = replay.sheet_context ? normalizeRelativePath(replay.sheet_context.path) : undefined;
    // Editing a message to explicitly address another file/range takes precedence.
    if (explicit.some((path) => !paths.includes(normalizeRelativePath(path))) || tokens.some((token) => token.rangeSpec)
      || (focusedPath && explicit.length && !explicit.some((path) => normalizeRelativePath(path) === focusedPath))) replay = undefined;
    else if (focusedPath && !paths.includes(focusedPath)) throw new Error("原消息的聚焦表格不在引用列表中");
  }
  const context = explicit.length || replay ? null : currentWorkbookSendContext(sessionId);
  const group = replay ? replay.sheet_contexts.map((view) => ({ ...view })) : explicit.length ? [] : currentWorkbookGroupContexts(sessionId);
  if (!replay && group.length > 1) request.implicitGroupPaths = JSON.stringify(group.map((view) => view.path));
  const views = useWorkbookConversationStore.getState().views;
  if (context) {
    request.implicitTargetKey = fileRefKey(context.target.file);
    if (context.view?.status !== "ready") throw new Error(context.view?.error || "正在加载表格，请稍后发送");
  }
  const files = new Map((explicit.filter(isSpreadsheetFile)).map((path) => {
    const file = fileRefFromSession(path, session);
    return [fileRefKey(file), file] as const;
  }));
  if (context) files.set(fileRefKey(context.target.file), context.target.file);
  for (const view of group) {
    const file = fileRefFromSession(view.path, session);
    files.set(fileRefKey(file), file);
  }
  for (const file of files.values()) {
    await flushWorkbookEdits(file);
    if (isWorkbookEditPaused(file) || hasPendingWorkbookEdits(file)) {
      throw new Error(`表格 ${file.relative} 还有未保存的修改，请先处理保存问题再发送`);
    }
  }
  assertWorkbookRequestCurrent(request);

  // Only locally acknowledged saves can advance a captured reference, never a remote notification.
  for (const token of [...tokens].reverse()) {
    const file = fileRefFromSession(token.value, session);
    const viewed = views[workbookViewKey(sessionId, file)];
    const version = token.version ?? (viewed?.status === "ready" ? viewed.version : undefined);
    const acknowledged = token.rangeSpec
      ? acknowledgedSelectionVersion(file, version) : acknowledgedWorkbookVersion(file, version);
    if (acknowledged) {
      const raw = token.version ? token.raw.slice(0, -(token.version.length + 1)) : token.raw;
      request.text = request.text.slice(0, token.start) + `${raw}@${acknowledged}` + request.text.slice(token.end);
    }
  }
  if (context) {
    const version = context.view?.range
      ? acknowledgedSelectionVersion(context.target.file, context.view.version)
      : acknowledgedWorkbookVersion(context.target.file, context.view?.version);
    request.text = formatWorkbookMessage(text, context.target, version);
    request.sheetContext = workbookSheetContext(context);
    if (request.sheetContext && version) request.sheetContext.observed_version = version;
  }
  if (replay?.sheet_context) {
    const captured = replay.sheet_context;
    const file = fileRefFromSession(captured.path, session);
    const version = captured.range ? acknowledgedSelectionVersion(file, captured.observed_version) : acknowledgedWorkbookVersion(file, captured.observed_version);
    request.sheetContext = { ...captured, ...(version ? { observed_version: version } : {}) };
  }
  if (group.length > 1) request.sheetContexts = group.map((view) => {
    const file = fileRefFromSession(view.path, session);
    const version = view.range ? acknowledgedSelectionVersion(file, view.observed_version) : acknowledgedWorkbookVersion(file, view.observed_version);
    return { ...view, ...(version ? { observed_version: version } : {}) };
  });
  return request;
}

export async function prepareWorkbookMessage(text: string, sessionId: string | null): Promise<string> {
  return (await prepareWorkbookRequest(text, sessionId)).text;
}
