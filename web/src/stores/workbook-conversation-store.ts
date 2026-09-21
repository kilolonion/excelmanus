import { create } from "zustand";
import { persist } from "zustand/middleware";
import { isSpreadsheetFile } from "@/lib/file-kind";
import { fileRefKey, type WorkspaceFileRef } from "@/lib/workspace-file-ref";
import type { WorkbookViewLayout } from "@/lib/workspace-surface";

export interface WorkbookConversationTarget {
  file: WorkspaceFileRef;
  sheet?: string;
  showSheet: boolean;
  layout?: WorkbookViewLayout;
}

export interface WorkbookViewState {
  status: "loading" | "ready" | "error";
  sheet?: string;
  range?: string;
  version?: string;
  error?: string;
}

interface WorkbookConversationState {
  pickerOpen: boolean;
  pickerLayout: WorkbookViewLayout;
  pickerMode: "open" | "switch";
  pickerShowSheet: boolean;
  targets: Record<string, WorkbookConversationTarget>;
  views: Record<string, WorkbookViewState>;
  openPicker: (layout?: WorkbookViewLayout) => void;
  openSwitchPicker: (sessionId: string) => void;
  closePicker: () => void;
  bind: (sessionId: string, file: WorkspaceFileRef, sheet?: string, layout?: WorkbookViewLayout) => void;
  setShowSheet: (sessionId: string, show: boolean) => void;
  observe: (sessionId: string, file: WorkspaceFileRef, view: WorkbookViewState) => void;
  detach: (sessionId: string) => void;
}

export function workbookViewKey(sessionId: string, file: WorkspaceFileRef): string {
  return `${sessionId}|${fileRefKey(file)}`;
}

function isWorkbookTarget(value: unknown): value is WorkbookConversationTarget {
  if (!value || typeof value !== "object") return false;
  const target = value as Partial<WorkbookConversationTarget>;
  const file = target.file;
  return Boolean(
    file
    && typeof file === "object"
    && typeof file.relative === "string"
    && isSpreadsheetFile(file.relative),
  );
}

/** A discussion target survives hiding the grid; readiness never survives a reload. */
export const useWorkbookConversationStore = create<WorkbookConversationState>()(persist((set) => ({
  pickerOpen: false,
  pickerLayout: "embedded",
  pickerMode: "open",
  pickerShowSheet: true,
  targets: {},
  views: {},
  openPicker: (layout = "embedded") => set({ pickerOpen: true, pickerLayout: layout, pickerMode: "open", pickerShowSheet: true }),
  openSwitchPicker: (sessionId) => set((state) => {
    const target = state.targets[sessionId];
    if (!target) return state;
    return { pickerOpen: true, pickerMode: "switch", pickerLayout: target.layout ?? "embedded", pickerShowSheet: target.showSheet };
  }),
  closePicker: () => set({ pickerOpen: false }),
  bind: (sessionId, file, sheet, layout = "embedded") => set((state) => {
    if (!isSpreadsheetFile(file.relative)) return state;
    return {
      targets: { ...state.targets, [sessionId]: { file, sheet, showSheet: true, layout } },
      views: { ...state.views, [workbookViewKey(sessionId, file)]:
        state.views[workbookViewKey(sessionId, file)] ?? { status: "loading" } },
    };
  }),
  setShowSheet: (sessionId, showSheet) => set((state) => {
    const target = state.targets[sessionId];
    if (!target || target.showSheet === showSheet) return state;
    return { targets: { ...state.targets, [sessionId]: { ...target, showSheet } } };
  }),
  observe: (sessionId, file, view) => set((state) => {
    const target = state.targets[sessionId];
    if (!target || fileRefKey(target.file) !== fileRefKey(file)) return state;
    const key = workbookViewKey(sessionId, file);
    const previous = state.views[key];
    const nextView = view.status === "ready" && previous?.status === "ready"
      && view.sheet === previous.sheet && view.version === previous.version && !("range" in view)
      ? { ...view, range: previous.range } : view;
    if (previous?.status === nextView.status && previous?.sheet === nextView.sheet
      && previous?.range === nextView.range
      && previous?.version === nextView.version && previous?.error === nextView.error) return state;
    return {
      views: { ...state.views, [key]: nextView },
      ...(view.status === "ready" ? {
        targets: { ...state.targets, [sessionId]: { ...target,
          sheet: view.sheet ?? target.sheet,
          file: { ...target.file, observedVersion: view.version },
        } },
      } : {}),
    };
  }),
  detach: (sessionId) => set((state) => {
    const targets = { ...state.targets };
    delete targets[sessionId];
    return { targets };
  }),
}), {
  name: "excelmanus-workbook-conversations",
  partialize: (state) => ({ targets: Object.fromEntries(Object.entries(state.targets).slice(-100)) }),
  merge: (persisted, current) => {
    const raw = persisted as { targets?: unknown } | undefined;
    const hasPersistedTargets = Boolean(raw?.targets && typeof raw.targets === "object");
    const rawTargets = hasPersistedTargets ? raw?.targets as Record<string, unknown> : null;
    const targets = rawTargets
      ? Object.fromEntries(
        Object.entries(rawTargets).filter(([, target]) => isWorkbookTarget(target)),
      ) as Record<string, WorkbookConversationTarget>
      : current.targets;
    return { ...current, targets };
  },
}));
