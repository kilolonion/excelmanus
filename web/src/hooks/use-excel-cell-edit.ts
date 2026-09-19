"use client";

import { useCallback, useEffect, useState } from "react";
import { invalidateWorkbookCaches } from "@/lib/api";
import {
  enqueueExcelCellEdit,
  resumeExcelCellEdits,
} from "@/lib/excel-cell-edit";
import { useExcelStore } from "@/stores/excel-store";
import { activeFileRef } from "@/lib/workspace-file-ref";
import { useSessionStore } from "@/stores/session-store";

export function useExcelCellEdit(filePath: string | null) {
  const [conflict, setConflict] = useState(false);
  const [writeError, setWriteError] = useState<string | null>(null);

  useEffect(() => {
    setConflict(false);
    setWriteError(null);
    if (filePath) resumeExcelCellEdits(filePath);
  }, [filePath]);

  const handleCellEdit = useCallback(
    (cell: string, value: unknown, sheet?: string) => {
      if (!filePath || !cell) return;
      const file = activeFileRef(filePath);
      enqueueExcelCellEdit({
        path: filePath,
        sheet,
        cell,
        value,
        file,
        sessionId: useSessionStore.getState().activeSessionId,
        viewGeneration: useExcelStore.getState().viewGeneration,
        expectedVersion: useExcelStore.getState().getContentVersion(filePath, file.workspaceKey),
        onConflict: () => {
          setWriteError(null);
          setConflict(true);
        },
        onError: (message) => {
          setConflict(false);
          setWriteError(message || "保存失败");
        },
      });
    },
    [filePath],
  );

  const reloadAfterConflict = useCallback(() => {
    if (!filePath) return;
    resumeExcelCellEdits(filePath);
    const file = activeFileRef(filePath);
    useExcelStore.getState().setContentVersion(filePath, null, file.workspaceKey);
    invalidateWorkbookCaches({ workspaceKey: file.workspaceKey, relative: filePath });
    useExcelStore.setState((s) => ({ refreshCounter: s.refreshCounter + 1 }));
    setConflict(false);
    setWriteError(null);
  }, [filePath]);

  return {
    handleCellEdit,
    conflict,
    writeError,
    reloadAfterConflict,
    dismissWriteError: () => setWriteError(null),
  };
}
