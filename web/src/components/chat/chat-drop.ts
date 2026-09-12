import { formatFileMention } from "./chat-input-insert";

export const WORKSPACE_FILE_MIME = "application/x-excel-file";

export type WorkspaceDroppedFile = { path: string; filename: string };

export function transferHasType(
  types: ArrayLike<string> | null | undefined,
  type: string,
): boolean {
  if (!types) return false;
  const needle = type.toLowerCase();
  return Array.from(types).some((item) => String(item).toLowerCase() === needle);
}

export function isWorkspaceFileDrag(
  types: ArrayLike<string> | null | undefined,
  draggingFileCount: number,
): boolean {
  return draggingFileCount > 0 || transferHasType(types, WORKSPACE_FILE_MIME);
}

export function hasOsFileDrag(types: ArrayLike<string> | null | undefined): boolean {
  return transferHasType(types, "Files");
}

export function shouldCancelComposerNativeDrop(
  types: ArrayLike<string> | null | undefined,
  draggingFileCount: number,
): boolean {
  return isWorkspaceFileDrag(types, draggingFileCount) || hasOsFileDrag(types);
}

export function parseWorkspaceDroppedFiles(payload: string): WorkspaceDroppedFile[] {
  if (!payload.trim()) return [];
  try {
    const parsed: unknown = JSON.parse(payload);
    const files = Array.isArray(parsed) ? parsed : [parsed];
    return files.filter((file): file is WorkspaceDroppedFile => {
      if (!file || typeof file !== "object") return false;
      const path = (file as { path?: unknown }).path;
      const filename = (file as { filename?: unknown }).filename;
      return typeof path === "string" && path.length > 0 && typeof filename === "string" && filename.length > 0;
    });
  } catch {
    return [];
  }
}

export function workspaceFileMention(file: WorkspaceDroppedFile): string {
  return formatFileMention({ path: file.path || file.filename });
}
