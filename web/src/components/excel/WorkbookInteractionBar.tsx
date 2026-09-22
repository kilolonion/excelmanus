"use client";

import { useEffect, useState } from "react";
import { Check, LocateFixed, X } from "lucide-react";
import { useChatStore } from "@/stores/chat-store";
import { useSessionStore } from "@/stores/session-store";
import { useExcelStore } from "@/stores/excel-store";
import { useWorkbookInteractionStore } from "@/stores/workbook-interaction-store";
import { useWorkbookFocusStore } from "@/stores/workbook-focus-store";
import { openWorkbookQuestion, openWorkbookTarget, selectionFromDraft } from "@/lib/workbook-interaction";
import { fileRefFromSession, normalizeRelativePath } from "@/lib/workspace-file-ref";
import { flushWorkbookEdits, hasPendingWorkbookEdits, isWorkbookEditPaused } from "@/lib/excel-cell-edit";
import { answerQuestion } from "@/lib/api";
import { resumeAfterInteraction } from "@/lib/chat-actions";

export function useWorkbookQuestionRequest() {
  const request = useWorkbookInteractionStore((s) => s.request);
  const questionId = useChatStore((s) => s.pendingQuestion?.id);
  const sessionId = useSessionStore((s) => s.activeSessionId);
  const valid = request?.questionId === questionId && request?.sessionId === sessionId;
  useEffect(() => {
    if (request && !valid && useWorkbookInteractionStore.getState().request === request) {
      useWorkbookInteractionStore.getState().finish(request.questionId);
      useWorkbookFocusStore.getState().clear();
      useExcelStore.getState().exitSelectionMode();
    }
  }, [request, valid]);
  return valid ? request : null;
}

export function WorkbookInteractionBar({ filePath }: { filePath: string | null }) {
  const request = useWorkbookQuestionRequest();
  const presentation = useWorkbookInteractionStore((s) => s.presentation);
  const sessionId = useSessionStore((s) => s.activeSessionId);
  const draft = useExcelStore((s) => s.draftRange);
  const selectionMode = useExcelStore((s) => s.selectionMode);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => { setError(""); setSubmitting(false); }, [request?.questionId]);
  const target = request?.target ?? (presentation?.sessionId === sessionId ? presentation.target : null);
  if (!target || !filePath || normalizeRelativePath(target.file_path) !== normalizeRelativePath(filePath)) return null;
  const selected = request ? selectionFromDraft(target, draft) : undefined;
  const submit = async () => {
    if (!request || !selected || submitting) return;
    const { questionId, sessionId: capturedSessionId } = request;
    if (useSessionStore.getState().activeSessionId !== capturedSessionId
      || useChatStore.getState().pendingQuestion?.id !== questionId) return;
    setSubmitting(true);
    setError("");
    try {
      const session = useSessionStore.getState().sessions.find((s) => s.id === capturedSessionId);
      const file = fileRefFromSession(selected.file_path, session);
      await flushWorkbookEdits(file);
      if (hasPendingWorkbookEdits(file) || isWorkbookEditPaused(file)) throw new Error("请先处理表格保存问题，再确认选区");
      if (useSessionStore.getState().activeSessionId !== capturedSessionId
        || useChatStore.getState().pendingQuestion?.id !== questionId) return;
      const response = await answerQuestion(capturedSessionId, questionId, "", selected);
      if (useSessionStore.getState().activeSessionId === capturedSessionId
        && useChatStore.getState().pendingQuestion?.id === questionId) {
        useChatStore.getState().setPendingQuestion(null);
      }
      if (useWorkbookInteractionStore.getState().request?.questionId === questionId) {
        useWorkbookInteractionStore.getState().finish(questionId);
        useExcelStore.getState().exitSelectionMode();
        useWorkbookFocusStore.getState().clear();
      }
      await resumeAfterInteraction(capturedSessionId, response);
    } catch (err) {
      if (useWorkbookInteractionStore.getState().request?.questionId === questionId) {
        setError(err instanceof Error ? err.message : "确认失败，请重试");
      }
    } finally {
      const current = useWorkbookInteractionStore.getState().request;
      if (!current || current.questionId === questionId) setSubmitting(false);
    }
  };
  return (
    <div className="shrink-0 border-t border-border bg-background px-3 py-2 space-y-2" aria-label="表格区域交互">
      <div className="flex items-center gap-2 flex-wrap">
        <LocateFixed className="h-4 w-4 shrink-0" style={{ color: request ? "#3b82f6" : presentation?.stage === "planned" ? "#d97706" : "var(--em-primary)" }} />
        <div className="flex-1 min-w-0 text-xs" aria-live="polite">
          <p className="font-medium">{request ? "请选区并确认" : presentation?.stage === "planned" ? "准备修改的区域" : presentation?.stage === "changed" ? "已修改的区域" : "查看区域"}</p>
          <p className="text-muted-foreground break-words mt-1">{target.sheet} · {request ? selected?.ranges.join("、") || "拖选单元格后确认；也可回到对话补充说明" : target.ranges.join("、")}</p>
          {!request && <p className="text-muted-foreground mt-1">{presentation?.summary}</p>}
        </div>
        {request ? <>
          {selectionMode ? <button type="button" disabled={!selected || submitting} onClick={submit}
            className="inline-flex items-center gap-1 rounded-md px-3 py-2 text-xs text-white disabled:opacity-40" style={{ background: "var(--em-primary)" }}>
            <Check className="h-3.5 w-3.5" />{submitting ? "正在确认…" : "确认此区域"}
          </button> : <button type="button" onClick={() => openWorkbookQuestion(request.questionId, target, request.sessionId)} className="text-xs underline">继续选区</button>}
          <button type="button" disabled={submitting} onClick={() => {
            useWorkbookInteractionStore.getState().finish(request.questionId);
            useExcelStore.getState().exitSelectionMode();
            useWorkbookFocusStore.getState().clear();
          }} className="text-xs text-muted-foreground px-2 py-2">稍后选择</button>
        </> : <>
          <button type="button" className="text-xs underline" onClick={() => sessionId && openWorkbookTarget(target, sessionId, presentation!.stage)}>重新定位</button>
          <button type="button" aria-label="关闭区域高亮" className="p-2" onClick={() => {
            useWorkbookInteractionStore.getState().dismissPresentation();
            useWorkbookFocusStore.getState().clear();
          }}><X className="h-4 w-4" /></button>
        </>}
      </div>
      {error && <p role="alert" className="text-xs text-destructive break-words">{error}</p>}
    </div>
  );
}
