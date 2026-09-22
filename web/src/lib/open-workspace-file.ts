import { downloadFile } from "@/lib/api";
import {
  classifyWorkspaceFile,
  fileNameOf,
  type WorkspaceFileKind,
} from "@/lib/file-kind";
import { displayFileName } from "@/lib/file-identity";
import { useExcelStore } from "@/stores/excel-store";
import { useFilePreviewStore } from "@/stores/file-preview-store";
import { useSessionStore } from "@/stores/session-store";
import { useWordStore } from "@/stores/word-store";
import type { WorkbookViewLayout } from "@/lib/workspace-surface";
import { useWorkbookFocusStore } from "@/stores/workbook-focus-store";
import { fileRefFromSession, normalizeRelativePath } from "@/lib/workspace-file-ref";
import { useWorkbookWorkspace } from "@/hooks/use-workbook-workspace";

export type OpenWorkspaceFileIntent = "preview" | "full";

export interface OpenWorkspaceFileOptions {
  intent?: OpenWorkspaceFileIntent;
  sheet?: string;
  sessionId?: string | null;
  workspaceId?: string | null;
  workbookLayout?: WorkbookViewLayout;
  range?: string;
  version?: string;
}

function currentFileScope(opts?: OpenWorkspaceFileOptions): {
  sessionId?: string;
  workspaceId?: string;
} {
  const state = useSessionStore.getState();
  const sessionId = opts?.sessionId ?? state.activeSessionId ?? undefined;
  const session = state.sessions?.find((item) => item.id === sessionId);
  const workspaceId = opts?.workspaceId ?? session?.workspaceId ?? undefined;
  return {
    ...(sessionId ? { sessionId } : {}),
    ...(workspaceId ? { workspaceId } : {}),
  };
}

export function openWorkspaceFile(path: string, opts?: OpenWorkspaceFileOptions): WorkspaceFileKind {
  const filename = displayFileName(path) || fileNameOf(path);
  const kind = classifyWorkspaceFile(filename);
  const intent = opts?.intent ?? "preview";
  const scope = currentFileScope(opts);
  const excel = useExcelStore.getState();
  const word = useWordStore.getState();
  const preview = useFilePreviewStore.getState();

  if (kind === "spreadsheet") {
    preview.closeText();
    preview.closeImage();
    word.closePanel();
    word.closeFullView();
    excel.addRecentFile({ path, filename });
    if (intent === "full" || excel.fullViewPath) {
      excel.openFullView(path, opts?.sheet, opts?.workbookLayout ?? excel.fullViewLayout);
    } else {
      excel.openPanel(path, opts?.sheet);
    }
    if (opts?.range) {
      const session = useSessionStore.getState().sessions?.find((item) => item.id === scope.sessionId);
      useWorkbookFocusStore.getState().focus(fileRefFromSession(path, session), opts.sheet, opts.range, opts.version);
    }
    return kind;
  }

  if (kind === "word") {
    preview.closeText();
    preview.closeImage();
    if (intent === "full") {
      excel.closeCompare();
      excel.closeFullView();
      excel.closePanel();
      word.closePanel();
      word.openFullView(path);
    } else {
      excel.closePanel();
      word.openPanel(path);
    }
    return kind;
  }

  if (kind === "image") {
    preview.closeText();
    preview.openImage(path, filename, scope);
    return kind;
  }

  if (kind === "text") {
    preview.closeImage();
    preview.openText(path, filename, scope);
    return kind;
  }

  downloadFile(path, filename, scope.sessionId, scope.workspaceId).catch(() => {});
  return kind;
}

export function useOpenWorkspacePathSet(): Set<string> {
  const { workspace } = useWorkbookWorkspace();
  const fullViewPath = useExcelStore((s) => s.fullViewPath);
  const excelPath = useExcelStore((s) => (s.panelOpen ? s.activeFilePath : null));
  const wordPath = useWordStore((s) => (s.panelOpen ? s.activeDocPath : null));
  const textPath = useFilePreviewStore((s) => (s.textOpen ? s.textTarget?.path ?? null : null));
  const imagePath = useFilePreviewStore((s) => (s.imageOpen ? s.imageTarget?.path ?? null : null));
  const paths = [excelPath, wordPath, textPath, imagePath, ...(fullViewPath ? workspace.files.map((file) => file.path) : [])];
  return new Set(paths.filter((value): value is string => Boolean(value))
    .flatMap((path) => [path, normalizeRelativePath(path), `./${normalizeRelativePath(path)}`]));
}

export function useWorkspaceFileActive(path: string, _filename?: string): boolean {
  return useOpenWorkspacePathSet().has(path);
}
