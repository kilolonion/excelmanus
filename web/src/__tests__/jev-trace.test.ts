import { describe, it, expect, beforeEach } from "vitest";
import {
  ANSWER_LABELS,
  cardTone,
  jevEmptyCopy,
  parseJevTrace,
  packTitle,
  traceStatus,
  traceActionLabel,
  traceNeedsAttention,
  summarizeTraces,
  formatConfidence,
  formatLatency,
  formatContextAnswer,
} from "@/lib/jev-trace";
import { useJevStore } from "@/stores/jev-store";
import { dispatchSSEEvent } from "@/lib/sse-event-handler";

function makeTrace(overrides: Record<string, unknown> = {}) {
  return parseJevTrace(
    {
      pack: "exposure.turn",
      gate: "shadow",
      applied: false,
      stage: "evaluation",
      evaluated: true,
      transport: "gateway",
      latency_ms: 210,
      action: "minimal",
      kind: "noop",
      reason: "domain:chitchat",
      answers: { domain: "chitchat", needs_write: 0.02, mode_hint: "keep", confidence: 0.91 },
      impact: "仅观察，未改 wire/审批/UI",
      ...overrides,
    },
    "jev-1",
  );
}

describe("jev-trace", () => {
  beforeEach(() => {
    useJevStore.getState().reset();
    useJevStore.setState({ drawerOpen: false, pinned: false, railCollapsed: false, chatEnabled: true, seq: 0 });
  });

  it("normalizes a legacy shadow gate value to enforce", () => {
    const trace = makeTrace();
    expect(trace).not.toBeNull();
    expect(trace?.pack).toBe("exposure.turn");
    expect(packTitle(trace!.pack)).toBe("回合入口");
    // 二态契约：旧的 shadow 事件值归一为 enforce
    expect(trace?.gate).toBe("enforce");
    expect(trace?.applied).toBe(false);
    expect(trace?.answers.domain).toBe("chitchat");
    expect(cardTone(trace!)).toBe("unavailable");
  });

  it("drops secrets and oversized state from answers", () => {
    const trace = parseJevTrace(
      {
        pack: "exposure.turn",
        gate: "shadow",
        answers: {
          domain: "chitchat",
          api_key: "vck_should_not_appear",
          user_text: "你好",
        },
      },
      "jev-2",
    );
    expect(trace?.answers.domain).toBe("chitchat");
    expect(trace?.answers.api_key).toBeUndefined();
    expect(trace?.answers.user_text).toBeUndefined();
  });

  it("uses Chinese pack titles and answer labels", () => {
    expect(packTitle("exposure.turn")).toBe("回合入口");
    expect(packTitle("skill.pin")).toBe("技能置顶");
    expect(packTitle("approval.tool_call")).toBe("审批建议");
    expect(packTitle("observation.shape")).toBe("结果收起");
    expect(packTitle("loop.wrap")).toBe("是否继续");
    expect(packTitle("ui.surface")).toBe("界面建议");
    expect(packTitle("observation.prune")).toBe("历史收起");
  });

  it("explains empty state for off / not yet evaluated", () => {
    expect(jevEmptyCopy({ streaming: true, traces: [] })).toBe("本回合尚未评估");
    expect(jevEmptyCopy({ streaming: false, traces: [] })).toBe("本回合尚未评估");
    const off = makeTrace({ gate: "off", reason: "disabled", transport: "unavailable" })!;
    expect(jevEmptyCopy({ streaming: false, traces: [off] })).toBe("当前已关闭，本回合未评估");
  });

  it("marks deny and unavailable tones", () => {
    expect(cardTone(makeTrace({ kind: "deny", action: "deny", gate: "enforce", stage: "effect", state_changed: true })!)).toBe("deny");
    expect(cardTone(makeTrace({ kind: "ask", action: "ask", gate: "enforce", stage: "effect", state_changed: true })!)).toBe("ask");
    expect(cardTone(makeTrace({ transport: "unavailable" })!)).toBe("unavailable");
    expect(cardTone(makeTrace({ stage: "effect", state_changed: true, gate: "enforce" })!)).toBe("applied");
  });

  it("does not present an unapplied denial as an actual intervention", () => {
    const trace = makeTrace({ kind: "deny", action: "deny" })!;
    expect(cardTone(trace)).toBe("unavailable");
    expect(traceStatus(trace).label).toBe("已评估");
    expect(traceActionLabel(trace)).toBe("建议拒绝");
    expect(traceNeedsAttention(trace)).toBe(false);
  });

  it("distinguishes skipped, failed, unapplied, and advisory outcomes", () => {
    expect(traceStatus(makeTrace({ gate: "off", transport: "unavailable" })!).label).toBe("已跳过");
    expect(traceStatus(makeTrace({ gate: "enforce", applied: false })!).label).toBe("已评估");
    expect(traceStatus(makeTrace({ pack: "context.resolve", gate: "enforce", stage: "effect", advice_delivered: true })!).label).toBe("建议已送达");
    const routed = makeTrace({
      pack: "context.resolve", gate: "enforce", stage: "effect", state_changed: true,
      answers: { routed_workspace: "销售" },
      impact: "会话已绑定到工作区「销售」",
    })!;
    expect(traceStatus(routed).label).toBe("行为已改变");
    expect(traceStatus(routed).description).toContain("销售");
    for (const reason of ["error:timeout", "unavailable", "budget_exhausted", "provider_cooldown"]) {
      const trace = makeTrace({ reason })!;
      expect(traceStatus(trace).label).toBe("已回退");
      expect(cardTone(trace)).toBe("unavailable");
      expect(traceNeedsAttention(trace)).toBe(true);
    }
  });

  it("keeps valid context advice visible even when tool exposure is off", () => {
    const skipped = makeTrace({ gate: "off", reason: "disabled" })!;
    const advice = makeTrace({ pack: "context.resolve", gate: "enforce", applied: true })!;
    expect(jevEmptyCopy({ streaming: false, traces: [advice, skipped] })).toBe("");
  });

  it("counts actual evaluations and excludes missing latency from the mean", () => {
    const traces = [
      makeTrace({ latency_ms: 200, gate: "enforce", applied: true })!,
      makeTrace({ latency_ms: 400 })!,
      makeTrace({ latency_ms: 0, reason: "disabled", gate: "off" })!,
      makeTrace({ latency_ms: 0, reason: "budget_exhausted" })!,
    ];
    expect(summarizeTraces(traces)).toEqual({ evaluated: 2, applied: 0, delivered: 0, attention: 1, meanMs: 300 });
  });

  it("separates evaluations, delivery, actual effects and outcomes", () => {
    const traces = [
      makeTrace({ stage: "evaluation", evaluated: true, applied: true, latency_ms: 200 })!,
      makeTrace({ stage: "effect", evaluated: false, advice_delivered: true, latency_ms: 0 })!,
      makeTrace({ stage: "effect", evaluated: false, state_changed: true, latency_ms: 0 })!,
      makeTrace({ stage: "sent", evaluated: false, applied: true, latency_ms: 0 })!,
      makeTrace({ stage: "outcome", evaluated: false, kind: "outcome", applied: true, latency_ms: 0 })!,
    ];
    expect(summarizeTraces(traces)).toEqual({ evaluated: 1, applied: 1, delivered: 1, attention: 0, meanMs: 200 });
    expect(traceStatus(traces[0]).label).toBe("已评估");
    expect(traceStatus(traces[1]).label).toBe("建议已送达");
    expect(traceStatus(traces[2]).label).toBe("行为已改变");
    expect(traceStatus(traces[3]).label).toBe("已发送");
    expect(traceStatus(traces[4]).label).toBe("结果记录");
  });

  it("does not treat legacy eligibility as proof of evaluation or execution", () => {
    const trace = makeTrace({ stage: undefined, evaluated: undefined, applied: true })!;
    expect(trace.applied).toBe(false);
    expect(traceStatus(trace).label).toBe("旧格式记录");
    expect(cardTone(trace)).toBe("unavailable");
    expect(summarizeTraces([trace])).toMatchObject({ evaluated: 0, applied: 0, delivered: 0 });
  });

  it("keeps deterministic and frontend effects visible without an evaluator connection", () => {
    for (const source of ["deterministic", "frontend"]) {
      const trace = makeTrace({
        source, stage: "effect", state_changed: true, transport: "unavailable",
      })!;
      expect(traceStatus(trace).label).toBe("行为已改变");
      expect(traceNeedsAttention(trace)).toBe(false);
    }
  });

  it("formats readable answers and rejects invalid measurements", () => {
    expect(formatContextAnswer("domain", "spreadsheet_write")).toBe("表格编辑");
    expect(formatContextAnswer("needs_write", 0.91)).toBe("91%");
    expect(formatContextAnswer("needs_user", false)).toBe("否");
    for (const value of [NaN, Infinity, -0.1, 1.1]) expect(formatConfidence(value)).toBeNull();
    expect(formatLatency(Infinity)).toBe("—");
    expect(formatLatency(1250)).toBe("1.25 s");
    expect(makeTrace({ latency_ms: Infinity })!.latencyMs).toBe(0);
  });

  it("parses recovery outcome traces and keeps the advice source", () => {
    const trace = parseJevTrace(
      {
        pack: "recovery.next_step",
        gate: "enforce",
        applied: true,
        transport: "gateway",
        kind: "outcome",
        action: "恢复结果 escaped",
        outcome: "escaped",
        source: "deterministic",
        impact: "建议后已摆脱同一失败",
      },
      "jev-outcome",
    );
    expect(trace?.outcome).toBe("escaped");
    expect(trace?.source).toBe("deterministic");
    expect(trace?.kind).toBe("outcome");
  });

  it("labels missing evidence items on mutation.verify cards", () => {
    expect(ANSWER_LABELS.missing_items).toBe("缺证据事项");
    const trace = makeTrace({
      pack: "mutation.verify",
      gate: "enforce",
      applied: true,
      action: "inspect_more",
      answers: { satisfied: 0.9, scope_ok: 0.9, missing_items: 2 },
    })!;
    expect(traceActionLabel(trace)).toBe("2 项待核对");
    const clean = makeTrace({
      pack: "mutation.verify",
      gate: "enforce",
      applied: true,
      action: "none",
      answers: { satisfied: 0.9, scope_ok: 0.9, missing_items: 0 },
    })!;
    expect(traceActionLabel(clean)).not.toContain("待核对");
  });

  it("handler accumulates live jev_trace and skips replay", () => {
    const ctx = {
      assistantMsgId: "a1",
      batcher: { pushText() {}, pushThinking() {}, flush() {}, dispose() {}, hasPendingContent() { return false; } },
      effectiveSessionId: "s1",
      isFirstSend: true,
      thinkingInProgress: false,
      hadStreamError: false,
    };
    dispatchSSEEvent(
      {
        event: "jev_trace",
        data: {
          pack: "exposure.turn",
          gate: "shadow",
          applied: false,
          transport: "unavailable",
          latency_ms: 0,
          action: "full",
          kind: "noop",
          reason: "unavailable",
          answers: { domain: "mixed" },
          impact: "评估不可用，执行面与接线前相同",
        },
      },
      ctx,
    );
    expect(useJevStore.getState().traces).toHaveLength(1);
    expect(useJevStore.getState().traces[0].pack).toBe("exposure.turn");

    dispatchSSEEvent(
      {
        event: "jev_trace",
        data: { pack: "loop.wrap", gate: "shadow", action: "continue" },
      },
      { ...ctx, fromReplay: true },
    );
    expect(useJevStore.getState().traces).toHaveLength(1);
  });

  it("ignores traces and hides chrome when chat is not enabled", () => {
    useJevStore.getState().setChatEnabled(false);
    useJevStore.getState().appendFromEvent({
      pack: "exposure.turn",
      gate: "shadow",
      action: "full",
    });
    expect(useJevStore.getState().traces).toHaveLength(0);
    useJevStore.setState({ traces: [makeTrace()!], drawerOpen: true, pinned: true, pending: true });
    useJevStore.getState().setChatEnabled(false);
    expect(useJevStore.getState()).toMatchObject({
      chatEnabled: false,
      traces: [],
      drawerOpen: false,
      pinned: false,
      pending: false,
    });
  });
});
