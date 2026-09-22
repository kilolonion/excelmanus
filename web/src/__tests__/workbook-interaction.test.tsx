// @vitest-environment jsdom
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { WorkbookInteractionBar } from "@/components/excel/WorkbookInteractionBar";
import { InlineQuestionBanner } from "@/components/modals/QuestionPanel";
import { WorkbookPresentationCard } from "@/components/chat/WorkbookPresentationCard";
import { AskUserCard } from "@/components/chat/AskUserCard";
import { openWorkbookQuestion, parseWorkbookTarget, selectionFromDraft, showWorkbookPresentation, type WorkbookTarget } from "@/lib/workbook-interaction";
import { useWorkbookInteractionStore } from "@/stores/workbook-interaction-store";
import { useWorkbookFocusStore } from "@/stores/workbook-focus-store";
import { useExcelStore } from "@/stores/excel-store";
import { useChatStore } from "@/stores/chat-store";
import { useSessionStore } from "@/stores/session-store";
import { answerQuestion } from "@/lib/api";
import { resumeAfterInteraction } from "@/lib/chat-actions";
import type { Question, Session } from "@/lib/types";
import { dispatchSSEEvent, type SSEHandlerContext } from "@/lib/sse-event-handler";

vi.mock("@/lib/api", async (importOriginal) => ({ ...await importOriginal<typeof import("@/lib/api")>(), answerQuestion: vi.fn(), abortChat: vi.fn(), downloadFile: vi.fn() }));
vi.mock("@/lib/chat-actions", () => ({ resumeAfterInteraction: vi.fn() }));
vi.mock("@/lib/excel-cell-edit", () => ({ flushWorkbookEdits: vi.fn(), hasPendingWorkbookEdits: () => false, isWorkbookEditPaused: () => false }));

const session: Session = { id: "s1", title: "Sales", workspaceId: "w1", messageCount: 0, inFlight: false };
const target: WorkbookTarget = { file_path: "./sales.xlsx", workspace_id: "w1", sheet: "明细", ranges: ["B2:B9"], content_version: "sha256:old" };
const question: Question = { id: "q1", header: "范围", text: "请选择要处理的金额", options: [], multiSelect: false, selection: target, sessionId: "s1", toolCallId: "call1" };

beforeEach(() => {
  cleanup();
  vi.clearAllMocks();
  useSessionStore.setState({ activeSessionId: "s1", sessions: [session] });
  useChatStore.setState({ pendingQuestion: question });
  useExcelStore.setState({ activeWorkspaceKey: "id:w1", fullViewPath: null, panelOpen: false, activeFilePath: null, selectionMode: false, draftRange: null, pendingSelection: null });
  useWorkbookInteractionStore.setState({ request: null, presentation: null });
  useWorkbookFocusStore.getState().clear();
});

