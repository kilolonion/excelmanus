import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/sse", () => ({ consumeSSE: vi.fn(), SSEError: class extends Error {} }));
vi.mock("@/lib/idb-cache", () => ({
  loadCachedMessages: vi.fn().mockResolvedValue(null), saveCachedMessages: vi.fn().mockResolvedValue(undefined),
  deleteCachedMessages: vi.fn(), clearAllCachedMessages: vi.fn(),
}));

import { rollbackAndResend, retryAssistantMessage, sendMessage } from "@/lib/chat-actions";
import * as api from "@/lib/api";
import { enqueueExcelCellEdit, resetExcelCellEditStateForTests, setPersistExcelCellEditsForTests } from "@/lib/excel-cell-edit";
import { consumeSSE } from "@/lib/sse";
import { refreshSessionMessagesFromBackend, useChatStore } from "@/stores/chat-store";
import { useExcelStore } from "@/stores/excel-store";
import { useSessionStore } from "@/stores/session-store";
import { useUIStore } from "@/stores/ui-store";
import { useWorkbookConversationStore } from "@/stores/workbook-conversation-store";
import { useWorkbookChatPreferencesStore as preferences } from "@/stores/workbook-chat-preferences-store";
import { sendWorkbookHandoff } from "@/lib/workbook-handoff";
import { useWorkbookWorkspaceStore } from "@/stores/workbook-workspace-store";

beforeEach(() => {
  vi.useFakeTimers();
  vi.clearAllMocks();
  vi.mocked(consumeSSE).mockResolvedValue(undefined);
  useChatStore.getState().setMessages([]);
  useChatStore.setState({ isStreaming: false, abortController: null, loadedSessionId: "s1", pendingApproval: null, pendingQuestion: null });
  useSessionStore.setState({ activeSessionId: "s1", sessions: [
    { id: "s1", title: "Sales", workspaceId: "w1", messageCount: 0, inFlight: false },
  ] });
  useUIStore.setState({ configReady: true, chatMode: "write" });
  preferences.getState().setAutoReturnToChat(true);
  preferences.getState().setLearnFromNavigation(true);
  useExcelStore.setState({ compareMode: false });
  useWorkbookWorkspaceStore.setState({ workspaces: {} });
  useExcelStore.getState().openFullView("sales.xlsx", "明细");
  useWorkbookConversationStore.setState({ targets: {}, views: {} });
  useWorkbookConversationStore.getState().observe("s1", { workspaceId: "w1", workspaceKey: "id:w1", relative: "sales.xlsx" },
    { status: "ready", sheet: "明细", range: "A1:B4", version: "sha256:aaaa" });
  vi.stubGlobal("fetch", vi.fn(async () => new Response("{}")));
});

