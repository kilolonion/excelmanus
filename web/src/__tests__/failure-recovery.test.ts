import { describe, expect, it } from "vitest";
import type { Message } from "@/lib/types";
import {
  canOfferRetry,
  dedupeFailureGuidance,
  displayFailureMessage,
  displayFailureTitle,
  findLastRetryableFailure,
  hydrateFailureGuidanceFromText,
  needsContinuationOffer,
  resolveFailureActions,
  shouldRenderFailureGuidance,
} from "@/lib/failure-recovery";

describe("canOfferRetry", () => {
  it("allows retry for unknown and transient codes", () => {
    expect(canOfferRetry()).toBe(true);
    expect(canOfferRetry("internal_error")).toBe(true);
    expect(canOfferRetry("provider_internal_error")).toBe(true);
    expect(canOfferRetry("model_auth_failed")).toBe(true);
  });

  it("blocks retry only when the session is gone", () => {
    expect(canOfferRetry("session_not_found")).toBe(false);
  });
});

describe("resolveFailureActions", () => {
  it("puts retry first for transient failures", () => {
    expect(resolveFailureActions({ code: "internal_error", retryable: true }).map((a) => a.type))
      .toEqual(["retry", "open_settings", "copy_diagnostic"]);
  });

  it("still offers retry when the backend marked the error non-retryable", () => {
    expect(resolveFailureActions({ code: "model_auth_failed", retryable: false }).map((a) => a.type))
      .toEqual(["open_settings", "retry", "copy_diagnostic"]);
  });

  it("puts subscription login first for revoked OAuth credentials", () => {
    expect(resolveFailureActions({ code: "model_oauth_expired", retryable: false })[0])
      .toEqual({ type: "open_settings", label: "重新登录订阅账号" });
  });

  it("only copies diagnostics when retry is structurally impossible", () => {
    expect(resolveFailureActions({ code: "session_not_found", retryable: false }).map((a) => a.type))
      .toEqual(["copy_diagnostic"]);
  });
});

describe("findLastRetryableFailure", () => {
  it("returns the last assistant failure when it can be retried", () => {
    const messages: Message[] = [
      { id: "u1", role: "user", content: "识别表格" },
      {
        id: "a1",
        role: "assistant",
        blocks: [{
          type: "failure_guidance",
          category: "unknown",
          code: "internal_error",
          title: "内部错误",
          message: "服务处理出现异常，请稍后重试。",
          stage: "preparing",
          retryable: false,
          diagnosticId: "7c6ba38f",
          actions: [{ type: "copy_diagnostic", label: "复制诊断 ID" }],
        }],
      },
    ];
    expect(findLastRetryableFailure(messages)).toEqual({
      messageId: "a1",
      code: "internal_error",
      retryable: false,
      title: "回复未完成",
    });
  });

  it("ignores failures that are not the latest assistant turn", () => {
    const messages: Message[] = [
      { id: "u1", role: "user", content: "第一次" },
      {
        id: "a1",
        role: "assistant",
        blocks: [{
          type: "failure_guidance",
          category: "unknown",
          code: "internal_error",
          title: "内部错误",
          message: "失败",
          stage: "preparing",
          retryable: true,
          diagnosticId: "old",
          actions: [],
        }],
      },
      { id: "u2", role: "user", content: "第二次" },
      { id: "a2", role: "assistant", blocks: [{ type: "text", content: "已完成" }] },
    ];
    expect(findLastRetryableFailure(messages)).toBeNull();
  });

  it("ignores a failure that already has a successful reply", () => {
    const messages: Message[] = [
      { id: "u1", role: "user", content: "识别表格" },
      {
        id: "a1",
        role: "assistant",
        blocks: [
          {
            type: "failure_guidance",
            category: "unknown",
            code: "internal_error",
            title: "回复未完成",
            message: "服务处理出现异常，请稍后重试。",
            stage: "preparing",
            retryable: true,
            diagnosticId: "x",
            actions: [],
          },
          { type: "text", content: "表格已写入 Sheet1。" },
        ],
      },
    ];
    expect(findLastRetryableFailure(messages)).toBeNull();
  });
});

describe("shouldRenderFailureGuidance", () => {
  const failureBlocks: Extract<Message, { role: "assistant" }>["blocks"] = [{
    type: "failure_guidance",
    category: "unknown",
    code: "internal_error",
    title: "回复未完成",
    message: "失败",
    stage: "preparing",
    retryable: true,
    diagnosticId: "x",
    actions: [],
  }];

  it("only shows the current failed turn", () => {
    expect(shouldRenderFailureGuidance(true, failureBlocks)).toBe(true);
    expect(shouldRenderFailureGuidance(false, failureBlocks)).toBe(false);
  });
});

describe("hydrateFailureGuidanceFromText", () => {
  it.each([
    "⚠️ 模型认证失败\nResponses API 错误: token_revoked\n诊断 ID: revoked-1",
    "⚠️ 订阅登录已失效\n请重新登录对应账号，再继续对话。\n诊断 ID: revoked-1",
  ])("restores subscription guidance from current and legacy history: %s", (text) => {
    expect(hydrateFailureGuidanceFromText(text)).toMatchObject({
      category: "model", code: "model_oauth_expired", title: "订阅登录已失效",
      retryable: false, diagnosticId: "revoked-1",
    });
    expect(hydrateFailureGuidanceFromText(text)?.message).not.toContain("API Key");
  });

  it("restores a persisted internal error into a retryable card", () => {
    const block = hydrateFailureGuidanceFromText(
      "⚠️ 内部错误\n服务处理出现异常，请稍后重试。如问题持续，请联系管理员。\n诊断 ID: 7c6ba38f-aaaa-bbbb-cccc-ddddeeeeffff",
    );
    expect(block).not.toBeNull();
    expect(block?.title).toBe("内部错误");
    expect(block?.retryable).toBe(true);
    expect(block?.diagnosticId).toBe("7c6ba38f-aaaa-bbbb-cccc-ddddeeeeffff");
    expect(block?.actions.map((a) => a.type)).toContain("retry");
  });

  it("ignores ordinary assistant text", () => {
    expect(hydrateFailureGuidanceFromText("表格已写入 Sheet1。")).toBeNull();
  });
});

