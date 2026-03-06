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
      fetchBackups: vi.fn(),
    }),
  },
}));

// mock session-title
vi.mock("@/lib/session-title", () => ({
  deriveSessionTitleFromMessages: vi.fn(),
  isFallbackSessionTitle: vi.fn().mockReturnValue(true),
}));

import { useChatStore } from "@/stores/chat-store";
import type { Message } from "@/lib/types";

// ---------------------------------------------------------------------------
// helpers
// ---------------------------------------------------------------------------

function resetStore() {
  useChatStore.setState({
    messages: [],
    messageOrder: [],
    messagesById: {},
    messageIndexById: {},
    currentSessionId: null,
    activeStreamId: null,
    latestSeq: 0,
    resumeFailedReason: null,
    isStreaming: false,
    pendingApproval: null,
    _lastDismissedApprovalId: null,
    pendingQuestion: null,
    abortController: null,
    pipelineStatus: null,
    vlmPhases: [],
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

function makeAssistantMsg(id: string, blocks: Message extends { blocks: infer B } ? B : never = []): Message {
  return { id, role: "assistant", blocks: blocks as any, timestamp: Date.now() };
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("chat-store", () => {
  beforeEach(() => {
    resetStore();
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
      expect((s.messagesById["dup"] as any).content).toBe("second");
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
      expect((s.messagesById["u1"] as any).content).toBe("hello");
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
      expect((msg as any).files?.length).toBe(1);
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
      expect((msg as any).blocks).toEqual([]);
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
      expect((msg as any).blocks.length).toBe(1);
      expect((msg as any).blocks[0]).toEqual({ type: "text", content: "hello" });
    });

    it("连续追加多个 block", () => {
      useChatStore.getState().addAssistantMessage("a1");
      useChatStore.getState().appendBlock("a1", { type: "text", content: "1" });
      useChatStore.getState().appendBlock("a1", { type: "text", content: "2" });
      useChatStore.getState().appendBlock("a1", {
        type: "tool_call",
        name: "read_excel",
        args: {},
        status: "running",
      });

      const msg = useChatStore.getState().messagesById["a1"] as any;
      expect(msg.blocks.length).toBe(3);
      expect(msg.blocks[2].type).toBe("tool_call");
    });

    it("对不存在的消息 ID appendBlock 不崩溃", () => {
      useChatStore.getState().addAssistantMessage("a1");
      // 不应抛错
      useChatStore.getState().appendBlock("nonexistent", { type: "text", content: "x" });
      // a1 不受影响
      expect((useChatStore.getState().messagesById["a1"] as any).blocks.length).toBe(0);
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
      expect((s.messages[idx] as any).blocks[0].content).toBe("reply");
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

      const msg = useChatStore.getState().messagesById["a1"] as any;
      expect(msg.blocks[0].content).toBe("new");
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

  // ── updateToolCallBlock ─────────────────────────────────────
  describe("updateToolCallBlock", () => {
    it("按 toolCallId 精确匹配更新", () => {
      useChatStore.getState().addAssistantMessage("a1");
      useChatStore.getState().appendBlock("a1", {
        type: "tool_call",
        toolCallId: "tc1",
        name: "read_excel",
        args: {},
        status: "running",
      });
      useChatStore.getState().appendBlock("a1", {
        type: "tool_call",
        toolCallId: "tc2",
        name: "write_cells",
        args: {},
        status: "running",
      });

      useChatStore.getState().updateToolCallBlock("a1", "tc1", (b) => {
        if (b.type === "tool_call") return { ...b, status: "success" };
        return b;
      });

      const msg = useChatStore.getState().messagesById["a1"] as any;
      expect(msg.blocks[0].status).toBe("success");
      expect(msg.blocks[1].status).toBe("running"); // tc2 不受影响
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

      const msg = useChatStore.getState().messagesById["a1"] as any;
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

      const msg = useChatStore.getState().messagesById["a1"] as any;
      // 仍然只有 1 个 block（被替换而非追加）
      expect(msg.blocks.length).toBe(1);
      expect(msg.blocks[0].retryAttempt).toBe(2);
    });
  });

  // ── addAffectedFiles ────────────────────────────────────────
  describe("addAffectedFiles", () => {
    it("添加受影响文件到 assistant 消息", () => {
      useChatStore.getState().addAssistantMessage("a1");
      useChatStore.getState().addAffectedFiles("a1", ["/workspace/data.xlsx"]);

      const msg = useChatStore.getState().messagesById["a1"] as any;
      expect(msg.affectedFiles).toEqual(["/workspace/data.xlsx"]);
    });

    it("重复文件不重复添加", () => {
      useChatStore.getState().addAssistantMessage("a1");
      useChatStore.getState().addAffectedFiles("a1", ["/a.xlsx", "/b.xlsx"]);
      useChatStore.getState().addAffectedFiles("a1", ["/a.xlsx", "/c.xlsx"]);

      const msg = useChatStore.getState().messagesById["a1"] as any;
      expect(msg.affectedFiles.length).toBe(3);
      expect(new Set(msg.affectedFiles)).toEqual(new Set(["/a.xlsx", "/b.xlsx", "/c.xlsx"]));
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
      const msg = useChatStore.getState().messagesById["a1"] as any;
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
      const msg = useChatStore.getState().messagesById["a1"] as any;
      expect(msg.blocks.length).toBe(1); // 未被移除
    });
  });
});
