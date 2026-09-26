import { beforeEach, describe, expect, it, vi } from "vitest";
import type { AssistantBlock, Message, Session } from "@/lib/types";

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

  const richHistory = [
    { role: "user", message_id: "u-rich", content: "还原收据" },
    { role: "assistant", message_id: "a-rich", content: "", reasoning_content: "读取图片并制定计划", tool_calls: [
      { id: "create", function: { name: "task_create", arguments: JSON.stringify({ subtasks: ["读取图片", "核对金额"] }) } },
    ] },
    { role: "tool", tool_call_id: "create", content: "已创建任务清单" },
    { role: "assistant", message_id: "a-update", content: "", thinking: "核对合计", tool_calls: [
      { id: "update", function: { name: "task_update", arguments: { task_index: 0, status: "completed" } } },
    ] },
    { role: "tool", tool_call_id: "update", content: "任务已完成" },
    { role: "assistant", message_id: "a-final", content: "完成了", reasoning_content: "确认结果" },
  ];
  const richPage = { messages: richHistory, total: richHistory.length, offset: 0, limit: 50, hasMore: false };
  const blocks = () => useChatStore.getState().messages.flatMap((message) => message.role === "assistant" ? message.blocks : []);

  it("restores thinking and rich task cards on a cold page entry", async () => {
    vi.mocked(fetchSessionMessages).mockResolvedValue(richPage);
    select("cold-rich");
    await flush();
    expect(blocks().filter((block) => block.type === "thinking").map((block) => block.content))
      .toEqual(["读取图片并制定计划", "核对合计", "确认结果"]);
    expect(blocks().find((block) => block.type === "tool_call" && block.toolCallId === "create"))
      .toMatchObject({ taskList: [{ status: "pending" }, { status: "pending" }] });
    expect(blocks().find((block) => block.type === "tool_call" && block.toolCallId === "update"))
      .toMatchObject({ taskList: [{ content: "读取图片", status: "completed" }, { content: "核对金额", status: "pending" }] });
    expect(blocks().map((block) => block.type)).toEqual([
      "thinking", "tool_call", "thinking", "tool_call", "thinking", "text",
    ]);
  });

  it("restores a paged task update from its server snapshot before loading the creation", async () => {
    const snapshot = { items: [{ title: "读取图片", status: "completed" }, { title: "核对金额", status: "pending" }] };
    const tail = { ...richPage, offset: 3, hasMore: true, messages: [
      { ...richHistory[3], tool_calls: [{ id: "update", task_list: snapshot,
        function: { name: "task_update", arguments: JSON.stringify({ task_index: 0, status: "completed" }) } }] },
      ...richHistory.slice(4),
    ] };
    vi.mocked(fetchSessionMessages).mockResolvedValueOnce(tail).mockResolvedValueOnce(richPage);
    select("paged-rich");
    await flush();
    expect(blocks().find((block) => block.type === "tool_call" && block.toolCallId === "update"))
      .toMatchObject({ taskList: [{ content: "读取图片", status: "completed" }, { content: "核对金额", status: "pending" }] });
    await useChatStore.getState().loadOlderMessages();
    expect(blocks().filter((block) => block.type === "tool_call" && block.taskList?.length)).toHaveLength(2);
    expect(blocks().filter((block) => block.type === "thinking")).toHaveLength(3);
  });

  it.each(["cache-first", "http-first"])("keeps rich cards and measured timing without duplicates (%s)", async (order) => {
    const cache = deferred<Message[] | null>();
    const network = deferred<typeof richPage>();
    vi.mocked(loadCachedMessages).mockReturnValue(cache.promise);
    vi.mocked(fetchSessionMessages).mockReturnValue(network.promise);
    const cachedBlocks: AssistantBlock[] = [
      { type: "thinking", content: "读取图片并制定计划", duration: 24 },
      { type: "tool_call", toolCallId: "create", name: "task_create", args: {}, status: "success" },
      { type: "task_list", items: [{ index: 0, content: "读取图片", status: "pending" }] },
      { type: "subagent", name: "explorer", reason: "核验", status: "done", iterations: 1, toolCalls: 1, tools: [] },
    ];
    const cachedMessages: Message[] = [user("u-rich", "还原收据"), { id: "local-rich", role: "assistant", blocks: cachedBlocks }];
    select(`rich-${order}`);
    if (order === "cache-first") { cache.resolve(cachedMessages); await flush(); network.resolve(richPage); }
    else { network.resolve(richPage); await flush(); cache.resolve(cachedMessages); }
    await flush();
    expect(blocks().filter((block) => block.type === "thinking")).toHaveLength(3);
    expect(blocks()[0]).toMatchObject({ type: "thinking", duration: 24 });
    expect(blocks().filter((block) => block.type === "tool_call" && block.taskList?.length)).toHaveLength(2);
    expect(blocks().find((block) => block.type === "tool_call" && block.toolCallId === "update"))
      .toMatchObject({ taskList: [{ status: "completed" }, { status: "pending" }] });
    expect(blocks().filter((block) => block.type === "task_list")).toHaveLength(0);
    expect(blocks().filter((block) => block.type === "subagent")).toHaveLength(1);
    for (let i = 0; i < 2; i++) {
      vi.mocked(fetchSessionMessages).mockResolvedValue(richPage);
      await refreshSessionMessagesFromBackend(`rich-${order}`);
    }
    expect(blocks().filter((block) => block.type === "thinking")).toHaveLength(3);
    expect(blocks().filter((block) => block.type === "tool_call" && block.taskList?.length)).toHaveLength(2);
    expect(blocks().filter((block) => block.type === "subagent")).toHaveLength(1);
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
