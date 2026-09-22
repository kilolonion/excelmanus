/**
 * chat-store.ts 单元测试
 *
 * 覆盖：
 * - _buildMessageEntities（通过 setMessages 间接测试）
 * - _setMessagesSnapshot（通过 setMessages 间接测试）
 * - _patchMessageById（通过 appendBlock / updateAssistantMessage 间接测试）
 * - addUserMessage / addAssistantMessage 四层索引一致性
 * - appendBlock O(1) 定位更新
 * - setMessages 快照恢复后实体索引重建
 * - 重复 ID 消息去重行为
 * - setStreamState / markResumeFailed / clearResumeFailed
 */

import { describe, it, expect, beforeEach } from "vitest";

// mock idb-cache（chat-store 在顶层 import 了它）
import { vi } from "vitest";
vi.mock("@/lib/idb-cache", () => ({
  loadCachedMessages: vi.fn().mockResolvedValue(null),
  saveCachedMessages: vi.fn().mockResolvedValue(undefined),
  deleteCachedMessages: vi.fn().mockResolvedValue(undefined),
  clearAllCachedMessages: vi.fn().mockResolvedValue(undefined),
}));

// mock api（chat-store 在顶层 import 了它）
vi.mock("@/lib/api", () => ({
  fetchSessionMessages: vi.fn().mockResolvedValue([]),
  fetchSessionExcelEvents: vi.fn().mockResolvedValue({ diffs: [], previews: [] }),
  clearAllSessions: vi.fn().mockResolvedValue(undefined),
}));

// mock session-store
vi.mock("@/stores/session-store", () => ({
  useSessionStore: {
    getState: () => ({
      activeSessionId: null,
      sessions: [],
      setActiveSession: vi.fn(),
      setSessions: vi.fn(),
      updateSessionTitle: vi.fn(),
    }),
  },
}));

// mock excel-store
vi.mock("@/stores/excel-store", () => ({
  useExcelStore: {
    getState: () => ({
      diffs: [],
    }),
  },
}));

// mock session-title
vi.mock("@/lib/session-title", () => ({
  deriveSessionTitleFromMessages: vi.fn(),
  isFallbackSessionTitle: vi.fn().mockReturnValue(true),
}));

import { useChatStore } from "@/stores/chat-store";
import { fetchSessionMessages } from "@/lib/api";
import { refreshSessionMessagesFromBackend } from "@/stores/chat-store";
import type { Message, AssistantBlock, SubagentRun } from "@/lib/types";

// ---------------------------------------------------------------------------
// helpers
// ---------------------------------------------------------------------------

function resetStore() {
  useChatStore.setState({
    messages: [],
    messageOrder: [],
    messagesById: {},
    messageIndexById: {},
    loadedSessionId: null,
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
  });
}

/** 验证四层索引一致性 */
function assertIndexConsistency() {
  const s = useChatStore.getState();
  // messageOrder 长度 === messages 长度
  expect(s.messageOrder.length).toBe(s.messages.length);
  // 遍历 messageOrder，验证三层映射一致
  for (let i = 0; i < s.messageOrder.length; i++) {
    const id = s.messageOrder[i];
    // messagesById 包含该 ID
    expect(s.messagesById[id]).toBeDefined();
    // messageIndexById 的索引 === i
    expect(s.messageIndexById[id]).toBe(i);
    // messages[i] 的 id === messageOrder[i]
    expect(s.messages[i].id).toBe(id);
    // messages[i] === messagesById[id]（同引用）
    expect(s.messages[i]).toBe(s.messagesById[id]);
  }
}

function makeUserMsg(id: string, content = "hello"): Message {
  return { id, role: "user", content, timestamp: Date.now() };
}

