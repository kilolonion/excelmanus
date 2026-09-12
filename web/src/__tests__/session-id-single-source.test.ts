/**
 * session id 单源：session-store.activeSessionId 是唯一可变事实源。
 *
 * chat-store 不得再持有可写入的 currentSessionId，否则会与 session-store 分叉。
 * switchSession 只负责加载消息，不得回写 activeSessionId。
 */

import { beforeEach, describe, expect, it, vi } from "vitest";

const mem = new Map<string, string>();
vi.stubGlobal("localStorage", {
  getItem: (k: string) => mem.get(k) ?? null,
  setItem: (k: string, v: string) => {
    mem.set(k, String(v));
  },
  removeItem: (k: string) => {
    mem.delete(k);
  },
  clear: () => {
    mem.clear();
  },
  key: (i: number) => [...mem.keys()][i] ?? null,
  get length() {
    return mem.size;
  },
});

vi.mock("@/lib/idb-cache", () => ({
  loadCachedMessages: vi.fn().mockResolvedValue(null),
  saveCachedMessages: vi.fn().mockResolvedValue(undefined),
  deleteCachedMessages: vi.fn().mockResolvedValue(undefined),
  clearAllCachedMessages: vi.fn().mockResolvedValue(undefined),
}));

vi.mock("@/lib/api", () => ({
  fetchSessionMessages: vi.fn().mockResolvedValue([]),
  fetchSessionExcelEvents: vi.fn().mockResolvedValue({
    diffs: [],
    previews: [],
    affected_files: [],
  }),
  clearAllSessions: vi.fn().mockResolvedValue(undefined),
}));

vi.mock("@/stores/excel-store", () => ({
  useExcelStore: {
    getState: () => ({
      diffs: [],
    }),
  },
}));

vi.mock("@/lib/session-title", () => ({
  deriveSessionTitleFromMessages: vi.fn(),
  isFallbackSessionTitle: vi.fn().mockReturnValue(true),
  buildDefaultSessionTitle: (id: string) => `会话 ${id.slice(0, 8)}`,
}));

import { useChatStore } from "@/stores/chat-store";
import { useSessionStore } from "@/stores/session-store";

function resetStores() {
  mem.clear();
  useSessionStore.setState({ sessions: [], activeSessionId: null });
  useChatStore.setState({
    messages: [],
    messageOrder: [],
    messagesById: {},
    messageIndexById: {},
    activeStreamId: null,
    latestSeq: 0,
    resumeFailedReason: null,
    isStreaming: false,
    pendingApproval: null,
    _lastDismissedApprovalId: null,
    pendingQuestion: null,
    abortController: null,
    pipelineStatus: null,
    batchProgress: null,
    toolProgress: {},
    isLoadingMessages: false,
    loadedSessionId: null,
  });
  useChatStore.getState().switchSession(null);
}

describe("session id 单源", () => {
  beforeEach(() => {
    resetStores();
  });

  it("chat-store 没有可写入的 currentSessionId", () => {
    const state = useChatStore.getState() as unknown as Record<string, unknown>;
    expect("currentSessionId" in state).toBe(false);
  });

  it("切换会话只更新 session-store；switchSession 不回写 activeSessionId", () => {
    useSessionStore.getState().setActiveSession("s1");
    useChatStore.getState().switchSession("s1");
    expect(useSessionStore.getState().activeSessionId).toBe("s1");
    expect("currentSessionId" in (useChatStore.getState() as object)).toBe(false);

    useChatStore.getState().switchSession("s2");
    expect(useSessionStore.getState().activeSessionId).toBe("s1");
    expect(
      (useChatStore.getState() as { currentSessionId?: string | null }).currentSessionId ?? null,
    ).toBeNull();
    expect(useChatStore.getState().loadedSessionId).toBe("s2");

    useSessionStore.getState().setActiveSession("s2");
    useChatStore.getState().switchSession("s2");
    expect(useSessionStore.getState().activeSessionId).toBe("s2");
    expect("currentSessionId" in (useChatStore.getState() as object)).toBe(false);
  });
});
