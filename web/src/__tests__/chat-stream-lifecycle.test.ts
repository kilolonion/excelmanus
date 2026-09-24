import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/sse", () => ({ consumeSSE: vi.fn(), SSEError: class extends Error {} }));
vi.mock("@/lib/idb-cache", () => ({
  loadCachedMessages: vi.fn().mockResolvedValue(null), saveCachedMessages: vi.fn().mockResolvedValue(undefined),
  deleteCachedMessages: vi.fn(), clearAllCachedMessages: vi.fn(),
}));

import { sendMessage, sendContinuation, stopGeneration, subscribeToSession } from "@/lib/chat-actions";
import { consumeSSE } from "@/lib/sse";
import { useChatStore } from "@/stores/chat-store";
import { useSessionStore } from "@/stores/session-store";
import { useUIStore } from "@/stores/ui-store";

beforeEach(() => {
  vi.useFakeTimers();
  vi.clearAllMocks();
  vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => setTimeout(() => callback(performance.now()), 16));
  vi.stubGlobal("cancelAnimationFrame", clearTimeout);
  vi.stubGlobal("fetch", vi.fn(async () => new Response("{}")));
  useSessionStore.setState({ activeSessionId: "s1", sessions: [{ id: "s1", workspaceId: "w1", title: "Stream", messageCount: 0, inFlight: false }] });
  useChatStore.getState().setMessages([]);
  useChatStore.setState({ isStreaming: false, abortController: null, loadedSessionId: "s1", activeStreamId: "stream1", latestSeq: 0 });
  useUIStore.setState({ configReady: true, chatMode: "write" });
});

afterEach(() => {
  vi.clearAllTimers();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("sendContinuation", () => {
  it("sends prompt_kind and never creates a user bubble", async () => {
    vi.mocked(consumeSSE).mockImplementation((_url, _body, emit) => {
      emit({ event: "reply", data: { content: "已继续", total_tokens: 0 } });
      emit({ event: "done", data: {} });
      return Promise.resolve();
    });
    useChatStore.getState().addAssistantMessage("a1");
    await sendContinuation("continue", "s1", { promptKind: "continue" });
    const body = vi.mocked(consumeSSE).mock.calls[0][1] as Record<string, unknown>;
    expect(body).toMatchObject({ message: "continue", session_id: "s1", prompt_kind: "continue" });
    // 没有产生新的用户消息，回复仍落在原 assistant 气泡上
    const messages = useChatStore.getState().messages;
    expect(messages).toHaveLength(1);
    expect(messages[0].id).toBe("a1");
    expect(messages[0].role).toBe("assistant");
    expect(JSON.stringify(messages[0])).toContain("已继续");
  });

  it("omits prompt_kind when not provided", async () => {
    vi.mocked(consumeSSE).mockResolvedValue(undefined);
    useChatStore.getState().addAssistantMessage("a1");
    await sendContinuation("/resume", "s1");
    const body = vi.mocked(consumeSSE).mock.calls[0][1] as Record<string, unknown>;
    expect(body).not.toHaveProperty("prompt_kind");
  });

  it("creates a fresh assistant bubble when the tail is a user message", async () => {
    vi.mocked(consumeSSE).mockImplementation((_url, _body, emit) => {
      emit({ event: "reply", data: { content: "已继续", total_tokens: 0 } });
      emit({ event: "done", data: {} });
      return Promise.resolve();
    });
    // 尾部是 user 消息（发出后没有回复，进程被杀/流中断）
    useChatStore.getState().setMessages([
      { id: "a-old", role: "assistant", blocks: [{ type: "text", content: "旧回复" }] },
      { id: "u1", role: "user", content: "触发后无回复的消息" },
    ]);
    await sendContinuation("continue", "s1", { promptKind: "continue" });
    const messages = useChatStore.getState().messages;
    expect(messages).toHaveLength(3);
    expect(messages[2].role).toBe("assistant");
    expect(messages[2].id).not.toBe("a-old");
    expect(JSON.stringify(messages[2])).toContain("已继续");
    // 旧 assistant 消息未被写入
    expect(JSON.stringify(messages[0])).not.toContain("已继续");
  });
});

describe("stream ownership", () => {
  it.each(["send", "continuation", "subscribe"])("a stopped %s cannot clear or append into the next live request", async (kind) => {
    const readers: { finish: () => void; emit: Parameters<typeof consumeSSE>[2] }[] = [];
    vi.mocked(consumeSSE).mockImplementation((_url, _body, emit) => new Promise<void>((finish) => readers.push({ finish, emit })));
    useChatStore.getState().addAssistantMessage("previous");
    if (kind === "send") await sendMessage("/resume", undefined, "s1");
    else if (kind === "continuation") void sendContinuation("/resume", "s1");
    else void subscribeToSession("s1");
    expect(readers).toHaveLength(1);
    const previousMessageId = useChatStore.getState().messageOrder.at(-1)!;
    readers[0].emit({ event: "text_delta", data: { content: "已收到的内容" } });
    stopGeneration();
    await sendMessage("/resume", undefined, "s1");
    expect(readers).toHaveLength(2);
    const controller = useChatStore.getState().abortController;
    const nextMessageId = useChatStore.getState().messageOrder.at(-1)!;
    const blocks = useChatStore.getState().messagesById[previousMessageId];
    readers[0].emit({ event: "text_delta", data: { content: "不该追加的旧内容" } });
    readers[0].finish();
    await Promise.resolve();
    expect(useChatStore.getState().isStreaming).toBe(true);
    expect(useChatStore.getState().abortController).toBe(controller);
    expect(useChatStore.getState().messagesById[previousMessageId]).toBe(blocks);
    readers[1].emit({ event: "text_delta", data: { content: "新的输出" } });
    await vi.advanceTimersByTimeAsync(100);
    expect(JSON.stringify(useChatStore.getState().messagesById[nextMessageId])).toContain("新的输出");
    readers[1].finish();
    await Promise.resolve();
    expect(useChatStore.getState().isStreaming).toBe(false);
  });
});
