import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/sse", () => ({ consumeSSE: vi.fn(), SSEError: class extends Error {} }));
vi.mock("@/lib/idb-cache", () => ({
  loadCachedMessages: vi.fn().mockResolvedValue(null), saveCachedMessages: vi.fn().mockResolvedValue(undefined),
  deleteCachedMessages: vi.fn().mockResolvedValue(undefined), clearAllCachedMessages: vi.fn().mockResolvedValue(undefined),
}));

import { consumeSSE } from "@/lib/sse";
import { resumeAfterInteraction } from "@/lib/chat-actions";
import { answerQuestion, fetchSessionDetail, submitApproval } from "@/lib/api";
import { useChatStore } from "@/stores/chat-store";
import { useSessionStore } from "@/stores/session-store";
import { useUIStore } from "@/stores/ui-store";

describe("restored interaction handoff", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useChatStore.getState().setMessages([]);
    useChatStore.setState({ isStreaming: false, abortController: null, loadedSessionId: "s1", pendingQuestion: null, pendingApproval: null });
    useSessionStore.setState({ activeSessionId: "s1", sessions: [{ id: "s1", title: "恢复任务", messageCount: 0, inFlight: false }] });
    useUIStore.setState({ configReady: true, configError: null });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("{}")));
  });
  afterEach(() => vi.unstubAllGlobals());

  it("continues through the existing chat stream once without waiting in the submit handler", async () => {
    let finish!: () => void;
    vi.mocked(consumeSSE).mockImplementation(() => new Promise<void>((resolve) => { finish = resolve; }));
    expect(resumeAfterInteraction("s1", { resume_required: true })).toBeUndefined();
    expect(useChatStore.getState().isStreaming).toBe(true);
    resumeAfterInteraction("s1", { resume_required: true });
    await vi.waitFor(() => expect(consumeSSE).toHaveBeenCalledTimes(1));
    expect(vi.mocked(consumeSSE).mock.calls[0][1]).toMatchObject({ message: "/resume", session_id: "s1" });
    expect(useChatStore.getState().messages[0]).toMatchObject({ role: "user", content: "继续执行" });
    finish();
    await vi.waitFor(() => expect(useChatStore.getState().isStreaming).toBe(false));
  });

  it("leaves live waiters on their stream and does not modify a different selected session", () => {
    resumeAfterInteraction("s1", { resume_required: false });
    resumeAfterInteraction("another", { resume_required: true });
    expect(consumeSSE).not.toHaveBeenCalled();
    expect(useChatStore.getState().messages).toEqual([]);
  });

  it("preserves the recovery response from both existing submission endpoints", async () => {
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockImplementation(async () => new Response(JSON.stringify({ status: "resolved", resume_required: true })));
    expect(await answerQuestion("s1", "q1", "1\n2")).toMatchObject({ resume_required: true });
    expect(await submitApproval("s1", "a1", "accept")).toMatchObject({ resume_required: true });
    expect(JSON.parse(String(fetchMock.mock.calls[0][1]?.body))).toEqual({ question_id: "q1", answer: "1\n2" });
  });

  it("hydrates the remaining question count from the restored session", async () => {
    vi.mocked(fetch).mockResolvedValue(new Response(JSON.stringify({
      id: "s1", pending_question: { id: "q2", text: "下一题", multi_select: false, options: [], queue_size: 2 },
    })));
    expect((await fetchSessionDetail("s1"))?.pendingQuestion).toMatchObject({ id: "q2", queueSize: 2 });
  });
});
