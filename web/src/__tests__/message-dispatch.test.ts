import { afterEach, beforeEach, expect, it, vi } from "vitest";

vi.mock("@/lib/sse", () => ({ consumeSSE: vi.fn(), SSEError: class extends Error {} }));
vi.mock("@/lib/idb-cache", () => ({ loadCachedMessages: vi.fn().mockResolvedValue(null), saveCachedMessages: vi.fn().mockResolvedValue(undefined), deleteCachedMessages: vi.fn(), clearAllCachedMessages: vi.fn() }));
vi.mock("@/lib/workbook-conversation", () => ({
  prepareWorkbookRequest: vi.fn(async (text, sessionId) => ({ text, sessionId, workspaceKey: "id:w1" })),
  assertWorkbookRequestCurrent: vi.fn(),
}));

import * as api from "@/lib/api";
import { sendMessage, subscribeToSession } from "@/lib/chat-actions";
import { consumeSSE } from "@/lib/sse";
import { dispatchSSEEvent, preDispatch, type SSEHandlerContext } from "@/lib/sse-event-handler";
import { useChatStore } from "@/stores/chat-store";
import { useSessionStore } from "@/stores/session-store";
import { useUIStore } from "@/stores/ui-store";
import { useDispatchStore } from "@/stores/dispatch-store";
import type { DispatchReceipt } from "@/lib/types";
import { prepareWorkbookRequest, assertWorkbookRequestCurrent } from "@/lib/workbook-conversation";

const receipt = (changes: Partial<DispatchReceipt> = {}): DispatchReceipt => ({
  dispatch_id: "d2", client_message_id: "c2", mode: "queue", content: "second",
  revision: 1, status: "queued", created_at: 1, ...changes,
});
const ctx = (): SSEHandlerContext => ({ assistantMsgId: "a1", effectiveSessionId: "s1", isFirstSend: false,
  thinkingInProgress: false, hadStreamError: false, turnId: "t1",
  batcher: { pushText: vi.fn(), pushThinking: vi.fn(), flush: vi.fn(), dispose: vi.fn(), hasPendingContent: () => false, setTarget: vi.fn() },
});

