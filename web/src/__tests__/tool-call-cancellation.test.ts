import { beforeEach, afterEach, expect, it, vi } from "vitest";

vi.mock("@/lib/idb-cache", () => ({
  loadCachedMessages: vi.fn().mockResolvedValue(null), saveCachedMessages: vi.fn(),
  deleteCachedMessages: vi.fn(), clearAllCachedMessages: vi.fn(),
}));

import { useChatStore } from "@/stores/chat-store";
import { useSessionStore } from "@/stores/session-store";
import { dispatchSSEEvent, type SSEHandlerContext } from "@/lib/sse-event-handler";
import { cancelToolCall } from "@/lib/api";

const context: SSEHandlerContext = {
  assistantMsgId: "message", effectiveSessionId: "session", isFirstSend: true,
  thinkingInProgress: false, hadStreamError: false,
  batcher: { pushText: vi.fn(), pushThinking: vi.fn(), flush: vi.fn(), dispose: vi.fn(), hasPendingContent: () => false },
};

function emit(event: string, state: string, id = "execution", extra = {}) {
  dispatchSSEEvent({ event, data: { tool_call_id: "call", tool_name: "read_text_file",
    execution_id: id, execution_state: state, ...extra } }, context);
}

function blocks() {
  const message = useChatStore.getState().messages[0];
  if (message.role !== "assistant") throw new Error("expected assistant");
  return message.blocks;
}

beforeEach(() => {
  useChatStore.getState().setMessages([{ id: "message", role: "assistant", blocks: [], timestamp: Date.now() }]);
  useChatStore.setState({ loadedSessionId: "session", pendingApproval: null, pendingQuestion: null });
  useSessionStore.setState({ activeSessionId: "session", sessions: [] });
});
afterEach(() => vi.unstubAllGlobals());

it("updates one queued card through admission, cancellation and final result", () => {
  emit("tool_call_state", "queued");
  expect(blocks()[0]).toMatchObject({ status: "pending", executionState: "queued", executionId: "execution" });
  emit("tool_call_start", "running");
  emit("tool_call_state", "cancelling");
  expect(blocks()).toHaveLength(1);
  expect(blocks()[0]).toMatchObject({ status: "running", executionState: "cancelling" });
  emit("tool_call_end", "cancelled", "execution", { success: false, error: "CANCELLED", result: "cancelled after settlement" });
  expect(blocks()[0]).toMatchObject({ status: "error", executionState: "cancelled", result: "cancelled after settlement" });
});

it("accepts the final SSE payload after the cancel response has already marked the card cancelled", () => {
  emit("tool_call_state", "queued");
  useChatStore.getState().updateAssistantMessage("message", (message) => ({ ...message,
    blocks: message.blocks.map((block) => block.type === "tool_call" ? { ...block, status: "error", executionState: "cancelled" } : block),
  }));
  emit("tool_call_start", "cancelled");
  emit("tool_call_end", "cancelled", "execution", { success: false, error: "CANCELLED", result: "not executed" });
  expect(blocks()).toHaveLength(1);
  expect(blocks()[0]).toMatchObject({ status: "error", result: "not executed" });
});

it("does not apply an old execution result to a reused call ID", () => {
  emit("tool_call_state", "queued", "old");
  emit("tool_call_start", "running", "old");
  emit("tool_call_end", "completed", "old", { success: true });
  emit("tool_call_state", "queued", "new");
  emit("tool_call_start", "running", "new");
  emit("tool_call_end", "cancelled", "old", { success: false, error: "CANCELLED" });
  expect(blocks()[1]).toMatchObject({ executionId: "new", status: "running", executionState: "running" });
});

it("sends one cancellation to the exact session and execution endpoint", async () => {
  const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ call: { status: "cancelling" } })));
  vi.stubGlobal("fetch", fetchMock);
  expect(await cancelToolCall("session/a", "execution")).toEqual({ status: "cancelling" });
  expect(fetchMock).toHaveBeenCalledTimes(1);
  expect(String(fetchMock.mock.calls[0][0])).toContain("/sessions/session%2Fa/tool-calls/execution/cancel");
});
