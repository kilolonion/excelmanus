import { describe, expect, it } from "vitest";
import type { Message } from "@/lib/types";
import {
  canOfferRetry,
  displayFailureMessage,
  displayFailureTitle,
  findLastRetryableFailure,
  hydrateFailureGuidanceFromText,
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
