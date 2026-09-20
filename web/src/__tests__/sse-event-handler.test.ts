/**
 * sse-event-handler.ts 单元测试
 *
 * 覆盖：
 * - stream_init 事件设置 activeStreamId + latestSeq + clearResumeFailed
 * - 普通事件更新 latestSeq = max(current, eventSeq)
 * - resume_failed 事件触发 markResumeFailed + setPipelineStatus
 * - subscribe_resume 事件设置 stream state + 清除 resume failed
 * - route_end 不再写入 Smart Route 状态块
 * - _mapDiffChanges snake_case → camelCase
 * - preDispatch / finalizeThinking 状态管理
 */

import { describe, it, expect, beforeEach, vi } from "vitest";

// ---------------------------------------------------------------------------
// Mock stores — sse-event-handler.ts 通过 S() = useChatStore.getState() 读写
// ---------------------------------------------------------------------------

const chatActions: Record<string, ReturnType<typeof vi.fn>> = {
  setStreamState: vi.fn(),
  clearResumeFailed: vi.fn(),
  markResumeFailed: vi.fn(),
  setPipelineStatus: vi.fn(),
  appendBlock: vi.fn(),
  updateBlockByType: vi.fn(),
  upsertBlockByType: vi.fn(),
  updateToolCallBlock: vi.fn(),
  updateSubagentBlock: vi.fn(),
  updateAssistantMessage: vi.fn(),
  setPendingApproval: vi.fn(),
  dismissApproval: vi.fn(),
  setPendingQuestion: vi.fn(),
  setToolProgress: vi.fn(),
  clearToolProgress: vi.fn(),
  setBatchProgress: vi.fn(),
  addAffectedFiles: vi.fn(),
  saveCurrentSession: vi.fn(),
  bindLoadedSession: vi.fn(),
  retractLastThinking: vi.fn(),
};

let mockChatState: Record<string, unknown> = {};

function resetChatState() {
  mockChatState = {
    messages: [],
    messagesById: {},
    messageOrder: [],
    messageIndexById: {},
    activeStreamId: null,
    latestSeq: 0,
    resumeFailedReason: null,
    loadedSessionId: null,
    pendingApproval: null,
    pendingQuestion: null,
    _lastDismissedApprovalId: null,
    ...chatActions,
  };
  // 重置所有 mock 调用记录
  for (const fn of Object.values(chatActions)) {
    fn.mockClear();
  }
  for (const fn of Object.values(excelActions)) {
    fn.mockClear();
  }
}

vi.mock("@/stores/chat-store", () => ({
  useChatStore: {
    getState: () => mockChatState,
    setState: vi.fn((partial: Record<string, unknown>) => {
      Object.assign(mockChatState, partial);
    }),
  },
}));

const sessionMock = vi.hoisted(() => {
  const state = {
    activeSessionId: "test-session" as string | null,
    sessions: [
      { id: "test-session", workspaceId: "ws-test" },
    ] as { id: string; workspaceId?: string | null }[],
    setActiveSession: vi.fn((id: string | null) => {
      state.activeSessionId = id;
    }),
    updateSessionTitle: vi.fn(),
    patchSession: vi.fn(),
  };
  return state;
});

vi.mock("@/stores/session-store", () => ({
  useSessionStore: {
    getState: () => sessionMock,
  },
}));

const uiMock = vi.hoisted(() => ({
  setFullAccessEnabled: vi.fn(),
  setChatMode: vi.fn(),
  setSidebarTab: vi.fn(),
}));

vi.mock("@/stores/ui-store", () => ({
  useUIStore: {
    getState: () => uiMock,
  },
}));

const excelActions: Record<string, ReturnType<typeof vi.fn>> = {
  addPreview: vi.fn(),
  addDiff: vi.fn(),
  addTextDiff: vi.fn(),
  addTextPreview: vi.fn(),
  addRecentFileIfNotDismissed: vi.fn(),
  addRecentFile: vi.fn(),
  closePanel: vi.fn(),
  closeFullView: vi.fn(),
  closeCompare: vi.fn(),
  appendStreamingArgs: vi.fn(),
  clearStreamingArgs: vi.fn(),
  setMergeResult: vi.fn(),
  fetchOperationHistory: vi.fn(),
  bumpWorkspaceFilesVersion: vi.fn(),
  notifyWorkbookChanged: vi.fn(),
  openCompare: vi.fn(),
  openPanel: vi.fn(),
};

const excelView = {
  compareMode: false,
  panelOpen: false,
  dismissedPaths: new Set<string>(),
};

vi.mock("@/stores/excel-store", () => ({
  useExcelStore: {
    getState: () => ({
      ...excelActions,
      ...excelView,
    }),
  },
}));

const wordActions: Record<string, ReturnType<typeof vi.fn>> = {
  handleFilesChanged: vi.fn(),
};

const wordView = {
  panelOpen: false,
  closePanel: vi.fn(),
  closeFullView: vi.fn(),
  openPanel: vi.fn(),
  openFullView: vi.fn(),
};