describe("workbook questions", () => {
  it("opens the pending question's side panel with suggestions, without selecting or submitting them", () => {
    render(<InlineQuestionBanner question={question} selected={new Set()} onToggle={() => {}} />);
    expect(useExcelStore.getState()).toMatchObject({ panelOpen: true, activeFilePath: target.file_path, activeSheet: "明细", selectionMode: true, draftRange: null });
    expect(useWorkbookFocusStore.getState().request).toMatchObject({ ranges: ["B2:B9"], persistent: true, select: false, stage: "selection" });
    expect(answerQuestion).not.toHaveBeenCalled();
  });

  it("does not automatically navigate for replayed questions, but allows explicit reopen", () => {
    render(<InlineQuestionBanner question={{ ...question, autoOpen: false }} selected={new Set()} onToggle={() => {}} />);
    expect(useExcelStore.getState().panelOpen).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: /打开表格选择区域/ }));
    expect(useExcelStore.getState().panelOpen).toBe(true);
  });

  it("keeps the selected draft on failure and sends a version-bound answer on retry", async () => {
    openWorkbookQuestion(question.id, target, "s1");
    render(<WorkbookInteractionBar filePath="sales.xlsx" />);
    expect(screen.getByRole("button", { name: "确认此区域" }).hasAttribute("disabled")).toBe(true);
    act(() => useExcelStore.getState().setDraftRange({ path: "sales.xlsx", sheet: "明细", range: "B3:B5,D3:D5", contentVersion: "sha256:current" }));
    vi.mocked(answerQuestion).mockRejectedValueOnce(new Error("版本已变化，请重新选择"));
    fireEvent.click(screen.getByRole("button", { name: "确认此区域" }));
    await screen.findByRole("alert");
    expect(useChatStore.getState().pendingQuestion?.id).toBe("q1");
    expect(useExcelStore.getState().draftRange?.range).toBe("B3:B5,D3:D5");
    vi.mocked(answerQuestion).mockResolvedValueOnce({ status: "answered", resume_required: true });
    fireEvent.click(screen.getByRole("button", { name: "确认此区域" }));
    await waitFor(() => expect(resumeAfterInteraction).toHaveBeenCalled());
    expect(answerQuestion).toHaveBeenLastCalledWith("s1", "q1", "", { ...target, ranges: ["B3:B5", "D3:D5"], content_version: "sha256:current" });
    expect(useChatStore.getState().pendingQuestion).toBeNull();
    expect(useExcelStore.getState().pendingSelection).toBeNull();
    expect(useWorkbookInteractionStore.getState().request).toBeNull();
  });

  it("a late response cannot clear the next question in the batch", async () => {
    openWorkbookQuestion(question.id, target, "s1");
    let resolve!: (value: { status: string }) => void;
    vi.mocked(answerQuestion).mockImplementationOnce(() => new Promise((r) => { resolve = r; }));
    render(<WorkbookInteractionBar filePath="sales.xlsx" />);
    act(() => useExcelStore.getState().setDraftRange({ path: "sales.xlsx", sheet: "明细", range: "B2", contentVersion: "sha256:old" }));
    fireEvent.click(screen.getByRole("button", { name: "确认此区域" }));
    await waitFor(() => expect(answerQuestion).toHaveBeenCalled());
    act(() => {
      useChatStore.getState().setPendingQuestion({ ...question, id: "q2" });
      openWorkbookQuestion("q2", target, "s1");
    });
    await act(async () => { resolve({ status: "answered" }); });
    expect(useChatStore.getState().pendingQuestion?.id).toBe("q2");
    expect(useWorkbookInteractionStore.getState().request?.questionId).toBe("q2");
  });

  it("leaving selection mode preserves the question and allows reopening", () => {
    openWorkbookQuestion(question.id, target, "s1");
    render(<><InlineQuestionBanner question={question} selected={new Set()} onToggle={() => {}} /><WorkbookInteractionBar filePath="sales.xlsx" /></>);
    fireEvent.click(screen.getByRole("button", { name: "稍后选择" }));
    expect(useChatStore.getState().pendingQuestion?.id).toBe("q1");
    expect(answerQuestion).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: /打开表格选择区域/ }));
    expect(useWorkbookInteractionStore.getState().request?.questionId).toBe("q1");
  });

  it("rejects cross-workspace navigation and drafts with missing version or wrong sheet", () => {
    expect(openWorkbookQuestion("q1", { ...target, workspace_id: "w2" }, "s1")).toBe(false);
    expect(useExcelStore.getState().panelOpen).toBe(false);
    expect(selectionFromDraft(target, { path: "other.xlsx", sheet: "明细", range: "A1", contentVersion: "v1" })).toBeUndefined();
    expect(selectionFromDraft(target, { path: "sales.xlsx", sheet: "Other", range: "A1", contentVersion: "v1" })).toBeUndefined();
    expect(selectionFromDraft(target, { path: "sales.xlsx", sheet: "明细", range: "A1" })).toBeUndefined();
    expect(parseWorkbookTarget({ ...target, ranges: ["XFE1"] })).toBeUndefined();
  });

  it("presents planned changes with a persistent overlay and does not replace a pending selection", () => {
    const presentation = { kind: "workbook_presentation" as const, target, stage: "planned" as const, summary: "统一金额格式" };
    render(<WorkbookPresentationCard result={JSON.stringify(presentation)} />);
    fireEvent.click(screen.getByRole("button"));
    expect(useWorkbookFocusStore.getState().request).toMatchObject({ stage: "planned", persistent: true, select: false });
    openWorkbookQuestion("q1", target, "s1");
    expect(showWorkbookPresentation({ ...presentation, stage: "changed" }, "s1")).toBe(false);
    expect(useWorkbookFocusStore.getState().request?.stage).toBe("selection");
  });

  it("shows confirmed answers as clickable range summaries", () => {
    render(<AskUserCard args={{ questions: [{ text: "范围", selection: target }] }} status="success" result={JSON.stringify({ raw_input: "已确认 明细!B2:B9", selection: target })} />);
    fireEvent.click(screen.getByRole("button", { name: "已确认 明细!B2:B9" }));
    expect(useWorkbookFocusStore.getState().request).toMatchObject({ ranges: ["B2:B9"], version: "sha256:old" });
    expect(screen.queryByText(/"workspace_id"/)).toBeNull();
  });

  it("opens a tool result immediately after the preceding question ends, but never on replay", () => {
    const ctx: SSEHandlerContext = { assistantMsgId: "m1", effectiveSessionId: "s1", isFirstSend: false, thinkingInProgress: false, hadStreamError: false,
      batcher: { pushText() {}, pushThinking() {}, flush() {}, dispose() {}, hasPendingContent: () => false } };
    const presentation = { kind: "workbook_presentation", target, stage: "changed", summary: "已处理金额" };
    openWorkbookQuestion("q1", target, "s1");
    // Consecutive events can arrive before React runs its cleanup effects.
    useChatStore.getState().setPendingQuestion(null);
    dispatchSSEEvent({ event: "tool_call_end", data: { tool_name: "show_workbook", tool_call_id: "show1", success: true, result: JSON.stringify(presentation) } }, ctx);
    expect(useWorkbookInteractionStore.getState().presentation?.stage).toBe("changed");
    useExcelStore.getState().closePanel();
    dispatchSSEEvent({ event: "tool_call_end", data: { tool_name: "show_workbook", tool_call_id: "show1", success: true, result: JSON.stringify(presentation) } }, { ...ctx, fromReplay: true });
    expect(useExcelStore.getState().panelOpen).toBe(false);
  });
});