beforeEach(() => {
  vi.useFakeTimers();
  vi.clearAllMocks();
  vi.stubGlobal("requestAnimationFrame", (cb: FrameRequestCallback) => setTimeout(() => cb(0), 16));
  vi.stubGlobal("cancelAnimationFrame", clearTimeout);
  vi.stubGlobal("fetch", vi.fn(async () => new Response("{}")));
  useSessionStore.setState({ activeSessionId: "s1", sessions: [{ id: "s1", workspaceId: "w1", title: "fixture", inFlight: true, messageCount: 2 }] });
  useChatStore.getState().setMessages([{ id: "c1", role: "user", content: "first" }, { id: "a1", role: "assistant", blocks: [] }]);
  useChatStore.setState({ loadedSessionId: "s1", isStreaming: true, abortController: new AbortController(), pendingQuestion: null, pendingApproval: null });
  useUIStore.setState({ configReady: true, chatMode: "read", messageDispatchDefault: "queue" });
  useDispatchStore.setState({ sessions: {} });
});
afterEach(() => { vi.clearAllTimers(); vi.useRealTimers(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

it("retains one id after a lost HTTP response and preserves the live stream", async () => {
  const owner = useChatStore.getState().abortController;
  const post = vi.spyOn(api, "dispatchChatMessage").mockRejectedValueOnce(new TypeError("connection lost"))
    .mockImplementationOnce(async (_, body) => receipt({ client_message_id: body.client_message_id }));
  await expect(sendMessage("retry same input", undefined, "s1")).rejects.toThrow("connection lost");
  useChatStore.setState({ isStreaming: false, abortController: null });
  await sendMessage("retry same input", undefined, "s1");
  expect(post.mock.calls[0][1].client_message_id).toBe(post.mock.calls[1][1].client_message_id);
  expect(post.mock.calls[1][1]).toMatchObject({ mode: "queue", chat_mode: "read" });
  expect(consumeSSE).not.toHaveBeenCalled();
  expect(owner?.signal.aborted).toBe(false);
  expect(useChatStore.getState().messages).toHaveLength(2);
});

it("captures workbook context for running messages and blocks failed preflight", async () => {
  const post = vi.spyOn(api, "dispatchChatMessage").mockResolvedValue(receipt());
  const sheetContext = { workspace_id: "w1", path: "book.xlsx", sheet: "Sheet1", range: "A1", observed_version: "v1" };
  vi.mocked(prepareWorkbookRequest).mockResolvedValueOnce({ text: "follow", sessionId: "s1", workspaceKey: "id:w1", sheetContext });
  await sendMessage("follow", undefined, "s1", undefined, undefined, "interrupt");
  expect(post.mock.calls[0][1]).toMatchObject({ mode: "interrupt", sheet_context: sheetContext });
  vi.mocked(assertWorkbookRequestCurrent).mockImplementationOnce(() => { throw new Error("stale view"); });
  await expect(sendMessage("blocked", undefined, "s1")).rejects.toThrow("stale view");
  expect(post).toHaveBeenCalledOnce();
});

it("does not overwrite a completed SSE receipt with the later queued HTTP ack", () => {
  const state = useDispatchStore.getState();
  state.upsert("s1", receipt({ revision: 4, status: "completed" }));
  state.upsert("s1", receipt());
  state.upsert("s2", receipt());
  expect(useDispatchStore.getState().sessions.s1.c2.status).toBe("completed");
  expect(useDispatchStore.getState().sessions.s2.c2.status).toBe("queued");
});

it("materializes a steer once and retargets subsequent output", () => {
  const context = ctx();
  const data = receipt({ mode: "steer", status: "completed", turn_id: "t1", revision: 3 });
  dispatchSSEEvent({ event: "dispatch_state", data: { ...data } }, context);
  dispatchSSEEvent({ event: "dispatch_state", data: { ...data } }, context);
  expect(useChatStore.getState().messages.filter((m) => m.id === "c2")).toHaveLength(1);
  expect(context.assistantMsgId).toBe("dispatch:d2");
  expect(context.batcher.setTarget).toHaveBeenCalledOnce();
});

it("does not label the initial idle send as an in-flight intervention", () => {
  const context = { ...ctx(), showDispatch: false, turnId: undefined };
  dispatchSSEEvent({ event: "turn_start", data: { turn_id: "t1", dispatch: receipt({ client_message_id: "c1", mode: "steer" }) } }, context);
  const initial = useChatStore.getState().messagesById.c1;
  expect(initial?.role === "user" ? initial.dispatchMode : undefined).toBeUndefined();
  expect(context.turnId).toBe("t1");
});

it("materializes queued attachment receipts without leaking transport context", () => {
  const context = ctx();
  const data = receipt({
    mode: "queue",
    status: "queued",
    client_message_id: "queued-with-file",
    content: "[已上传文件: ./uploads/abcd1234_收款收据.xlsx]\n\n@file:uploads/收款收据.xlsx\n当前工作表：\"收款收据\"\n\n帮我把表格拉宽一点",
  });
  dispatchSSEEvent({ event: "turn_start", data: { turn_id: "t2", dispatch: data } }, context);
  const message = useChatStore.getState().messagesById["queued-with-file"];
  expect(message).toMatchObject({ role: "user", content: "帮我把表格拉宽一点" });
  expect(message?.role === "user" ? message.files : undefined).toEqual([
    { filename: "收款收据.xlsx", path: "./uploads/abcd1234_收款收据.xlsx", size: 0 },
  ]);
  expect(context.batcher.setTarget).toHaveBeenCalledWith("dispatch:d2");
});

it("a hidden continuation never creates a visible user message", () => {
  const context = ctx();
  dispatchSSEEvent({ event: "turn_start", data: { turn_id: "t2", dispatch: receipt({ content: "continue", hidden: true }) } }, context);
  expect(useChatStore.getState().messagesById.c2).toBeUndefined();
  expect(context.assistantMsgId).toBe("a1");
});

it("routes each queued turn's text and token totals to its own reply", async () => {
  useChatStore.getState().setMessages([]);
  useChatStore.setState({ isStreaming: false, abortController: null });
  let emit!: Parameters<typeof consumeSSE>[2];
  let finish!: () => void;
  vi.mocked(consumeSSE).mockImplementation((_url, _body, callback) => {
    emit = callback;
    return new Promise<void>((resolve) => { finish = resolve; });
  });
  await sendMessage("first", undefined, "s1");
  const body = vi.mocked(consumeSSE).mock.calls[0][1] as { client_message_id: string };
  const firstId = useChatStore.getState().messages[1].id;
  const d1 = receipt({ dispatch_id: "d1", client_message_id: body.client_message_id });
  emit({ event: "turn_start", data: { turn_id: "t1", dispatch: d1 } });
  emit({ event: "text_delta", data: { content: "first output" } });
  emit({ event: "turn_reply", data: { turn_id: "t1", content: "first output", total_tokens: 10 } });
  emit({ event: "turn_start", data: { turn_id: "t2", dispatch: receipt() } });
  emit({ event: "text_delta", data: { content: "second output" } });
  emit({ event: "turn_reply", data: { turn_id: "t2", content: "second output", total_tokens: 20 } });
  emit({ event: "reply", data: { turn_id: "t2", content: "second output", total_tokens: 20 } });
  await vi.advanceTimersByTimeAsync(50);
  finish();
  await Promise.resolve();
  expect(JSON.stringify(useChatStore.getState().messagesById[firstId])).toContain("first output");
  expect(JSON.stringify(useChatStore.getState().messagesById[firstId])).not.toContain("second output");
  expect(JSON.stringify(useChatStore.getState().messagesById["dispatch:d2"])).toContain("second output");
  expect(useChatStore.getState().messageOrder).toHaveLength(4);
});

it("reconnect snapshot restores pending messages without starting another request", () => {
  const context = ctx();
  const event = { event: "dispatch_snapshot", data: { dispatches: [receipt()], turn_id: "t1" } };
  preDispatch(event, context);
  dispatchSSEEvent(event, context);
  expect(useDispatchStore.getState().sessions.s1.c2.status).toBe("queued");
  expect(useChatStore.getState().messageOrder).toHaveLength(2);
  expect(consumeSSE).not.toHaveBeenCalled();
});

it("reconnect after a user-only tail never appends into the previous assistant", async () => {
  useChatStore.getState().setMessages([
    { id: "old", role: "assistant", blocks: [{ type: "text", content: "previous reply" }] },
    { id: "c2", role: "user", content: "current request" },
  ]);
  useChatStore.setState({ abortController: null, isStreaming: false, activeStreamId: "stream1", latestSeq: 0 });
  vi.mocked(consumeSSE).mockImplementation(async (_, __, emit) => {
    emit({ event: "dispatch_snapshot", data: { turn_id: "t2", active_dispatch: receipt({ status: "applied" }), dispatches: [] } });
    emit({ event: "text_delta", data: { content: "current reply" } });
    emit({ event: "turn_reply", data: { turn_id: "t2", content: "current reply" } });
    emit({ event: "done", data: {} });
  });
  await subscribeToSession("s1");
  expect(JSON.stringify(useChatStore.getState().messagesById.old)).not.toContain("current reply");
  expect(JSON.stringify(useChatStore.getState().messages.at(-1))).toContain("current reply");
});
