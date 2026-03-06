/**
 * sse-event-handler.ts 单元测试
 *
 * 覆盖：
 * - stream_init 事件设置 activeStreamId + latestSeq + clearResumeFailed
 * - 普通事件更新 latestSeq = max(current, eventSeq)
 * - resume_failed 事件触发 markResumeFailed + setPipelineStatus
 * - subscribe_resume 事件设置 stream state + 清除 resume failed
 * - _friendlyRouteMode 映射
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
  setPendingQuestion: vi.fn(),
  setToolProgress: vi.fn(),
  clearToolProgress: vi.fn(),
  setBatchProgress: vi.fn(),
  pushVlmPhase: vi.fn(),
  addAffectedFiles: vi.fn(),
  saveCurrentSession: vi.fn(),
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
    currentSessionId: null,
    pendingApproval: null,
    pendingQuestion: null,
    ...chatActions,
  };
  // 重置所有 mock 调用记录
  for (const fn of Object.values(chatActions)) {
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

vi.mock("@/stores/session-store", () => ({
  useSessionStore: {
    getState: () => ({
      activeSessionId: "test-session",
      setActiveSession: vi.fn(),
      updateSessionTitle: vi.fn(),
    }),
  },
}));

vi.mock("@/stores/ui-store", () => ({
  useUIStore: {
    getState: () => ({
      setFullAccessEnabled: vi.fn(),
      setChatMode: vi.fn(),
    }),
  },
}));

vi.mock("@/stores/excel-store", () => ({
  useExcelStore: {
    getState: () => ({
      addPreview: vi.fn(),
      addDiff: vi.fn(),
      addTextDiff: vi.fn(),
      addTextPreview: vi.fn(),
      addRecentFileIfNotDismissed: vi.fn(),
      appendStreamingArgs: vi.fn(),
      clearStreamingArgs: vi.fn(),
      setMergeResult: vi.fn(),
      fetchOperationHistory: vi.fn(),
      fetchBackups: vi.fn(),
      handleStagingUpdated: vi.fn(),
      bumpWorkspaceFilesVersion: vi.fn(),
      compareMode: false,
      panelOpen: false,
      openCompare: vi.fn(),
      openPanel: vi.fn(),
    }),
  },
}));

import {
  dispatchSSEEvent,
  finalizeThinking,
  preDispatch,
  _friendlyRouteMode,
  _mapDiffChanges,
  type SSEEvent,
  type SSEHandlerContext,
  type DeltaBatcher,
} from "@/lib/sse-event-handler";

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
  beforeEach(() => {
    resetChatState();
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
    it("追加 status block with route variant", () => {
      const ctx = makeCtx();
      dispatchSSEEvent(
        makeEvent("route_end", { route_mode: "all_tools", skills_used: ["csv_lookup"] }),
        ctx,
      );

      expect(chatActions.appendBlock).toHaveBeenCalledWith(
        "a1",
        expect.objectContaining({
          type: "status",
          label: "Smart Route",
          detail: "csv_lookup",
          variant: "route",
        }),
      );
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

  // ── _friendlyRouteMode ──────────────────────────────────────

  describe("_friendlyRouteMode", () => {
    it("已知 mode 返回友好标签", () => {
      expect(_friendlyRouteMode("all_tools")).toBe("Smart Route");
      expect(_friendlyRouteMode("control_command")).toBe("Control Command");
      expect(_friendlyRouteMode("fallback")).toBe("Fallback Mode");
    });

    it("未知 mode 返回原值", () => {
      expect(_friendlyRouteMode("custom_mode")).toBe("custom_mode");
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
          approval_tool_name: "write_cells",
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
          toolName: "write_cells",
          riskLevel: "high",
        }),
      );
    });
  });
});
