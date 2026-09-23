import { beforeEach, describe, expect, it, vi } from "vitest";
import type { Message, Session } from "@/lib/types";

vi.mock("@/lib/idb-cache", () => ({
  loadCachedMessages: vi.fn(), saveCachedMessages: vi.fn().mockResolvedValue(undefined),
  deleteCachedMessages: vi.fn().mockResolvedValue(undefined), clearAllCachedMessages: vi.fn(),
}));
vi.mock("@/lib/api", () => ({
  fetchSessionMessages: vi.fn(), fetchSessionExcelEvents: vi.fn(), clearAllSessions: vi.fn(),
}));
vi.mock("@/stores/excel-store", () => ({ useExcelStore: { getState: () => ({ diffs: [] }) } }));

import { useChatStore, refreshSessionMessagesFromBackend } from "@/stores/chat-store";
import { useSessionStore } from "@/stores/session-store";
import { fetchSessionMessages, fetchSessionExcelEvents } from "@/lib/api";
import { loadCachedMessages } from "@/lib/idb-cache";

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
}
const user = (id: string, content = id): Message => ({ id, role: "user", content, timestamp: 1 });
const page = (id: string, total = 1, offset = 0) => ({
  messages: [{ role: "user", message_id: id, content: id }], total, offset, limit: 50, hasMore: offset > 0,
});
function select(id: string, patch: Partial<Session> = {}) {
  useSessionStore.setState({ sessions: [{ id, title: "Test", messageCount: 1, inFlight: false, ...patch }], activeSessionId: id });
  useChatStore.getState().switchSession(id);
}
const flush = async () => { for (let i = 0; i < 8; i++) await Promise.resolve(); };

beforeEach(() => {
  useChatStore.getState().clearMessages();
  useChatStore.setState({ loadedSessionId: null, loadedMessageTotal: null, isStreaming: false, abortController: null });
  useSessionStore.setState({ sessions: [], activeSessionId: null });
  vi.mocked(loadCachedMessages).mockReset().mockResolvedValue(null);
  vi.mocked(fetchSessionMessages).mockReset().mockResolvedValue([]);
  vi.mocked(fetchSessionExcelEvents).mockReset().mockResolvedValue({ diffs: [], previews: [], affected_files: [] });
});

describe("session loading lifecycle", () => {
  it("opens a known blank conversation synchronously without IDB/history/events", async () => {
    select("blank", { blank: true, messageCount: 0 });
    expect(useChatStore.getState()).toMatchObject({ loadedSessionId: "blank", isLoadingMessages: false, loadedMessageTotal: 0 });
    useChatStore.getState().switchSession("blank");
    await flush();
    expect(loadCachedMessages).not.toHaveBeenCalled();
    expect(fetchSessionMessages).not.toHaveBeenCalled();
    expect(fetchSessionExcelEvents).not.toHaveBeenCalled();
  });

  it("does not wait for a blocked IndexedDB and deduplicates repeated switches", async () => {
    vi.mocked(loadCachedMessages).mockReturnValue(new Promise(() => {}));
    vi.mocked(fetchSessionMessages).mockResolvedValue(page("remote"));
    select("idb-blocked");
    useChatStore.getState().switchSession("idb-blocked");
    expect(fetchSessionMessages).toHaveBeenCalledOnce();
    expect(fetchSessionMessages).toHaveBeenCalledWith("idb-blocked", 50, 0, expect.objectContaining({ tail: true, signal: expect.any(AbortSignal) }));
    await flush();
    expect(useChatStore.getState().messageOrder).toEqual(["remote"]);
    expect(useChatStore.getState().isLoadingMessages).toBe(false);
    expect(fetchSessionExcelEvents).not.toHaveBeenCalled();
  });

  it("renders cached history without blocking input while HTTP is pending", async () => {
    const network = deferred<ReturnType<typeof page>>();
    vi.mocked(loadCachedMessages).mockResolvedValue([user("cached")]);
    vi.mocked(fetchSessionMessages).mockReturnValue(network.promise);
    select("cache-ready");
    await flush();
    expect(useChatStore.getState()).toMatchObject({ isLoadingMessages: false, isRefreshingMessages: true, messageOrder: ["cached"] });
    network.resolve(page("fresh"));
    await flush();
    expect(useChatStore.getState().messageOrder).toEqual(["fresh"]);
  });

  it("ignores a late cache response after the server has already loaded", async () => {
    const cache = deferred<Message[] | null>();
    vi.mocked(loadCachedMessages).mockReturnValue(cache.promise);
    vi.mocked(fetchSessionMessages).mockResolvedValue(page("authoritative"));
    select("late-cache");
    await flush();
    cache.resolve([user("stale")]);
    await flush();
    expect(useChatStore.getState().messageOrder).toEqual(["authoritative"]);
  });

  it("aborts old requests and does not let an A→B→A response overwrite the latest visit", async () => {
    const old = deferred<ReturnType<typeof page>>();
    const newest = deferred<ReturnType<typeof page>>();
    vi.mocked(fetchSessionMessages).mockReturnValueOnce(old.promise).mockReturnValueOnce(newest.promise);
    select("race-a");
    const signal = vi.mocked(fetchSessionMessages).mock.calls[0][3]?.signal;
    select("race-b", { blank: true, messageCount: 0 });
    expect(signal?.aborted).toBe(true);
    expect(useChatStore.getState().messages).toEqual([]);
    select("race-a");
    newest.resolve(page("newest"));
    await flush();
    old.resolve(page("obsolete"));
    await flush();
    expect(useChatStore.getState().messageOrder).toEqual(["newest"]);
  });

  it("never overwrites a new local turn with an earlier revalidation", async () => {
    const network = deferred<ReturnType<typeof page>>();
    vi.mocked(loadCachedMessages).mockResolvedValue([user("cached-send")]);
    vi.mocked(fetchSessionMessages).mockReturnValue(network.promise);
    select("send-race");
    await flush();
    useChatStore.getState().addUserMessage("new-local-turn", "new prompt");
    network.resolve(page("old-server"));
    await flush();
    expect(useChatStore.getState().messageOrder).toEqual(["cached-send", "new-local-turn"]);
  });

  it("retains the raw pagination offset after revalidating an expanded history", async () => {
    vi.mocked(fetchSessionMessages)
      .mockResolvedValueOnce(page("tail", 200, 150))
      .mockResolvedValueOnce(page("older", 200, 100))
      .mockResolvedValueOnce(page("tail", 200, 150))
      .mockResolvedValueOnce(page("oldest", 200, 50));
    select("expanded-history");
    await flush();
    await useChatStore.getState().loadOlderMessages();
    await refreshSessionMessagesFromBackend("expanded-history");
    await useChatStore.getState().loadOlderMessages();
    expect(vi.mocked(fetchSessionMessages).mock.calls.at(-1)?.slice(0, 3)).toEqual(["expanded-history", 50, 50]);
    expect(useChatStore.getState().messageOrder).toEqual(["oldest", "older", "tail"]);
  });
});
