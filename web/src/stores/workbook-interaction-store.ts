import { create } from "zustand";
import type { WorkbookTarget, WorkbookPresentation } from "@/lib/workbook-interaction";

export interface WorkbookQuestionRequest {
  questionId: string;
  sessionId: string;
  target: WorkbookTarget;
}

export const useWorkbookInteractionStore = create<{
  request: WorkbookQuestionRequest | null;
  presentation: (WorkbookPresentation & { sessionId: string }) | null;
  begin: (request: WorkbookQuestionRequest) => void;
  finish: (questionId: string) => void;
  present: (presentation: WorkbookPresentation, sessionId: string) => void;
  dismissPresentation: () => void;
}>((set) => ({
  request: null,
  presentation: null,
  begin: (request) => set({ request, presentation: null }),
  finish: (questionId) => set((s) => s.request?.questionId === questionId ? { request: null } : {}),
  present: (presentation, sessionId) => set({ presentation: { ...presentation, sessionId } }),
  dismissPresentation: () => set({ presentation: null }),
}));
