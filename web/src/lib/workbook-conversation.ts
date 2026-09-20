import { formatFileMention } from "@/components/chat/chat-input-insert";
import { fileRefKey, workspaceKeyForSessionId } from "@/lib/workspace-file-ref";
import { acknowledgedWorkbookVersion, flushWorkbookEdits, hasPendingWorkbookEdits, isWorkbookEditPaused } from "@/lib/excel-cell-edit";
import { useWorkbookConversationStore, workbookViewKey, type WorkbookConversationTarget } from "@/stores/workbook-conversation-store";
import { useSessionStore } from "@/stores/session-store";
import { extractTypedFileMentions } from "@/lib/mention-existence";

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

/** Capture BEFORE awaiting saves; switching sheets/files cannot retarget a send. */
export async function prepareWorkbookMessage(text: string, sessionId: string | null): Promise<string> {
  const target = currentWorkbookTarget(sessionId);
  if (!target || !sessionId || text.trimStart().startsWith("/")) return text;
  const explicit = extractTypedFileMentions(text);
  if (explicit.length && !explicit.includes(target.file.relative)) return text;
  const view = useWorkbookConversationStore.getState().views[workbookViewKey(sessionId, target.file)];
  if (view?.status !== "ready") throw new Error(view?.error || "正在加载表格，请稍后发送");
  await flushWorkbookEdits(target.file);
  if (isWorkbookEditPaused(target.file) || hasPendingWorkbookEdits(target.file)) {
    throw new Error("表格还有未保存的修改，请先处理保存问题再发送");
  }
  if (useSessionStore.getState().activeSessionId !== sessionId) {
    throw new Error("已切换对话，草稿已保留，请确认后重新发送");
  }
  const current = currentWorkbookTarget(sessionId);
  if (!current || fileRefKey(current.file) !== fileRefKey(target.file)) {
    throw new Error("当前表格已改变，草稿已保留，请确认后重新发送");
  }
  // A change notification alone does not prove that the user has seen its data.
  return formatWorkbookMessage(text, target, acknowledgedWorkbookVersion(target.file, view.version));
}