describe("dedupeFailureGuidance", () => {
  const restored = hydrateFailureGuidanceFromText("⚠️ 模型认证失败\n认证失败\n诊断 ID: auth-1")!;
  const detailed = { ...restored, category: "model" as const, code: "model_auth_failed",
    provider: "chatgpt", model: "test-model", stage: "calling_llm", retryable: false };

  it("prefers structured metadata regardless of replay order", () => {
    expect(dedupeFailureGuidance([restored, detailed])).toEqual([detailed]);
    expect(dedupeFailureGuidance([detailed, restored])).toEqual([detailed]);
  });

  it("keeps distinct diagnostics even with identical error text", () => {
    const blocks = [detailed, { ...restored, diagnosticId: "auth-2" }];
    expect(dedupeFailureGuidance(blocks)).toBe(blocks);
  });

  it("matches legacy cards without diagnostics by title and message", () => {
    expect(dedupeFailureGuidance([{ ...restored, diagnosticId: "" }, detailed])).toEqual([detailed]);
  });

  it("repairs the login prompt in old structured browser caches without losing metadata", () => {
    expect(dedupeFailureGuidance([{ ...detailed, message: "Responses API: token_revoked" }])[0])
      .toMatchObject({ code: "model_oauth_expired", title: "订阅登录已失效", retryable: false,
        provider: "chatgpt", model: "test-model", diagnosticId: "auth-1" });
  });
});

describe("needsContinuationOffer", () => {
  const asst = (blocks: Extract<Message, { role: "assistant" }>["blocks"]): Message =>
    ({ id: "a1", role: "assistant", blocks });

  it("returns false for an empty conversation", () => {
    expect(needsContinuationOffer([])).toBe(false);
  });

  it("offers continue when the last message is a user turn with no reply", () => {
    expect(needsContinuationOffer([{ id: "u1", role: "user", content: "分析表格" }])).toBe(true);
  });

  it("offers continue when the assistant reply never produced blocks", () => {
    expect(needsContinuationOffer([asst([])])).toBe(true);
    expect(needsContinuationOffer([asst([{ type: "text", content: "  " }])])).toBe(true);
  });

  it("offers continue for tails that cannot end a turn normally", () => {
    expect(needsContinuationOffer([asst([{ type: "failure_guidance", category: "unknown", code: "internal_error", title: "t", message: "m", stage: "s", retryable: true, diagnosticId: "d", actions: [] }])])).toBe(true);
    expect(needsContinuationOffer([asst([{ type: "tool_call", toolCallId: "t1", name: "run_python", args: {}, status: "running" }])])).toBe(true);
    expect(needsContinuationOffer([asst([{ type: "thinking", content: "…" }])])).toBe(true);
    expect(needsContinuationOffer([asst([{ type: "llm_retry", retryStatus: "exhausted", retryAttempt: 3, retryMaxAttempts: 4, retryDelaySeconds: 5, retryErrorMessage: "boom" }])])).toBe(true);
    // 审批后回合被截断：approval_action 是中间态而非终态
    expect(needsContinuationOffer([asst([
      { type: "tool_call", toolCallId: "t1", name: "write_sheet", args: {}, status: "success" },
      { type: "approval_action", approvalId: "ap1", toolName: "write_sheet", success: true, undoable: false },
    ])])).toBe(true);
  });

  it("does not offer continue after a normally finished turn", () => {
    expect(needsContinuationOffer([asst([{ type: "text", content: "已完成。" }])])).toBe(false);
    expect(needsContinuationOffer([asst([
      { type: "text", content: "已完成。" },
      { type: "token_stats", promptTokens: 1, completionTokens: 1, totalTokens: 2, iterations: 1 },
    ])])).toBe(false);
    expect(needsContinuationOffer([asst([
      { type: "text", content: "部分内容" },
      { type: "status", label: "对话已停止", variant: "info" },
    ])])).toBe(false);
    // 回合结束后追加/仅存于历史的收尾块不能误触发继续
    expect(needsContinuationOffer([asst([
      { type: "text", content: "已完成。" },
      { type: "memory_extracted", entries: [], trigger: "session_end", count: 2 },
    ])])).toBe(false);
    expect(needsContinuationOffer([asst([
      { type: "verification_report", verdict: "pass", confidence: "high", checks: [], issues: [], mode: "advisory" },
    ])])).toBe(false);
    expect(needsContinuationOffer([asst([
      { type: "staging_hint", pendingCount: 1, files: ["a.xlsx"] },
    ])])).toBe(false);
  });
});

describe("displayFailureCopy", () => {
  it("softens the generic internal error title", () => {
    expect(displayFailureTitle("internal_error", "内部错误")).toBe("回复未完成");
    expect(displayFailureTitle("rate_limited", "请求频率受限")).toBe("请求频率受限");
  });

  it("drops the admin-contact leftover", () => {
    expect(displayFailureMessage("服务处理出现异常，请稍后重试。如问题持续，请联系管理员。"))
      .toBe("服务处理出现异常，请稍后重试。");
  });
});
