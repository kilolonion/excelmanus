import { create } from "zustand";
import type { WorkspaceFileRef } from "@/lib/workspace-file-ref";

export interface WorkbookWorkflowScope {
  file: WorkspaceFileRef;
  sessionId: string;
}

export interface WorkbookHandoff extends WorkbookWorkflowScope {
  operation: string;
  sheet?: string;
  range?: string;
  version?: string;
  parameters?: Record<string, unknown>;
}

// Requests own their original workspace and selection, independent of later panel navigation.
export const useWorkbookWorkflowStore = create<{
  sequence: number;
  handoff: (WorkbookHandoff & { id: number }) | null;
  conflict: (WorkbookWorkflowScope & { id: number }) | null;
  openHandoff: (request: WorkbookHandoff) => void;
  openConflict: (request: WorkbookWorkflowScope) => void;
  closeHandoff: () => void;
  closeConflict: () => void;
}>((set) => ({
  sequence: 0, handoff: null, conflict: null,
  openHandoff: (request) => set((state) => ({ sequence: state.sequence + 1, handoff: { ...request, id: state.sequence + 1 } })),
  openConflict: (request) => set((state) => ({ sequence: state.sequence + 1, conflict: { ...request, id: state.sequence + 1 } })),
  closeHandoff: () => set({ handoff: null }),
  closeConflict: () => set({ conflict: null }),
}));
