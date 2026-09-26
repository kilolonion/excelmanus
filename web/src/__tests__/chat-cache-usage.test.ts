import { afterEach, beforeEach, expect, it, vi } from "vitest";

vi.mock("@/lib/sse", () => ({ consumeSSE: vi.fn(), SSEError: class extends Error {} }));
vi.mock("@/lib/idb-cache", () => ({
  loadCachedMessages: vi.fn().mockResolvedValue(null), saveCachedMessages: vi.fn().mockResolvedValue(undefined),
  deleteCachedMessages: vi.fn(), clearAllCachedMessages: vi.fn(),
}));

import { sendMessage, sendContinuation, subscribeToSession } from "@/lib/chat-actions";
import { consumeSSE } from "@/lib/sse";
import { useChatStore } from "@/stores/chat-store";
import { useSessionStore } from "@/stores/session-store";
import { useUIStore } from "@/stores/ui-store";

beforeEach(() => {
  vi.useFakeTimers();
  vi.clearAllMocks();
  vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => setTimeout(() => callback(0), 16));
  vi.stubGlobal("cancelAnimationFrame", clearTimeout);
  vi.stubGlobal("fetch", vi.fn(async () => new Response("{}")));
  useSessionStore.setState({ activeSessionId: "s1", sessions: [{ id: "s1", workspaceId: "w1", title: "Cache", messageCount: 0, inFlight: false }] });
  useChatStore.getState().setMessages([]);
  useChatStore.setState({ isStreaming: false, abortController: null, loadedSessionId: "s1", pendingQuestion: null, pendingApproval: null,
    activeStreamId: "stream1", latestSeq: 0 });
  useUIStore.setState({ configReady: true, chatMode: "write" });
});

afterEach(() => { vi.clearAllTimers(); vi.useRealTimers(); vi.unstubAllGlobals(); });

it.each(["send", "continuation", "subscribe"])("retains cache counts once for a %s stream with both reply events", async (kind) => {
  vi.mocked(consumeSSE).mockImplementation(async (_url, _body, emit) => {
    const data = { turn_id: "t1", content: "完成", prompt_tokens: 1000, completion_tokens: 100,
      total_tokens: 1100, iterations: 2, cached_tokens: 800 };
    emit({ event: "turn_reply", data });
    // Final reply may come from an older transport without cache usage.
    emit({ event: "reply", data: { ...data, cached_tokens: undefined } });
    emit({ event: "done", data: {} });
  });
  useChatStore.getState().addAssistantMessage("a1");
  if (kind === "send") await sendMessage("/resume", undefined, "s1");
  else if (kind === "continuation") await sendContinuation("continue", "s1");
  else await subscribeToSession("s1");
  await vi.advanceTimersByTimeAsync(50);
  const blocks = useChatStore.getState().messages.flatMap((m) => m.role === "assistant" ? m.blocks : []);
  expect(blocks.filter((b) => b.type === "token_stats")).toEqual([{
    type: "token_stats", promptTokens: 1000, completionTokens: 100, totalTokens: 1100,
    iterations: 2, cachedTokens: 800,
  }]);
});

it("accumulates legacy continuation cache counts with existing usage", async () => {
  useChatStore.getState().setMessages([{ id: "a1", role: "assistant", blocks: [{
    type: "token_stats", promptTokens: 5000, cachedTokens: 4000, completionTokens: 10,
    totalTokens: 5010, iterations: 1,
  }] }]);
  vi.mocked(consumeSSE).mockImplementation(async (_url, _body, emit) => {
    emit({ event: "reply", data: { content: "完成", prompt_tokens: 10000, cached_tokens: 1000,
      completion_tokens: 20, total_tokens: 10020, iterations: 1 } });
    emit({ event: "done", data: {} });
  });
  await sendContinuation("continue", "s1");
  const message = useChatStore.getState().messagesById.a1;
  expect(message.role === "assistant" && message.blocks.find((b) => b.type === "token_stats"))
    .toMatchObject({ promptTokens: 15000, cachedTokens: 5000, totalTokens: 15030, iterations: 2 });
});