function makeAssistantMsg(id: string, blocks: AssistantBlock[] = []): Message {
  return { id, role: "assistant", blocks, timestamp: Date.now() };
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("background task reconciliation", () => {
  const runningBlock = {
    type: "subagent" as const, name: "explorer", reason: "统计", conversationId: "run-1",
    iterations: 1, toolCalls: 1, status: "running" as const, tools: [],
  };
  const completed: SubagentRun = {
    run_id: "run-1", agent_name: "explorer", task: "统计", file_paths: [], background: true,
    status: "completed", created_at: 1, started_at: 2, finished_at: 3, iteration: 3,
    tool_calls: 4, last_tool: "edit_spreadsheet", resumed_from: null, changed_files: ["./sales.xlsx"],
    result: { stop_reason: "completed", output: "统计完成", diagnostic: null, iterations: 3,
      tool_calls_count: 4, structured_changes: [], observed_files: [] },
  };
  beforeEach(() => {
    resetStore();
    useChatStore.setState({ loadedSessionId: "session-1", isStreaming: false });
    useChatStore.getState().setMessages([makeAssistantMsg("m1", [runningBlock])]);
  });

  it("updates an ended stream's card and affected files from the task result", () => {
    useChatStore.getState().syncSubagentRuns("session-1", [completed]);
    const message = useChatStore.getState().messages[0];
    expect(message.role === "assistant" && message.blocks[0]).toMatchObject({
      status: "done", runStatus: "completed", background: true, summary: "统计完成", success: true,
      iterations: 3, toolCalls: 4,
    });
    expect(message.role === "assistant" && message.affectedFiles).toEqual(["./sales.xlsx"]);
    assertIndexConsistency();
    const previous = useChatStore.getState().messages;
    useChatStore.getState().syncSubagentRuns("session-1", [completed]);
    expect(useChatStore.getState().messages).toBe(previous);
  });

  it("never applies an old session response to the selected session", () => {
    const previous = useChatStore.getState().messages;
    useChatStore.getState().syncSubagentRuns("session-2", [completed]);
    expect(useChatStore.getState().messages).toBe(previous);
  });

  it("matches exact run identities, including late SSE events", () => {
    const previous = useChatStore.getState().messages;
    useChatStore.getState().syncSubagentRuns("session-1", [{ ...completed, run_id: "unknown" }]);
    useChatStore.getState().updateSubagentBlock("m1", "unknown", () => ({ ...runningBlock, status: "done" }));
    expect(useChatStore.getState().messages).toBe(previous);
  });

  it("does not regress an already settled run when an older query returns", () => {
    useChatStore.getState().syncSubagentRuns("session-1", [completed]);
    const previous = useChatStore.getState().messages;
    useChatStore.getState().syncSubagentRuns("session-1", [{ ...completed, status: "running", result: null }]);
    expect(useChatStore.getState().messages).toBe(previous);
  });
});

describe("chat-store", () => {
  beforeEach(() => {
    resetStore();
    vi.mocked(fetchSessionMessages).mockReset().mockResolvedValue([]);
  });

  it("loads the newest page first and prepends older pages on demand", async () => {
    const fetchMessages = vi.mocked(fetchSessionMessages);
    fetchMessages
      .mockResolvedValueOnce({
        messages: [
          { role: "user", content: "new-1", message_id: "m-new-1" },
          { role: "assistant", content: "new-2", message_id: "m-new-2" },
        ],
        total: 4,
        offset: 2,
        hasMore: true,
      } as never)
      .mockResolvedValueOnce({
        messages: [
          { role: "user", content: "old-1", message_id: "m-old-1" },
          { role: "assistant", content: "old-2", message_id: "m-old-2" },
        ],
        total: 4,
        offset: 0,
        hasMore: false,
      } as never);
    useChatStore.setState({ loadedSessionId: "progressive-session" });

    await refreshSessionMessagesFromBackend("progressive-session");
    expect(useChatStore.getState().messages.map((message) => message.id)).toEqual(["m-new-1", "m-new-2"]);
    expect(useChatStore.getState().hasMoreMessages).toBe(true);

    await useChatStore.getState().loadOlderMessages();
    expect(useChatStore.getState().messages.map((message) => message.id)).toEqual([
      "m-old-1", "m-old-2", "m-new-1", "m-new-2",
    ]);
    expect(useChatStore.getState().hasMoreMessages).toBe(false);
  });

  it("deduplicates concurrent history refreshes for one session", async () => {
    const fetchMessages = vi.mocked(fetchSessionMessages);
    let resolve: ((value: unknown) => void) | undefined;
    fetchMessages.mockImplementationOnce(() => new Promise((done) => { resolve = done; }) as never);
    useChatStore.setState({ loadedSessionId: "dedupe-session" });

    const first = refreshSessionMessagesFromBackend("dedupe-session");
    const second = refreshSessionMessagesFromBackend("dedupe-session");
    expect(fetchMessages).toHaveBeenCalledOnce();
    resolve?.({ messages: [], total: 0, offset: 0, hasMore: false });
    await Promise.all([first, second]);
  });

  // ── setMessages（快照恢复 → _buildMessageEntities）──────────
  describe("setMessages (snapshot restore)", () => {
    it("从空状态恢复消息列表，四层索引一致", () => {
      const msgs: Message[] = [
        makeUserMsg("u1", "hi"),
        makeAssistantMsg("a1"),
        makeUserMsg("u2", "bye"),
      ];
      useChatStore.getState().setMessages(msgs);
      const s = useChatStore.getState();

      expect(s.messages.length).toBe(3);
      expect(s.messageOrder).toEqual(["u1", "a1", "u2"]);
      assertIndexConsistency();
    });

    it("空数组恢复后索引全部清空", () => {
      useChatStore.getState().setMessages([makeUserMsg("u1")]);
      useChatStore.getState().setMessages([]);

      const s = useChatStore.getState();
      expect(s.messages.length).toBe(0);
      expect(s.messageOrder.length).toBe(0);
      expect(Object.keys(s.messagesById).length).toBe(0);
      expect(Object.keys(s.messageIndexById).length).toBe(0);
    });

    it("重复 ID 消息去重：后者覆盖前者", () => {
      const msgs: Message[] = [
        makeUserMsg("dup", "first"),
        makeUserMsg("dup", "second"),
      ];
      useChatStore.getState().setMessages(msgs);
      const s = useChatStore.getState();

      // 应该只有 1 条消息
      expect(s.messages.length).toBe(1);
      expect(s.messageOrder).toEqual(["dup"]);
      // 内容是后者
      expect((s.messagesById["dup"] as Extract<Message, { role: "user" }>).content).toBe("second");
      assertIndexConsistency();
    });

    it("空 ID 消息被过滤", () => {
      const msgs: Message[] = [
        { id: "", role: "user", content: "no id" } as Message,
        makeUserMsg("valid", "ok"),
        { id: "  ", role: "user", content: "whitespace id" } as Message,
      ];
      useChatStore.getState().setMessages(msgs);
      const s = useChatStore.getState();

      expect(s.messages.length).toBe(1);
      expect(s.messageOrder).toEqual(["valid"]);
      assertIndexConsistency();
    });
  });

  // ── addUserMessage ──────────────────────────────────────────
  describe("addUserMessage", () => {
    it("追加用户消息后四层索引一致", () => {
      useChatStore.getState().addUserMessage("u1", "hello");
      assertIndexConsistency();

      const s = useChatStore.getState();
      expect(s.messages.length).toBe(1);
      expect(s.messageOrder).toEqual(["u1"]);
      expect(s.messagesById["u1"].role).toBe("user");
      expect((s.messagesById["u1"] as Extract<Message, { role: "user" }>).content).toBe("hello");
      expect(s.messageIndexById["u1"]).toBe(0);
    });

    it("连续追加多条消息索引正确", () => {
      useChatStore.getState().addUserMessage("u1", "a");
      useChatStore.getState().addUserMessage("u2", "b");
      useChatStore.getState().addUserMessage("u3", "c");

      assertIndexConsistency();
      const s = useChatStore.getState();
      expect(s.messages.length).toBe(3);
      expect(s.messageOrder).toEqual(["u1", "u2", "u3"]);
      expect(s.messageIndexById["u1"]).toBe(0);
      expect(s.messageIndexById["u2"]).toBe(1);
      expect(s.messageIndexById["u3"]).toBe(2);
    });

    it("带文件附件的用户消息", () => {
      useChatStore.getState().addUserMessage("u1", "with file", [
        { filename: "test.xlsx", path: "/tmp/test.xlsx", size: 1024 },
      ]);
      const msg = useChatStore.getState().messagesById["u1"];
      expect(msg.role).toBe("user");
      expect((msg as Extract<Message, { role: "user" }>).files?.length).toBe(1);
    });
  });

  // ── addAssistantMessage ─────────────────────────────────────
  describe("addAssistantMessage", () => {
    it("追加 assistant 消息后 blocks 为空数组", () => {
      useChatStore.getState().addAssistantMessage("a1");
      assertIndexConsistency();

      const s = useChatStore.getState();
      expect(s.messages.length).toBe(1);
      const msg = s.messagesById["a1"];
      expect(msg.role).toBe("assistant");
      expect((msg as Extract<Message, { role: "assistant" }>).blocks).toEqual([]);
    });

    it("user + assistant 交替追加索引正确", () => {
      useChatStore.getState().addUserMessage("u1", "q");
      useChatStore.getState().addAssistantMessage("a1");
      useChatStore.getState().addUserMessage("u2", "q2");
      useChatStore.getState().addAssistantMessage("a2");

      assertIndexConsistency();
      expect(useChatStore.getState().messageOrder).toEqual(["u1", "a1", "u2", "a2"]);
    });
  });

  // ── appendBlock（间接测试 _patchMessageById O(1) 定位）─────
  describe("appendBlock", () => {
    it("向 assistant 消息追加 block", () => {
      useChatStore.getState().addAssistantMessage("a1");
      useChatStore.getState().appendBlock("a1", { type: "text", content: "hello" });

      assertIndexConsistency();
      const msg = useChatStore.getState().messagesById["a1"];
      expect(msg.role).toBe("assistant");
      expect((msg as Extract<Message, { role: "assistant" }>).blocks.length).toBe(1);
      expect((msg as Extract<Message, { role: "assistant" }>).blocks[0]).toEqual({ type: "text", content: "hello" });
    });

    it("连续追加多个 block", () => {
      useChatStore.getState().addAssistantMessage("a1");
      useChatStore.getState().appendBlock("a1", { type: "text", content: "1" });
      useChatStore.getState().appendBlock("a1", { type: "text", content: "2" });
      useChatStore.getState().appendBlock("a1", {
        type: "tool_call",
        name: "inspect_spreadsheet",
        args: {},
        status: "running",
      });

      const msg = useChatStore.getState().messagesById["a1"] as Extract<Message, { role: "assistant" }>;
      expect(msg.blocks.length).toBe(3);
      expect(msg.blocks[2].type).toBe("tool_call");
    });

    it("对不存在的消息 ID appendBlock 不崩溃", () => {
      useChatStore.getState().addAssistantMessage("a1");
      // 不应抛错
      useChatStore.getState().appendBlock("nonexistent", { type: "text", content: "x" });
      // a1 不受影响
      expect((useChatStore.getState().messagesById["a1"] as Extract<Message, { role: "assistant" }>).blocks.length).toBe(0);
    });

    it("对 user 消息 appendBlock 无效果", () => {
      useChatStore.getState().addUserMessage("u1", "hi");
      useChatStore.getState().appendBlock("u1", { type: "text", content: "x" });
      // user 消息没有 blocks
      expect(useChatStore.getState().messagesById["u1"].role).toBe("user");
    });

    it("messagesById 与 messages[idx] 同步更新", () => {
      useChatStore.getState().addUserMessage("u1", "q");
      useChatStore.getState().addAssistantMessage("a1");
      useChatStore.getState().appendBlock("a1", { type: "text", content: "reply" });

      const s = useChatStore.getState();
      const idx = s.messageIndexById["a1"];
      // messages[idx] 和 messagesById["a1"] 应该是同一条消息
      expect(s.messages[idx]).toBe(s.messagesById["a1"]);
      expect((s.messages[idx] as Extract<Message, { role: "assistant" }>).blocks[0]).toMatchObject({ type: "text", content: "reply" });
    });
  });

  // ── updateAssistantMessage ──────────────────────────────────
  describe("updateAssistantMessage", () => {
    it("通过 updater 修改 blocks", () => {
      useChatStore.getState().addAssistantMessage("a1");
      useChatStore.getState().appendBlock("a1", { type: "text", content: "old" });

      useChatStore.getState().updateAssistantMessage("a1", (m) => ({
        ...m,
        blocks: m.blocks.map((b) =>
          b.type === "text" ? { ...b, content: "new" } : b,
        ),
      }));

      const msg = useChatStore.getState().messagesById["a1"] as Extract<Message, { role: "assistant" }>;
      expect(msg.blocks[0]).toMatchObject({ type: "text", content: "new" });
      assertIndexConsistency();
    });

    it("updater 返回同引用时不触发更新", () => {
      useChatStore.getState().addAssistantMessage("a1");
      const before = useChatStore.getState().messages;

      useChatStore.getState().updateAssistantMessage("a1", (m) => m);

      // zustand set({}) 应该不改变 messages 引用
      // 由于 _patchMessageById 返回 null → set({})，messages 引用不变
      const after = useChatStore.getState().messages;
      expect(after).toBe(before);
    });
  });

  describe("updateLastBlock / updateBlockByType", () => {
    it("增量追加文本时替换消息引用，供列表订阅刷新", () => {
      useChatStore.getState().addAssistantMessage("a1");
      useChatStore.getState().appendBlock("a1", { type: "text", content: "你" });
      const before = useChatStore.getState().messagesById.a1;

      useChatStore.getState().updateLastBlock("a1", (b) => (
        b.type === "text" ? { ...b, content: b.content + "好" } : b
      ));

      const after = useChatStore.getState().messagesById.a1;
      expect(after).not.toBe(before);
      expect(after).toBe(useChatStore.getState().messages[0]);
      expect(after.role).toBe("assistant");
      if (after.role === "assistant") {
        expect(after.blocks[0]).toEqual({ type: "text", content: "你好" });
      }
    });

    it("updateBlockByType 更新最后一个文本块而不是末尾的其他块", () => {
      useChatStore.getState().addAssistantMessage("a1");
      useChatStore.getState().appendBlock("a1", { type: "text", content: "你" });
      useChatStore.getState().appendBlock("a1", {
        type: "token_stats",
        promptTokens: 1,
        completionTokens: 1,
        totalTokens: 2,
        iterations: 1,
      });

      useChatStore.getState().updateBlockByType("a1", "text", (b) => (
        b.type === "text" ? { ...b, content: b.content + "好" } : b
      ));

      const msg = useChatStore.getState().messagesById.a1;
      expect(msg.role).toBe("assistant");
      if (msg.role === "assistant") {
        expect(msg.blocks[0]).toEqual({ type: "text", content: "你好" });
        expect(msg.blocks[1].type).toBe("token_stats");
      }
    });

    it("thinking 增量拼接保留空格", () => {
      useChatStore.getState().addAssistantMessage("a1");
      useChatStore.getState().appendBlock("a1", {
        type: "thinking",
        content: "Let me",
        startedAt: Date.now(),
      });

      useChatStore.getState().updateBlockByType("a1", "thinking", (b) => (
        b.type === "thinking" ? { ...b, content: b.content + " " } : b
      ));
      useChatStore.getState().updateBlockByType("a1", "thinking", (b) => (
        b.type === "thinking" ? { ...b, content: b.content + "think" } : b
      ));

      const msg = useChatStore.getState().messagesById.a1;
      expect(msg.role).toBe("assistant");
      if (msg.role === "assistant") {
        expect(msg.blocks[0]).toMatchObject({ type: "thinking", content: "Let me think" });
      }
    });
  });

  // ── updateToolCallBlock ─────────────────────────────────────
  describe("updateToolCallBlock", () => {
    it("按 toolCallId 精确匹配更新", () => {
      useChatStore.getState().addAssistantMessage("a1");
      useChatStore.getState().appendBlock("a1", {
        type: "tool_call",
        toolCallId: "tc1",
        name: "inspect_spreadsheet",
        args: {},
        status: "running",
      });
      useChatStore.getState().appendBlock("a1", {
        type: "tool_call",
        toolCallId: "tc2",
        name: "edit_spreadsheet",
        args: {},
        status: "running",
      });

      useChatStore.getState().updateToolCallBlock("a1", "tc1", (b) => {
        if (b.type === "tool_call") return { ...b, status: "success" };
        return b;
      });

      const msg = useChatStore.getState().messagesById["a1"] as Extract<Message, { role: "assistant" }>;
      expect(msg.blocks[0]).toMatchObject({ type: "tool_call", status: "success" });
      expect(msg.blocks[1]).toMatchObject({ type: "tool_call", status: "running" }); // tc2 不受影响
    });
  });

  // ── stream state / resume ───────────────────────────────────
  describe("stream state management", () => {
    it("setStreamState 设置 activeStreamId 和 latestSeq", () => {
      useChatStore.getState().setStreamState("stream-abc", 5);
      const s = useChatStore.getState();
      expect(s.activeStreamId).toBe("stream-abc");
      expect(s.latestSeq).toBe(5);
    });

    it("setStreamState(null, 0) 清除流状态", () => {
      useChatStore.getState().setStreamState("stream-abc", 5);
      useChatStore.getState().setStreamState(null, 0);
      const s = useChatStore.getState();
      expect(s.activeStreamId).toBeNull();
      expect(s.latestSeq).toBe(0);
    });

    it("markResumeFailed / clearResumeFailed", () => {
      useChatStore.getState().markResumeFailed("gap_detected");
      expect(useChatStore.getState().resumeFailedReason).toBe("gap_detected");

      useChatStore.getState().clearResumeFailed();
      expect(useChatStore.getState().resumeFailedReason).toBeNull();
    });
  });

  // ── upsertBlockByType ───────────────────────────────────────
  describe("upsertBlockByType", () => {
    it("不存在时追加新 block", () => {
      useChatStore.getState().addAssistantMessage("a1");
      useChatStore.getState().upsertBlockByType("a1", "llm_retry", {
        type: "llm_retry",
        retryAttempt: 1,
        retryMaxAttempts: 3,
        retryDelaySeconds: 5,
        retryErrorMessage: "err",
        retryStatus: "retrying",
      });

      const msg = useChatStore.getState().messagesById["a1"] as Extract<Message, { role: "assistant" }>;
      expect(msg.blocks.length).toBe(1);
      expect(msg.blocks[0].type).toBe("llm_retry");
    });

    it("已存在时替换最后一个同类型 block", () => {
      useChatStore.getState().addAssistantMessage("a1");
      useChatStore.getState().upsertBlockByType("a1", "llm_retry", {
        type: "llm_retry",
        retryAttempt: 1,
        retryMaxAttempts: 3,
        retryDelaySeconds: 5,
        retryErrorMessage: "err1",
        retryStatus: "retrying",
      });
      useChatStore.getState().upsertBlockByType("a1", "llm_retry", {
        type: "llm_retry",
        retryAttempt: 2,
        retryMaxAttempts: 3,
        retryDelaySeconds: 10,
        retryErrorMessage: "err2",
        retryStatus: "retrying",
      });

      const msg = useChatStore.getState().messagesById["a1"] as Extract<Message, { role: "assistant" }>;
      // 仍然只有 1 个 block（被替换而非追加）
      expect(msg.blocks.length).toBe(1);
      expect(msg.blocks[0]).toMatchObject({ type: "llm_retry", retryAttempt: 2 });
    });
  });

  // ── addAffectedFiles ────────────────────────────────────────
  describe("addAffectedFiles", () => {
    it("添加受影响文件到 assistant 消息", () => {
      useChatStore.getState().addAssistantMessage("a1");
      useChatStore.getState().addAffectedFiles("a1", ["/workspace/data.xlsx"]);

      const msg = useChatStore.getState().messagesById["a1"] as Extract<Message, { role: "assistant" }>;
      expect(msg.affectedFiles).toEqual(["./data.xlsx"]);
    });

    it("重复文件按 identity 去重", () => {
      useChatStore.getState().addAssistantMessage("a1");
      useChatStore.getState().addAffectedFiles("a1", ["./a.xlsx", "b.xlsx"]);
      useChatStore.getState().addAffectedFiles("a1", ["a.xlsx", "./c.xlsx"]);

      const msg = useChatStore.getState().messagesById["a1"] as Extract<Message, { role: "assistant" }>;
      expect(msg.affectedFiles).toEqual(["./a.xlsx", "./b.xlsx", "./c.xlsx"]);
    });

    it("丢弃 outputs/backups 时间戳副本", () => {
      useChatStore.getState().addAssistantMessage("a1");
      useChatStore.getState().addAffectedFiles("a1", [
        "./sales.xlsx",
        "outputs/backups/sales_20260911T091344_f525.xlsx",
      ]);

      const msg = useChatStore.getState().messagesById["a1"] as Extract<Message, { role: "assistant" }>;
      expect(msg.affectedFiles).toEqual(["./sales.xlsx"]);
    });
  });

  // ── retractLastThinking ─────────────────────────────────────
  describe("retractLastThinking", () => {
    it("移除最后一个未完成的 thinking block", () => {
      useChatStore.getState().addAssistantMessage("a1");
      useChatStore.getState().appendBlock("a1", {
        type: "thinking",
        content: "hmm...",
        startedAt: Date.now(),
      });

      useChatStore.getState().retractLastThinking("a1");
      const msg = useChatStore.getState().messagesById["a1"] as Extract<Message, { role: "assistant" }>;
      expect(msg.blocks.length).toBe(0);
    });

    it("已有 duration 的 thinking block 不被移除", () => {
      useChatStore.getState().addAssistantMessage("a1");
      useChatStore.getState().appendBlock("a1", {
        type: "thinking",
        content: "done",
        duration: 1.5,
        startedAt: Date.now(),
      });

      useChatStore.getState().retractLastThinking("a1");
      const msg = useChatStore.getState().messagesById["a1"] as Extract<Message, { role: "assistant" }>;
      expect(msg.blocks.length).toBe(1); // 未被移除
    });
  });
});
