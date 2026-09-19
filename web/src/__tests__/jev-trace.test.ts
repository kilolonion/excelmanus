import { describe, it, expect, beforeEach } from "vitest";
import {
  cardTone,
  jevEmptyCopy,
  parseJevTrace,
  packTitle,
} from "@/lib/jev-trace";
import { useJevStore } from "@/stores/jev-store";
import { dispatchSSEEvent } from "@/lib/sse-event-handler";

function makeTrace(overrides: Record<string, unknown> = {}) {
  return parseJevTrace(
    {
      pack: "exposure.turn",
      gate: "shadow",
      applied: false,
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

  it("parses a shadow exposure.turn card", () => {
    const trace = makeTrace();
    expect(trace).not.toBeNull();
    expect(trace?.pack).toBe("exposure.turn");
    expect(packTitle(trace!.pack)).toBe("回合入口");
    expect(trace?.gate).toBe("shadow");
    expect(trace?.applied).toBe(false);
    expect(trace?.answers.domain).toBe("chitchat");
    expect(cardTone(trace!)).toBe("shadow");
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
    expect(cardTone(makeTrace({ kind: "deny", action: "deny" })!)).toBe("deny");
    expect(cardTone(makeTrace({ kind: "ask", action: "ask" })!)).toBe("ask");
    expect(cardTone(makeTrace({ transport: "unavailable" })!)).toBe("unavailable");
    expect(cardTone(makeTrace({ applied: true, gate: "enforce" })!)).toBe("applied");
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