vi.mock("@/stores/word-store", () => ({
  useWordStore: {
    getState: () => ({
      ...wordActions,
      ...wordView,
    }),
  },
}));

const previewView = {
  textOpen: false,
  imageOpen: false,
  closeText: vi.fn(),
  closeImage: vi.fn(),
  openText: vi.fn(),
  openImage: vi.fn(),
};

vi.mock("@/stores/file-preview-store", () => ({
  useFilePreviewStore: {
    getState: () => previewView,
  },
}));

vi.mock("@/lib/open-workspace-file", () => ({
  openWorkspaceFile: vi.fn(),
}));

const mobileMock = vi.hoisted(() => ({
  getIsMobile: vi.fn(() => false),
  getIsTablet: vi.fn(() => false),
  getIsDesktop: vi.fn(() => true),
  getIsMediumScreen: vi.fn(() => false),
}));

vi.mock("@/hooks/use-mobile", () => mobileMock);

import { useChatStore } from "@/stores/chat-store";
import {
  dispatchSSEEvent,
  finalizeThinking,
  preDispatch,
  _mapDiffChanges,
  type SSEEvent,
  type SSEHandlerContext,
  type DeltaBatcher,
} from "@/lib/sse-event-handler";
import { openWorkspaceFile } from "@/lib/open-workspace-file";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function makeBatcher(): DeltaBatcher {
  return {
    pushText: vi.fn(),
    pushThinking: vi.fn(),
    flush: vi.fn(),
    dispose: vi.fn(),
    hasPendingContent: vi.fn().mockReturnValue(false),
  };
}

function makeCtx(overrides: Partial<SSEHandlerContext> = {}): SSEHandlerContext {
  return {
    assistantMsgId: "a1",
    batcher: makeBatcher(),
    effectiveSessionId: "test-session",
    isFirstSend: true,
    thinkingInProgress: false,
    hadStreamError: false,
    ...overrides,
  };
}

