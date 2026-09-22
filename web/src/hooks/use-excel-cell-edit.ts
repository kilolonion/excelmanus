"use client";

import { useCallback, useState, useSyncExternalStore } from "react";
import {
  enqueueExcelCellEdit,
  discardWorkbookEdits,
  isWorkbookEditPaused,
  subscribeWorkbookEdits,
} from "@/lib/excel-cell-edit";
import { useExcelStore } from "@/stores/excel-store";
import { fileRefFromSession, versionStoreKey, workspaceKeyFromSession } from "@/lib/workspace-file-ref";
import { useSessionStore } from "@/stores/session-store";

export function useExcelCellEdit(filePath: string | null) {
  const session = useSessionStore((s) => s.sessions.find((item) => item.id === s.activeSessionId));
  const key = filePath ? versionStoreKey(filePath, workspaceKeyFromSession(session)) : null;
  const paused = useSyncExternalStore(subscribeWorkbookEdits,
    () => Boolean(filePath && isWorkbookEditPaused(fileRefFromSession(filePath, session))), () => false);
  const [writeState, setWriteState] = useState(() => ({
    key,
    conflict: Boolean(filePath && isWorkbookEditPaused(fileRefFromSession(filePath, session))),
    error: null as string | null,
  }));
  if (writeState.key !== key) {
    setWriteState({
      key,
      conflict: Boolean(filePath && isWorkbookEditPaused(fileRefFromSession(filePath, session))),
      error: null,
    });
  }

  const handleCellEdit = useCallback(
    (cell: string, value: unknown, sheet?: string) => {
      if (!filePath || !cell) return;
      const file = fileRefFromSession(filePath, session);
      enqueueExcelCellEdit({
        path: filePath,
        sheet,
        cell,
        value,
        file,
        sessionId: session?.id ?? null,
        viewGeneration: useExcelStore.getState().viewGeneration,
        expectedVersion: useExcelStore.getState().getContentVersion(filePath, file.workspaceKey),
        onConflict: () => {
          setWriteState((current) => current.key === key
            ? { key, conflict: true, error: null } : current);
        },
        onError: (message) => {
          setWriteState((current) => current.key === key
            ? { key, conflict: false, error: message || "保存失败" } : current);
        },
      });
    },
    [filePath, session, key],
  );

  const reloadAfterConflict = useCallback(() => {
    if (!filePath) return;
    const file = fileRefFromSession(filePath, session);
    discardWorkbookEdits(file);
    useExcelStore.getState().notifyWorkbookChanged(filePath, file.workspaceKey, undefined, "refresh");
    setWriteState({ key, conflict: false, error: null });
  }, [filePath, session, key]);

  return {
    handleCellEdit,
    conflict: paused && writeState.conflict,
    writeError: writeState.error,
    reloadAfterConflict,
    dismissWriteError: () => setWriteState((current) => ({ ...current, error: null })),
  };
}
