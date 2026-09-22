import { create } from "zustand";
import type { WorkspaceFileRef } from "@/lib/workspace-file-ref";
import { workbookFocusRanges } from "@/lib/workbook-focus";

export interface WorkbookFocusRequest {
  id: number;
  file: WorkspaceFileRef;
  sheet?: string;
  ranges: string[];
  version?: string;
  stage?: "selection" | "inspect" | "planned" | "changed";
  persistent?: boolean;
  select?: boolean;
}

export const useWorkbookFocusStore = create<{
  request: WorkbookFocusRequest | null;
  sequence: number;
  completedId: number;
  complete: (id: number) => void;
  focus: (file: WorkspaceFileRef, sheet: string | undefined, range: string, version?: string,
    options?: Pick<WorkbookFocusRequest, "stage" | "persistent" | "select">) => void;
  clear: () => void;
}>((set) => ({
  request: null,
  sequence: 0,
  completedId: 0,
  complete: (id) => set({ completedId: id }),
  clear: () => set({ request: null }),
  focus: (file, sheet, range, version, options) => set((state) => {
    const ranges = workbookFocusRanges(range);
    const sequence = state.sequence + 1;
    return { sequence, request: ranges.length ? { id: sequence, file, sheet, ranges, version, ...options } : null };
  }),
}));