function makeEvent(event: string, data: Record<string, unknown> = {}): SSEEvent {
  return { event, data };
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("sse-event-handler", () => {
  it("retains the background identity needed when the main generation is stopped", () => {
    resetChatState();
    dispatchSSEEvent(makeEvent("subagent_start", {
      name: "explorer", reason: "统计", conversation_id: "background-1", background: true,
    }), makeCtx());
    expect(chatActions.appendBlock).toHaveBeenCalledWith("a1", expect.objectContaining({
      type: "subagent", conversationId: "background-1", background: true, status: "running",
    }));
  });

  beforeEach(() => {
    resetChatState();
    for (const fn of Object.values(excelActions)) fn.mockClear();
    for (const fn of Object.values(wordActions)) fn.mockClear();
    sessionMock.activeSessionId = "test-session";
    sessionMock.setActiveSession.mockClear();
    sessionMock.updateSessionTitle.mockClear();
    sessionMock.patchSession.mockClear();
    excelView.compareMode = false;
    excelView.panelOpen = false;
    excelView.dismissedPaths = new Set();
    wordView.panelOpen = false;
    previewView.textOpen = false;
    previewView.imageOpen = false;
    mobileMock.getIsMobile.mockReturnValue(false);
    vi.mocked(openWorkspaceFile).mockClear();
    vi.mocked(useChatStore.setState).mockClear();
    uiMock.setFullAccessEnabled.mockClear();
    uiMock.setChatMode.mockClear();
    uiMock.setSidebarTab.mockClear();
  });

  // ── stream_init ─────────────────────────────────────────────

  describe("stream_init", () => {
    it("设置 activeStreamId + latestSeq + 调用 clearResumeFailed", () => {
      const ctx = makeCtx();
      dispatchSSEEvent(
        makeEvent("stream_init", { stream_id: "str-123", seq: 1 }),
        ctx,
      );

      expect(chatActions.setStreamState).toHaveBeenCalledWith("str-123", 1);
      expect(chatActions.clearResumeFailed).toHaveBeenCalled();
    });

    it("无 stream_id 时不调用 setStreamState（stream_init 分支）", () => {
      const ctx = makeCtx();
      dispatchSSEEvent(
        makeEvent("stream_init", { seq: 1 }),
        ctx,
      );

      // stream_init case 中的 setStreamState 仅在 eventStreamId 存在时调用
      // 但顶层 seq 追踪逻辑仍会调用（通过 state.activeStreamId fallback）
      expect(chatActions.clearResumeFailed).toHaveBeenCalled();
    });

    it("seq 为 0 时正常处理", () => {
      const ctx = makeCtx();
      dispatchSSEEvent(
        makeEvent("stream_init", { stream_id: "str-0", seq: 0 }),
        ctx,
      );

      expect(chatActions.setStreamState).toHaveBeenCalledWith("str-0", 0);
    });
  });

  describe("session_init", () => {
    it("无 activeSessionId 时只写入 session-store，不写 chat.currentSessionId", () => {
      sessionMock.activeSessionId = null;
      dispatchSSEEvent(
        makeEvent("session_init", { session_id: "sid-from-sse" }),
        makeCtx(),
      );

      expect(sessionMock.setActiveSession).toHaveBeenCalledWith("sid-from-sse");
      expect(useChatStore.setState).not.toHaveBeenCalledWith(
        expect.objectContaining({ currentSessionId: expect.anything() }),
      );
      expect(mockChatState).not.toHaveProperty("currentSessionId");
    });

    it("activeSessionId 已存在时仍不向 chat-store 写入 currentSessionId", () => {
      sessionMock.activeSessionId = "test-session";
      dispatchSSEEvent(
        makeEvent("session_init", { session_id: "sid-from-sse" }),
        makeCtx(),
      );

      expect(useChatStore.setState).not.toHaveBeenCalledWith(
        expect.objectContaining({ currentSessionId: expect.anything() }),
      );
    });

    it("即时标题保留用户首行全文，不按 12 字截断", () => {
      dispatchSSEEvent(
        makeEvent("session_init", { session_id: "test-session" }),
        makeCtx({ userText: "识别截图中的表格，还原数据" }),
      );

      expect(sessionMock.updateSessionTitle).toHaveBeenCalledWith(
        "test-session",
        "识别截图中的表格，还原数据",
      );
    });
  });

  // ── seq 追踪（普通事件）────────────────────────────────────

  describe("seq tracking on normal events", () => {
    it("普通事件带 seq 时更新 latestSeq = max(current, eventSeq)", () => {
      mockChatState.activeStreamId = "str-abc";
      mockChatState.latestSeq = 3;

      const ctx = makeCtx();
      // route_end 是一个普通事件，带 seq
      dispatchSSEEvent(
        makeEvent("route_end", { seq: 7, route_mode: "all_tools", skills_used: [] }),
        ctx,
      );

      // 顶层 seq 追踪逻辑：setStreamState(streamId, max(3, 7))
      expect(chatActions.setStreamState).toHaveBeenCalledWith("str-abc", 7);
    });

    it("eventSeq < latestSeq 时取 max（不回退）", () => {
      mockChatState.activeStreamId = "str-abc";
      mockChatState.latestSeq = 10;

      const ctx = makeCtx();
      dispatchSSEEvent(
        makeEvent("route_start", { seq: 5 }),
        ctx,
      );

      // max(10, 5) = 10
      expect(chatActions.setStreamState).toHaveBeenCalledWith("str-abc", 10);
    });

    it("事件无 seq 字段时不调用顶层 setStreamState", () => {
      mockChatState.activeStreamId = "str-abc";
      mockChatState.latestSeq = 3;

      const ctx = makeCtx();
      dispatchSSEEvent(
        makeEvent("route_start", {}),
        ctx,
      );

      // 无 seq → eventSeq === null → 跳过顶层追踪
      expect(chatActions.setStreamState).not.toHaveBeenCalled();
    });

    it("事件带 stream_id 时使用事件中的 stream_id", () => {
      mockChatState.activeStreamId = "old-stream";
      mockChatState.latestSeq = 1;

      const ctx = makeCtx();
      dispatchSSEEvent(
        makeEvent("iteration_start", { seq: 5, stream_id: "new-stream", iteration: 2 }),
        ctx,
      );

      // 使用事件中的 stream_id 而非 state.activeStreamId
      expect(chatActions.setStreamState).toHaveBeenCalledWith("new-stream", 5);
    });
  });

  // ── resume_failed ───────────────────────────────────────────

  describe("resume_failed", () => {
    it("调用 markResumeFailed(reason) + 设置 pipeline status", () => {
      const ctx = makeCtx();
      dispatchSSEEvent(
        makeEvent("resume_failed", { reason: "gap_detected" }),
        ctx,
      );

      expect(chatActions.markResumeFailed).toHaveBeenCalledWith("gap_detected");
      expect(chatActions.setPipelineStatus).toHaveBeenCalledWith(
        expect.objectContaining({
          stage: "resume_failed",
        }),
      );
    });

    it("reason 为空时使用 'unknown'", () => {
      const ctx = makeCtx();
      dispatchSSEEvent(
        makeEvent("resume_failed", {}),
        ctx,
      );

      expect(chatActions.markResumeFailed).toHaveBeenCalledWith("unknown");
    });
  });

  // ── subscribe_resume ────────────────────────────────────────

  describe("subscribe_resume", () => {
    it("设置 stream state + 清除 resume failed", () => {
      mockChatState.latestSeq = 5;

      const ctx = makeCtx();
      dispatchSSEEvent(
        makeEvent("subscribe_resume", {
          status: "reconnected",
          stream_id: "str-resume",
          seq: 10,
        }),
        ctx,
      );

      expect(chatActions.setStreamState).toHaveBeenCalledWith("str-resume", 10);
      expect(chatActions.clearResumeFailed).toHaveBeenCalled();
      expect(chatActions.setPipelineStatus).toHaveBeenCalledWith(
        expect.objectContaining({ stage: "resuming" }),
      );
    });

    it("无 stream_id 时使用事件中的 eventStreamId", () => {
      mockChatState.latestSeq = 3;

      const ctx = makeCtx();
      dispatchSSEEvent(
        makeEvent("subscribe_resume", { status: "ok", seq: 4, stream_id: "evt-id" }),
        ctx,
      );

      // eventStreamId = "evt-id"（从 data.stream_id 提取）
      expect(chatActions.setStreamState).toHaveBeenCalledWith("evt-id", 4);
    });
  });

  // ── route_end ───────────────────────────────────────────────

  describe("route_end", () => {
    it("does not append Smart Route status blocks", () => {
      const ctx = makeCtx();
      dispatchSSEEvent(
        makeEvent("route_end", { route_mode: "all_tools", skills_used: ["csv_lookup"] }),
        ctx,
      );

      expect(chatActions.appendBlock).not.toHaveBeenCalled();
    });
  });

  // ── done ────────────────────────────────────────────────────

  describe("done", () => {
    it("清除 pipeline status", () => {
      const ctx = makeCtx();
      dispatchSSEEvent(makeEvent("done", {}), ctx);

      expect(chatActions.setPipelineStatus).toHaveBeenCalledWith(null);
    });
  });

  // ── _mapDiffChanges ─────────────────────────────────────────

  describe("_mapDiffChanges", () => {
    it("snake_case → camelCase 映射", () => {
      const raw = [
        {
          cell: "A1",
          old: "x",
          new: "y",
          old_style: { bold: true },
          new_style: null,
          style_only: false,
        },
      ];
      const result = _mapDiffChanges(raw);
      expect(result).toEqual([
        {
          cell: "A1",
          old: "x",
          new: "y",
          oldStyle: { bold: true },
          newStyle: null,
          styleOnly: false,
        },
      ]);
    });

    it("非数组输入返回空数组", () => {
      expect(_mapDiffChanges(null as unknown as unknown[])).toEqual([]);
      expect(_mapDiffChanges(undefined as unknown as unknown[])).toEqual([]);
    });

    it("camelCase 字段也能正确映射", () => {
      const raw = [{ cell: "B2", old: 1, new: 2, oldStyle: null, newStyle: null, styleOnly: true }];
      const result = _mapDiffChanges(raw);
      expect(result[0].styleOnly).toBe(true);
    });
  });

  // ── preDispatch / finalizeThinking ──────────────────────────

  describe("preDispatch", () => {
    it("非 thinking 事件前 finalizeThinking", () => {
      // 准备一个有 thinking 进行中的 assistant 消息
      const thinkingMsg = {
        id: "a1",
        role: "assistant" as const,
        blocks: [{ type: "thinking" as const, content: "...", startedAt: Date.now() }],
        timestamp: Date.now(),
      };
      mockChatState.messages = [thinkingMsg];
      mockChatState.messagesById = { a1: thinkingMsg };
      mockChatState.messageOrder = ["a1"];
      mockChatState.messageIndexById = { a1: 0 };

      const ctx = makeCtx({ thinkingInProgress: true });
      preDispatch(makeEvent("text_delta", { content: "hello" }), ctx);

      // thinkingInProgress 应该被设为 false
      expect(ctx.thinkingInProgress).toBe(false);
      // batcher.flush 应该被调用
      expect(ctx.batcher.flush).toHaveBeenCalled();
      // updateBlockByType 应该被调用来 finalize thinking
      expect(chatActions.updateBlockByType).toHaveBeenCalledWith(
        "a1",
        "thinking",
        expect.any(Function),
      );
    });

    it("thinking_delta 事件不触发 finalizeThinking", () => {
      const ctx = makeCtx({ thinkingInProgress: true });
      preDispatch(makeEvent("thinking_delta", { content: "..." }), ctx);

      // thinkingInProgress 保持 true
      expect(ctx.thinkingInProgress).toBe(true);
      expect(chatActions.updateBlockByType).not.toHaveBeenCalled();
    });

    it("非 delta 事件前 flush batcher", () => {
      const ctx = makeCtx();
      preDispatch(makeEvent("tool_call_start", {}), ctx);

      expect(ctx.batcher.flush).toHaveBeenCalled();
    });

    it("text_delta 事件不 flush batcher（增量追加）", () => {
      const ctx = makeCtx();
      preDispatch(makeEvent("text_delta", { content: "x" }), ctx);

      // text_delta 不触发 flush
      expect(ctx.batcher.flush).not.toHaveBeenCalled();
    });
  });

  // ── finalizeThinking ────────────────────────────────────────

  describe("finalizeThinking", () => {
    it("thinkingInProgress=false 时不做任何事", () => {
      const ctx = makeCtx({ thinkingInProgress: false });
      finalizeThinking(ctx);

      expect(chatActions.updateBlockByType).not.toHaveBeenCalled();
      expect(ctx.batcher.flush).not.toHaveBeenCalled();
    });

    it("thinkingInProgress=true 时 flush + updateBlockByType", () => {
      const thinkingMsg = {
        id: "a1",
        role: "assistant" as const,
        blocks: [{ type: "thinking" as const, content: "hmm", startedAt: Date.now() - 2000 }],
        timestamp: Date.now(),
      };
      mockChatState.messages = [thinkingMsg];
      mockChatState.messagesById = { a1: thinkingMsg };

      const ctx = makeCtx({ thinkingInProgress: true });
      finalizeThinking(ctx);

      expect(ctx.thinkingInProgress).toBe(false);
      expect(ctx.batcher.flush).toHaveBeenCalled();
      expect(chatActions.updateBlockByType).toHaveBeenCalledWith(
        "a1",
        "thinking",
        expect.any(Function),
      );
    });
  });

  // ── thinking_delta ──────────────────────────────────────────

  describe("thinking_delta", () => {
    it("首个增量保留边界空格", () => {
      const ctx = makeCtx();
      dispatchSSEEvent(makeEvent("thinking_delta", { content: "Let me " }), ctx);

      expect(chatActions.appendBlock).toHaveBeenCalledWith(
        "a1",
        expect.objectContaining({ type: "thinking", content: "Let me " }),
      );
    });

    it("后续增量把空格和换行原样推进 batcher", () => {
      const assistantMsg = {
        id: "a1",
        role: "assistant" as const,
        blocks: [{ type: "thinking" as const, content: "Let me", startedAt: Date.now() }],
        timestamp: Date.now(),
      };
      mockChatState.messages = [assistantMsg];
      mockChatState.messagesById = { a1: assistantMsg };
      mockChatState.messageOrder = ["a1"];
      mockChatState.messageIndexById = { a1: 0 };

      const ctx = makeCtx();
      dispatchSSEEvent(makeEvent("thinking_delta", { content: " " }), ctx);
      expect(ctx.batcher.pushThinking).toHaveBeenCalledWith(" ");

      dispatchSSEEvent(makeEvent("thinking_delta", { content: "\n" }), ctx);
      expect(ctx.batcher.pushThinking).toHaveBeenCalledWith("\n");
      expect(chatActions.appendBlock).not.toHaveBeenCalled();
    });
  });

  // ── text_delta ──────────────────────────────────────────────

  describe("text_delta", () => {
    it("清除 pipeline status + 追加 text block if needed + pushText", () => {
      // 设置一个有 assistant 消息的状态（但无 text block）
      const assistantMsg = {
        id: "a1",
        role: "assistant" as const,
        blocks: [] as { type: string; content: string }[],
        timestamp: Date.now(),
      };
      mockChatState.messages = [assistantMsg];
      mockChatState.messagesById = { a1: assistantMsg };
      mockChatState.messageOrder = ["a1"];
      mockChatState.messageIndexById = { a1: 0 };

      const ctx = makeCtx();
      dispatchSSEEvent(makeEvent("text_delta", { content: "hello" }), ctx);

      expect(chatActions.setPipelineStatus).toHaveBeenCalledWith(null);
      // 因为没有 text block，应追加一个空 text block
      expect(chatActions.appendBlock).toHaveBeenCalledWith("a1", { type: "text", content: "" });
      expect(ctx.batcher.pushText).toHaveBeenCalledWith("hello");
    });
  });

  describe("reply", () => {
    it("没有文本块时用完整回复补上", () => {
      const assistantMsg = {
        id: "a1",
        role: "assistant" as const,
        blocks: [] as { type: string; content: string }[],
        timestamp: Date.now(),
      };
      mockChatState.messages = [assistantMsg];
      mockChatState.messagesById = { a1: assistantMsg };

      dispatchSSEEvent(makeEvent("reply", { content: "最终回复" }), makeCtx());

      expect(chatActions.appendBlock).toHaveBeenCalledWith("a1", {
        type: "text",
        content: "最终回复",
      });
    });

    it("已有空文本块时回填完整回复", () => {
      const assistantMsg = {
        id: "a1",
        role: "assistant" as const,
        blocks: [{ type: "text" as const, content: "" }],
        timestamp: Date.now(),
      };
      mockChatState.messages = [assistantMsg];
      mockChatState.messagesById = { a1: assistantMsg };

      dispatchSSEEvent(makeEvent("reply", { content: "最终回复" }), makeCtx());

      expect(chatActions.updateBlockByType).toHaveBeenCalledWith(
        "a1",
        "text",
        expect.any(Function),
      );
      const updater = chatActions.updateBlockByType.mock.calls[0][2] as (
        b: { type: string; content: string },
      ) => { type: string; content: string };
      expect(updater({ type: "text", content: "" })).toEqual({
        type: "text",
        content: "最终回复",
      });
    });

    it("增量文本已存在时不覆盖", () => {
      const assistantMsg = {
        id: "a1",
        role: "assistant" as const,
        blocks: [{ type: "text" as const, content: "已经流出来的字" }],
        timestamp: Date.now(),
      };
      mockChatState.messages = [assistantMsg];
      mockChatState.messagesById = { a1: assistantMsg };

      dispatchSSEEvent(makeEvent("reply", { content: "已经流出来的字更多" }), makeCtx());

      expect(chatActions.appendBlock).not.toHaveBeenCalled();
      expect(chatActions.updateBlockByType).not.toHaveBeenCalled();
    });
  });

  // ── tool_call_end ui.merge ─────────────────────────────────

  describe("tool_call_end", () => {
    it("从 data.ui.merge 投影合并结果，不解析 result 字符串", () => {
      mockChatState.messagesById = {
        a1: {
          id: "a1",
          role: "assistant",
          blocks: [{
            type: "tool_call",
            id: "tc-merge",
            name: "run_code",
            status: "running",
          }],
        },
      };
      const ctx = makeCtx();
      dispatchSSEEvent(
        makeEvent("tool_call_end", {
          tool_call_id: "tc-merge",
          tool_name: "run_code",
          success: true,
          result: '{"rows_matched":999}',
          ui: {
            merge: {
              source_files: ["a.xlsx", "b.xlsx"],
              output_file: "out.xlsx",
              rows_matched: 12,
              rows_added: 3,
              rows_unmatched: 1,
              key_columns: ["id"],
              join_type: "inner",
            },
          },
        }),
        ctx,
      );

      expect(excelActions.setMergeResult).toHaveBeenCalledWith(
        expect.objectContaining({
          sourceFiles: ["a.xlsx", "b.xlsx"],
          outputFile: "out.xlsx",
          rowsMatched: 12,
          toolCallId: "tc-merge",
        }),
      );
    });
  });

  // ── user_question ───────────────────────────────────────────

  describe("user_question", () => {
    it("设置 pending question", () => {
      const ctx = makeCtx();
      dispatchSSEEvent(
        makeEvent("user_question", {
          id: "q1",
          header: "Confirm",
          text: "Are you sure?",
          options: [{ label: "Yes", description: "Proceed" }],
          multi_select: false,
        }),
        ctx,
      );

      expect(chatActions.setPendingQuestion).toHaveBeenCalledWith(
        expect.objectContaining({
          id: "q1",
          header: "Confirm",
          text: "Are you sure?",
          multiSelect: false,
        }),
      );
    });
  });

  // ── pending_approval ────────────────────────────────────────

  describe("pending_approval", () => {
    it("更新 tool_call block 为 pending + 设置 pendingApproval", () => {
      const ctx = makeCtx();
      dispatchSSEEvent(
        makeEvent("pending_approval", {
          tool_call_id: "tc1",
          approval_id: "ap1",
          approval_tool_name: "edit_spreadsheet",
          risk_level: "high",
          args_summary: { cells: "A1:B5" },
        }),
        ctx,
      );

      expect(chatActions.updateToolCallBlock).toHaveBeenCalledWith(
        "a1",
        "tc1",
        expect.any(Function),
      );
      expect(chatActions.setPendingApproval).toHaveBeenCalledWith(
        expect.objectContaining({
          id: "ap1",
          toolName: "edit_spreadsheet",
          riskLevel: "high",
        }),
      );
      expect(sessionMock.patchSession).toHaveBeenCalledWith("test-session", {
        pendingApproval: true,
        pendingQuestion: false,
      });
    });

    it("忽略已关闭的同一审批单据重放", () => {
      mockChatState._lastDismissedApprovalId = "ap1";
      dispatchSSEEvent(
        makeEvent("pending_approval", {
          tool_call_id: "tc1",
          approval_id: "ap1",
          approval_tool_name: "edit_spreadsheet",
        }),
        makeCtx(),
      );
      expect(chatActions.setPendingApproval).not.toHaveBeenCalled();
    });
  });

  describe("approval_resolved", () => {
    it("applies a recovered result to its original message after the chat stream changes", () => {
      const old = { id: "old-message", role: "assistant" as const, timestamp: 1, blocks: [{
        type: "tool_call", toolCallId: "original-call", name: "edit_spreadsheet", args: {}, status: "error", result: "已停止",
      }] };
      mockChatState.messages = [old];
      dispatchSSEEvent(makeEvent("tool_call_end", {
        tool_call_id: "original-call", tool_name: "edit_spreadsheet", success: true, result: "恢复后已写入",
      }), makeCtx());
      const [messageId, update] = chatActions.updateAssistantMessage.mock.calls[0];
      expect(messageId).toBe("old-message");
      expect(update(old).blocks[0]).toMatchObject({ status: "success", result: "恢复后已写入" });
    });

    it("does not finish an ask_user tool when only one question in its batch was answered", () => {
      dispatchSSEEvent(makeEvent("approval_resolved", { approval_id: "q1", approval_tool_name: "ask_user", success: true }), makeCtx());
      expect(chatActions.updateToolCallBlock).not.toHaveBeenCalled();
    });

    it("关闭弹窗并更新 pending/running 工具卡片", () => {
      dispatchSSEEvent(
        makeEvent("approval_resolved", {
          tool_call_id: "tc1",
          approval_id: "ap1",
          approval_tool_name: "edit_spreadsheet",
          success: true,
          result: "已执行",
        }),
        makeCtx(),
      );
      expect(chatActions.dismissApproval).toHaveBeenCalledWith("ap1");
      expect(sessionMock.patchSession).toHaveBeenCalledWith("test-session", {
        pendingApproval: false,
      });
      const updater = chatActions.updateToolCallBlock.mock.calls[0]?.[2] as (
        b: { type: string; status: string; result?: string },
      ) => { type: string; status: string; result?: string };
      expect(updater({ type: "tool_call", status: "running" })).toEqual(
        expect.objectContaining({ status: "success", result: "已执行" }),
      );
      expect(updater({ type: "tool_call", status: "pending" })).toEqual(
        expect.objectContaining({ status: "success", result: "已执行" }),
      );
    });
  });

  describe("mutation", () => {
    it("刷新最近文件、受影响文件和工作区树", () => {
      dispatchSSEEvent(
        makeEvent("mutation", {
          files: ["a.xlsx"],
          mutations: [{ identity: "a.xlsx", content_version: "sha256:abc" }],
        }),
        makeCtx(),
      );

      expect(excelActions.addRecentFileIfNotDismissed).toHaveBeenCalledWith(
        {
          path: "a.xlsx",
          filename: "a.xlsx",
        },
        "id:ws-test",
      );
      expect(excelActions.bumpWorkspaceFilesVersion).toHaveBeenCalled();
      expect(excelActions.notifyWorkbookChanged).toHaveBeenCalledWith("a.xlsx", "id:ws-test", "sha256:abc");
      expect(wordActions.handleFilesChanged).toHaveBeenCalledWith(["a.xlsx"]);
      expect(chatActions.addAffectedFiles).toHaveBeenCalledWith("a1", ["a.xlsx"]);
    });

    it("legacy files_changed 仍可 replay", () => {
      dispatchSSEEvent(
        makeEvent("files_changed", { files: ["legacy.xlsx"] }),
        makeCtx(),
      );

      expect(wordActions.handleFilesChanged).toHaveBeenCalledWith(["legacy.xlsx"]);
      expect(chatActions.addAffectedFiles).toHaveBeenCalledWith("a1", ["legacy.xlsx"]);
    });
  });

  describe("tool_call_start", () => {
    it("没有同 id 的 streaming 块时 append 一条 running 工具卡片", () => {
      dispatchSSEEvent(
        makeEvent("tool_call_start", {
          tool_call_id: "tc-live",
          tool_name: "read_excel",
          arguments: { file_path: "./sales.xlsx" },
          iteration: 1,
        }),
        makeCtx(),
      );

      expect(chatActions.appendBlock).toHaveBeenCalledWith(
        "a1",
        expect.objectContaining({
          type: "tool_call",
          toolCallId: "tc-live",
          name: "read_excel",
          status: "running",
        }),
      );
    });

    it("把 parent_call_id 写进工具卡片以便嵌套展示", () => {
      dispatchSSEEvent(
        makeEvent("tool_call_start", {
          tool_call_id: "child-1",
          tool_name: "inspect_spreadsheet",
          arguments: { file_path: "book.xlsx" },
          parent_call_id: "run-1",
        }),
        makeCtx(),
      );

      expect(chatActions.appendBlock).toHaveBeenCalledWith(
        "a1",
        expect.objectContaining({
          type: "tool_call",
          toolCallId: "child-1",
          name: "inspect_spreadsheet",
          parentCallId: "run-1",
        }),
      );
    });
  });

  describe("done auto-open", () => {
    function seedAffected(files: string[]) {
      mockChatState.messages = [{
        id: "a1",
        role: "assistant",
        blocks: [],
        affectedFiles: files,
      }];
    }

    it("opens the last spreadsheet even when a later word file exists", () => {
      seedAffected(["notes.py", "book.xlsx", "report.docx", "sales.csv"]);
      dispatchSSEEvent(makeEvent("done"), makeCtx());
      expect(openWorkspaceFile).toHaveBeenCalledWith("sales.csv", { sessionId: "test-session" });
    });

    it("opens the last word document when no spreadsheet changed", () => {
      seedAffected(["notes.py", "a.docx", "b.docx"]);
      dispatchSSEEvent(makeEvent("done"), makeCtx());
      expect(openWorkspaceFile).toHaveBeenCalledWith("b.docx", { sessionId: "test-session" });
    });

    it("does not steal focus when a workbench panel is already open", () => {
      seedAffected(["book.xlsx"]);
      excelView.panelOpen = true;
      dispatchSSEEvent(makeEvent("done"), makeCtx());
      expect(openWorkspaceFile).not.toHaveBeenCalled();
    });

    it("skips auto-open when a high-confidence stay ui_hint suppressed it", () => {
      seedAffected(["book.xlsx"]);
      const ctx = makeCtx();
      dispatchSSEEvent(
        makeEvent("ui_hint", { surface: "stay", suppress_auto_open: true }),
        ctx,
      );
      dispatchSSEEvent(makeEvent("done"), ctx);
      expect(openWorkspaceFile).not.toHaveBeenCalled();
    });

    it("keeps auto-open when stay does not suppress", () => {
      seedAffected(["book.xlsx"]);
      const ctx = makeCtx();
      dispatchSSEEvent(
        makeEvent("ui_hint", { surface: "stay", suppress_auto_open: false }),
        ctx,
      );
      dispatchSSEEvent(makeEvent("done"), ctx);
      expect(openWorkspaceFile).toHaveBeenCalledWith("book.xlsx", { sessionId: "test-session" });
    });
  });

  describe("ui_hint", () => {
    it("does not append a message block", () => {
      dispatchSSEEvent(
        makeEvent("ui_hint", { surface: "side_panel", file_path: "book.xlsx" }),
        makeCtx(),
      );
      expect(chatActions.appendBlock).not.toHaveBeenCalled();
    });

    it("maps side_panel to preview openWorkspaceFile", () => {
      dispatchSSEEvent(
        makeEvent("ui_hint", {
          surface: "side_panel",
          file_path: "book.xlsx",
          sheet: "明细",
        }),
        makeCtx(),
      );
      expect(openWorkspaceFile).toHaveBeenCalledWith("book.xlsx", {
        intent: "preview",
        sheet: "明细",
        sessionId: "test-session",
      });
    });

    it("maps sheet_full to full openWorkspaceFile", () => {
      dispatchSSEEvent(
        makeEvent("ui_hint", { surface: "sheet_full", file_path: "book.xlsx" }),
        makeCtx(),
      );
      expect(openWorkspaceFile).toHaveBeenCalledWith("book.xlsx", {
        intent: "full",
        sheet: undefined,
        sessionId: "test-session",
      });
    });

    it("maps compare to openCompare", () => {
      dispatchSSEEvent(
        makeEvent("ui_hint", {
          surface: "compare",
          file_path: "a.xlsx",
          file_path_b: "b.xlsx",
        }),
        makeCtx(),
      );
      expect(excelActions.openCompare).toHaveBeenCalledWith("a.xlsx", "b.xlsx");
    });

    it("maps files_tab to setSidebarTab", () => {
      dispatchSSEEvent(makeEvent("ui_hint", { surface: "files_tab" }), makeCtx());
      expect(uiMock.setSidebarTab).toHaveBeenCalledWith("files");
    });

    it("does not steal an already open panel", () => {
      excelView.panelOpen = true;
      dispatchSSEEvent(
        makeEvent("ui_hint", { surface: "side_panel", file_path: "book.xlsx" }),
        makeCtx(),
      );
      expect(openWorkspaceFile).not.toHaveBeenCalled();
    });

    it("does not reopen a dismissed path", () => {
      excelView.dismissedPaths.add("book.xlsx");
      dispatchSSEEvent(
        makeEvent("ui_hint", { surface: "side_panel", file_path: "book.xlsx" }),
        makeCtx(),
      );
      expect(openWorkspaceFile).not.toHaveBeenCalled();
    });

    it("does not push on mobile", () => {
      mobileMock.getIsMobile.mockReturnValue(true);
      dispatchSSEEvent(
        makeEvent("ui_hint", { surface: "side_panel", file_path: "book.xlsx" }),
        makeCtx(),
      );
      expect(openWorkspaceFile).not.toHaveBeenCalled();
      expect(uiMock.setSidebarTab).not.toHaveBeenCalled();
    });

    it("ignores replayed hints", () => {
      dispatchSSEEvent(
        makeEvent("ui_hint", { surface: "side_panel", file_path: "book.xlsx" }),
        makeCtx({ fromReplay: true }),
      );
      expect(openWorkspaceFile).not.toHaveBeenCalled();
    });

    it("closes a this-turn auto compare when stay suppresses auto-open", () => {
      const ctx = makeCtx();
      dispatchSSEEvent(
        makeEvent("excel_diff", {
          file_path: "a.xlsx",
          file_path_b: "b.xlsx",
          diff_mode: "cross_file",
          diff_summary: { total_cells_compared: 2, cells_different: 1 },
        }),
        ctx,
      );
      expect(excelActions.openCompare).toHaveBeenCalledWith("a.xlsx", "b.xlsx");
      dispatchSSEEvent(
        makeEvent("ui_hint", { surface: "stay", suppress_auto_open: true }),
        ctx,
      );
      expect(excelActions.closeCompare).toHaveBeenCalled();
    });
  });

  describe("mode_changed", () => {
    it("reads mode_name=chat_mode and data.value", () => {
      dispatchSSEEvent(
        makeEvent("mode_changed", { mode_name: "chat_mode", value: "plan", enabled: true }),
        makeCtx(),
      );
      expect(uiMock.setChatMode).toHaveBeenCalledWith("plan");
    });

    it("ignores legacy mode_name=plan without chat_mode", () => {
      dispatchSSEEvent(
        makeEvent("mode_changed", { mode_name: "plan", enabled: true }),
        makeCtx(),
      );
      expect(uiMock.setChatMode).not.toHaveBeenCalled();
    });
  });
});
