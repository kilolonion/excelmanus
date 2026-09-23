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
