import { describe, expect, it } from "vitest";
import {
  blockHasVisibleOutput,
  getAssistantLeadingSurface,
  hasVisibleAssistantOutput,
  isHiddenAssistantChrome,
} from "@/lib/assistant-chrome";

describe("isHiddenAssistantChrome", () => {
  it("hides iteration dividers and Smart Route status", () => {
    expect(isHiddenAssistantChrome({ type: "iteration" })).toBe(true);
    expect(isHiddenAssistantChrome({ type: "status", variant: "route" })).toBe(true);
  });

  it("keeps thinking tools and other status visible", () => {
    expect(isHiddenAssistantChrome({ type: "thinking" })).toBe(false);
    expect(isHiddenAssistantChrome({ type: "tool_call" })).toBe(false);
    expect(isHiddenAssistantChrome({ type: "status", variant: "info" })).toBe(false);
    expect(isHiddenAssistantChrome({ type: "status" })).toBe(false);
  });
});

describe("visible assistant output", () => {
  it("ignores route chrome, empty text, and token stats", () => {
    expect(blockHasVisibleOutput({ type: "status", variant: "route" })).toBe(false);
    expect(blockHasVisibleOutput({ type: "iteration" })).toBe(false);
    expect(blockHasVisibleOutput({ type: "token_stats" })).toBe(false);
    expect(blockHasVisibleOutput({ type: "text", content: "   " })).toBe(false);
    expect(blockHasVisibleOutput({ type: "text", content: "你好" })).toBe(true);
    expect(blockHasVisibleOutput({ type: "thinking", content: "" })).toBe(true);
    expect(hasVisibleAssistantOutput([
      { type: "status", variant: "route" },
      { type: "text", content: "" },
    ])).toBe(false);
  });

  it("uses waiting before the first visible character while streaming", () => {
    expect(getAssistantLeadingSurface([], true)).toBe("waiting");
    expect(getAssistantLeadingSurface([{ type: "text", content: "" }], true)).toBe("waiting");
    expect(getAssistantLeadingSurface([], false)).toBe("text");
  });

  it("aligns text and tool bubbles separately", () => {
    expect(getAssistantLeadingSurface([{ type: "text", content: "ExcelManus" }], false)).toBe("text");
    expect(getAssistantLeadingSurface([
      { type: "status", variant: "route" },
      { type: "text", content: "ExcelManus" },
    ], false)).toBe("text");
    expect(getAssistantLeadingSurface([
      { type: "thinking", content: "先看结构" },
      { type: "text", content: "演示完成" },
    ], false)).toBe("bubble");
    expect(getAssistantLeadingSurface([
      { type: "tool_call" },
    ], true)).toBe("bubble");
  });
});
