import { prefetchWorkbookView } from "@/lib/api";
import { warmUniverModules } from "@/lib/univer-modules";
import {
  activeFileRef,
  activeSession,
  type WorkspaceFileRef,
} from "@/lib/workspace-file-ref";
import { useSessionStore } from "@/stores/session-store";

export function prefetchExcelView(
  pathOrRef?: string | WorkspaceFileRef | null,
) {
  warmUniverModules();
  if (!pathOrRef) return;
  const session = activeSession();
  const sessionId = useSessionStore.getState().activeSessionId ?? undefined;
  const ref = typeof pathOrRef === "string" ? activeFileRef(pathOrRef) : pathOrRef;
  prefetchWorkbookView({
    path: ref.relative,
    workspaceKey: ref.workspaceKey,
    sessionId,
    workspaceId: ref.workspaceId ?? session?.workspaceId,
  });
}