afterEach(() => {
  resetExcelCellEditStateForTests();
  vi.restoreAllMocks();
  preferences.getState().setLearnFromNavigation(false);
  vi.clearAllTimers();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("workbook navigation in the actual send path", () => {
  it("persists and replays the original multi-workbook context after navigation", async () => {
    useExcelStore.getState().openFullView("budget.xlsx", "明细");
    useWorkbookConversationStore.getState().observe("s1", { workspaceId: "w1", workspaceKey: "id:w1", relative: "budget.xlsx" },
      { status: "ready", sheet: "明细", version: "v1", range: "C2:D8" });
    expect(await sendMessage("比较这两张表", undefined, "s1")).toBe(true);
    const body = vi.mocked(consumeSSE).mock.calls[0][1] as { sheet_context: object; sheet_contexts: object[] };
    const metadata = { sheet_context: body.sheet_context, sheet_contexts: body.sheet_contexts };
    expect(body.sheet_contexts).toMatchObject([{ path: "sales.xlsx" }, { path: "budget.xlsx" }]);
    const user = useChatStore.getState().messages[0];
    expect(user.role === "user" && user.workbookContext).toEqual(metadata);
    vi.spyOn(api, "fetchSessionMessages").mockResolvedValue([{ role: "user", content: user.role === "user" ? user.content : "",
      message_id: "restored", _workbook_context: metadata }, { role: "assistant", content: "比较结果", message_id: "reply" }]);
    await refreshSessionMessagesFromBackend("s1");
    useExcelStore.getState().openFullView("other.xlsx");
    vi.spyOn(api, "rollbackChat").mockResolvedValue({} as never);
    await retryAssistantMessage("reply", "s1");
    expect(consumeSSE).toHaveBeenCalledTimes(2);
    expect(vi.mocked(consumeSSE).mock.calls[1][1]).toMatchObject(metadata);
  });

  it("checks reference drafts before retrying a merge plan", async () => {
    const file = { workspaceId: "w1", workspaceKey: "id:w1", relative: "sales.xlsx" };
    await sendWorkbookHandoff({ file, sessionId: "s1", operation: "merge-workbooks" }, "按编号关联", {
      sources: [{ workspace_id: "w1", path: "sales.xlsx" }, { workspace_id: "w1", path: "budget.xlsx" }],
    });
    const messages = useChatStore.getState().messages;
    setPersistExcelCellEditsForTests(async () => ({ kind: "conflict", code: "VERSION_CONFLICT" }));
    enqueueExcelCellEdit({ path: "budget.xlsx", file: { ...file, relative: "budget.xlsx" }, sessionId: "s1", sheet: "明细", cell: "A1", value: 42, expectedVersion: "v1" });
    const rollback = vi.spyOn(api, "rollbackChat").mockResolvedValue({} as never);
    await retryAssistantMessage(messages[messages.length - 1].id, "s1");
    expect(rollback).not.toHaveBeenCalled();
    expect(consumeSSE).toHaveBeenCalledOnce();
  });
  it("restores structured operation parameters from persisted message history", async () => {
    const action = { workspace_id: "w1", path: "sales.xlsx", sheet: "明细", range: "A1:B4", operation: "pivot", parameters: { values: "求和" }, instruction: "" };
    vi.spyOn(api, "fetchSessionMessages").mockResolvedValue([{ role: "user", content: "创建汇总方案", message_id: "restored", _workbook_action: action }]);
    await refreshSessionMessagesFromBackend("s1");
    const restored = useChatStore.getState().messages[0];
    expect(restored.role === "user" && restored.workbookAction).toEqual(action);
  });

  it("sends operation parameters directly in plan mode using the captured selection", async () => {
    const file = { workspaceId: "w1", workspaceKey: "id:w1", relative: "sales.xlsx" };
    await sendWorkbookHandoff({ file, sessionId: "s1", operation: "chart", sheet: "明细", range: "C1:D8", version: "sha256:aaaa" },
      "放到新工作表", { requested: { chart_type: "柱状图", series: "销售额" } });
    expect(consumeSSE).toHaveBeenCalledOnce();
    expect(vi.mocked(consumeSSE).mock.calls[0][1]).toMatchObject({ chat_mode: "plan",
      workbook_action: { operation: "chart", path: "sales.xlsx", sheet: "明细", range: "C1:D8", observed_version: "sha256:aaaa",
        parameters: { requested: { chart_type: "柱状图", series: "销售额" } }, instruction: "放到新工作表" } });
    expect(useUIStore.getState().chatMode).toBe("plan");
  });

  it("retains operation parameters and plan mode when retrying the generated plan", async () => {
    const file = { workspaceId: "w1", workspaceKey: "id:w1", relative: "sales.xlsx" };
    await sendWorkbookHandoff({ file, sessionId: "s1", operation: "pivot", sheet: "明细", range: "A1:B4", version: "sha256:aaaa" }, "按地区汇总", { rows: "地区", values: "金额求和" });
    const messages = useChatStore.getState().messages;
    expect(messages[0].role === "user" && messages[0].workbookAction?.parameters).toEqual({ rows: "地区", values: "金额求和" });
    vi.spyOn(api, "rollbackChat").mockResolvedValue({} as never);
    await retryAssistantMessage(messages[messages.length - 1].id, "s1");
    expect(consumeSSE).toHaveBeenCalledTimes(2);
    expect(vi.mocked(consumeSSE).mock.calls[1][1]).toMatchObject({ chat_mode: "plan", workbook_action: { operation: "pivot", range: "A1:B4", parameters: { rows: "地区", values: "金额求和" } } });
  });

  it("keeps a conflicting draft when sending it to the Agent for a new plan", async () => {
    const file = { workspaceId: "w1", workspaceKey: "id:w1", relative: "sales.xlsx" };
    setPersistExcelCellEditsForTests(async () => ({ kind: "conflict", code: "VERSION_CONFLICT" }));
    enqueueExcelCellEdit({ path: file.relative, file, sessionId: "s1", sheet: "明细", cell: "A1", value: 42, expectedVersion: "sha256:aaaa" });
    await expect(sendWorkbookHandoff({ file, sessionId: "s1", operation: "chart" }, "", {})).rejects.toThrow("未保存");
    await sendWorkbookHandoff({ file, sessionId: "s1", operation: "conflict-replan", version: "sha256:bbbb" }, "保留双方修改", { draft: { local: 42 } });
    expect(consumeSSE).toHaveBeenCalledOnce();
    expect(vi.mocked(consumeSSE).mock.calls[0][1]).toMatchObject({ chat_mode: "plan", workbook_action: { operation: "conflict-replan", parameters: { draft: { local: 42 } } } });
  });

  it("switches before the reply arrives and keeps sheet context in the request", async () => {
    let finish!: () => void;
    vi.mocked(consumeSSE).mockImplementation(() => new Promise<void>((resolve) => { finish = resolve; }));
    expect(await sendMessage("汇总这张表", undefined, "s1")).toBe(true);
    expect(useExcelStore.getState().fullViewPath).toBeNull();
    expect(useChatStore.getState().isStreaming).toBe(true);
    expect(vi.mocked(consumeSSE).mock.calls[0][1]).toMatchObject({
      message: expect.stringContaining("汇总这张表"), sheet_context: { workspace_id: "w1", path: "sales.xlsx", sheet: "明细", range: "A1:B4", observed_version: "sha256:aaaa" },
    });
    finish();
    await Promise.resolve();
  });

  it.each(["empty", "busy", "unconfigured"])("does not switch or learn from a rejected %s send", async (reason) => {
    if (reason === "busy") useChatStore.setState({ isStreaming: true });
    if (reason === "unconfigured") useUIStore.setState({ configReady: false });
    await sendMessage(reason === "empty" ? "  " : "汇总", undefined, "s1");
    expect(useExcelStore.getState().fullViewPath).toBe("sales.xlsx");
    expect(consumeSSE).not.toHaveBeenCalled();
    vi.advanceTimersByTime(10_000);
    expect(preferences.getState().recentChoices).toEqual([]);
  });

  it.each(["edit", "retry"])("keeps the existing conversation when a %s cannot save workbook edits", async (action) => {
    const rollback = vi.spyOn(api, "rollbackChat").mockResolvedValue({} as never);
    useChatStore.getState().addUserMessage("u1", "分析 @file:sales.xlsx");
    useChatStore.getState().addAssistantMessage("a1");
    setPersistExcelCellEditsForTests(async () => ({ kind: "conflict", code: "VERSION_CONFLICT" }));
    enqueueExcelCellEdit({ path: "sales.xlsx", file: { relative: "sales.xlsx", workspaceId: "w1", workspaceKey: "id:w1" },
      sessionId: "s1", sheet: "明细", cell: "A1", value: 42, expectedVersion: "sha256:aaaa" });
    if (action === "edit") await rollbackAndResend("u1", "重新分析 @file:sales.xlsx", "s1");
    else await retryAssistantMessage("a1", "s1");
    expect(rollback).not.toHaveBeenCalled();
    expect(consumeSSE).not.toHaveBeenCalled();
    expect(useChatStore.getState().messages.map((message) => message.id)).toEqual(["u1", "a1"]);
    expect(useExcelStore.getState().fullViewPath).toBe("sales.xlsx");
  });

  it.each(["edit", "retry"])("does not truncate another conversation when switching during %s rollback", async (action) => {
    let finish!: () => void;
    vi.spyOn(api, "rollbackChat").mockImplementation(() => new Promise((resolve) => { finish = () => resolve({} as never); }));
    useChatStore.getState().addUserMessage("u1", "分析 @file:sales.xlsx");
    useChatStore.getState().addAssistantMessage("a1");
    const resending = action === "edit" ? rollbackAndResend("u1", "重新分析 @file:sales.xlsx", "s1") : retryAssistantMessage("a1", "s1");
    await vi.waitFor(() => expect(finish).toBeDefined());
    useSessionStore.setState({ activeSessionId: "s2" });
    useChatStore.getState().setMessages([]);
    useChatStore.getState().addUserMessage("other", "另一个对话");
    finish(); await resending;
    expect(useChatStore.getState().messages.map((message) => message.id)).toEqual(["other"]);
    expect(consumeSSE).not.toHaveBeenCalled();
  });
});
