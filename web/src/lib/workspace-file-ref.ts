import { normalizeExcelPath } from "@/lib/api";
import { isSpreadsheetFile } from "@/lib/file-kind";
import { displayFileName } from "@/lib/file-identity";
import type { Session } from "@/lib/types";
import { useSessionStore } from "@/stores/session-store";

/** 第 1 批 FileRef 的 UI 投影。file_id 待后续批次，本批不写这个字段。 */
export interface WorkspaceFileRef {
  workspaceId: string | null;
  workspaceKey: string;
  relative: string;
  observedVersion?: string;
}

export interface ViewLease {
  file: WorkspaceFileRef;
  sessionId: string;
  viewGeneration: number;
  requestId: string;
}

export function normalizeRelativePath(path: string): string {
  return normalizeExcelPath(path).replace(/^\.\//, "");
}

export function workspaceKeyFromSession(
  session?: Pick<Session, "workspaceId" | "workspacePath"> | null,
): string {
  const id = session?.workspaceId?.trim();
  if (id) return `id:${id}`;
  const path = (session?.workspacePath || "").replace(/\\/g, "/").replace(/\/$/, "");
  if (path) return `path:${path}`;
  return "_";
}

export function fileRefFromSession(
  relative: string,
  session?: Pick<Session, "workspaceId" | "workspacePath"> | null,
  observedVersion?: string,
): WorkspaceFileRef {
  const ref: WorkspaceFileRef = {
    workspaceId: session?.workspaceId ?? null,
    workspaceKey: workspaceKeyFromSession(session),
    relative: normalizeRelativePath(relative),
  };
  if (observedVersion) ref.observedVersion = observedVersion;
  return ref;
}

export function fileRefKey(ref: Pick<WorkspaceFileRef, "workspaceKey" | "relative">): string {
  return `${ref.workspaceKey}|${normalizeExcelPath(ref.relative)}`;
}

export function versionStoreKey(path: string, workspaceKey?: string | null): string {
  return `${workspaceKey || "_"}|${normalizeExcelPath(path)}`;
}

/** `_` 是无会话哨兵，不算已绑定工作区。 */
export function isScopedWorkspaceKey(key?: string | null): key is string {
  return Boolean(key) && key !== "_";
}

export function recentFilesForWorkspace<T extends { workspaceKey?: string }>(
  files: T[],
  workspaceKey?: string | null,
): T[] {
  if (!isScopedWorkspaceKey(workspaceKey)) return [];
  return files.filter((item) => item.workspaceKey === workspaceKey && isRecentWorkbook(item));
}

export function sanitizeRecentFiles<T extends { workspaceKey?: string }>(files: T[]): T[] {
  return files.filter((item) => isScopedWorkspaceKey(item.workspaceKey) && isRecentWorkbook(item));
}

/** Recent-file buckets are workbook-only; mutation/recovery events may mention any workspace file. */
function isRecentWorkbook(item: { workspaceKey?: string; path?: unknown; filename?: unknown }): boolean {
  const path = typeof item.path === "string" ? item.path : "";
  const filename = typeof item.filename === "string" ? item.filename : "";
  return isSpreadsheetFile(displayFileName(path) || filename);
}

export function activeSession(): Session | undefined {
  const { activeSessionId, sessions } = useSessionStore.getState();
  if (!activeSessionId) return undefined;
  return sessions.find((item) => item.id === activeSessionId);
}

/** 事件/恢复来源会话的工作区键；会话不在列表中时返回 `_`（不入任何工作区桶）。 */
export function workspaceKeyForSessionId(sessionId?: string | null): string {
  if (!sessionId) return "_";
  const session = useSessionStore.getState().sessions?.find(
    (item) => item.id === sessionId,
  );
  return workspaceKeyFromSession(session);
}

export function activeFileRef(relative: string, observedVersion?: string): WorkspaceFileRef {
  return fileRefFromSession(relative, activeSession(), observedVersion);
}

export function nextRequestId(): string {
  return `view-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
}
