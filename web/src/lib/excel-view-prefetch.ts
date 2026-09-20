import { prefetchWorkbookView } from "@/lib/api";
import { warmUniverModules } from "@/lib/univer-modules";
import {
  activeFileRef,
  activeSession,
  type WorkspaceFileRef,
} from "@/lib/workspace-file-ref";
import { useSessionStore } from "@/stores/session-store";
import { useExcelStore } from "@/stores/excel-store";

let prefetchController: AbortController | null = null;
let prefetchKey = "";

export function prefetchExcelView(
  pathOrRef?: string | WorkspaceFileRef | null,
) {
  void warmUniverModules()?.catch(() => {});
  if (!pathOrRef) return;
  const session = activeSession();
  const sessionId = useSessionStore.getState().activeSessionId ?? undefined;
  const ref = typeof pathOrRef === "string" ? activeFileRef(pathOrRef) : pathOrRef;
  const key = `${ref.workspaceKey}|${ref.relative}`;
  if (key !== prefetchKey) {
    prefetchController?.abort();
    prefetchController = new AbortController();
    prefetchKey = key;
  }
  const state = useExcelStore.getState();
  prefetchWorkbookView({
    path: ref.relative,
    workspaceKey: ref.workspaceKey,
    sessionId,
    workspaceId: ref.workspaceId ?? session?.workspaceId,
    sheet: state.activeFilePath?.replace(/^\.\//, "") === ref.relative ? state.activeSheet || undefined : undefined,
    signal: prefetchController?.signal,
  });
}
