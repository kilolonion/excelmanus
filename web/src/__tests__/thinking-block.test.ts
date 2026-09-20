import React from "react";
import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { isNearScrollBottom, ThinkingBlock } from "@/components/chat/ThinkingBlock";
import {
  formatThinkingDuration,
  normalizeThinkingEffortOptions,
  thinkingPreview,
} from "@/lib/thinking";

describe("formatThinkingDuration", () => {
  it("omits empty and sub-second values", () => {
    expect(formatThinkingDuration(0)).toBe("");
    expect(formatThinkingDuration(-1)).toBe("");
  });

  it("formats seconds and mixed minutes", () => {
    expect(formatThinkingDuration(12)).toBe("12s");
    expect(formatThinkingDuration(60)).toBe("1m");
    expect(formatThinkingDuration(75)).toBe("1m 15s");
  });
});

describe("ThinkingBlock chrome", () => {
  it("keeps the heading and status badge from wrapping", () => {
    const html = renderToStaticMarkup(
      React.createElement(ThinkingBlock, {
        content: "先探查工作区，再生成示例数据",
        duration: 1,
      }),
    );
    expect(html).toContain("思考完成");
    expect(html).toContain("已完成");
    expect(html).toContain("whitespace-nowrap");
    expect(html).toContain("shrink-0");
  });

  it("contains active thinking scroll instead of chaining it to the message stream", () => {
    const html = renderToStaticMarkup(
      React.createElement(ThinkingBlock, {
        content: "正在持续生成的思考内容",
        isActive: true,
      }),
    );
    expect(html).toContain("overflow-y-auto");
    expect(html).toContain("overscroll-contain");
  });
});

describe("isNearScrollBottom", () => {
  it("only follows streaming content while the reader remains at the bottom", () => {
    expect(isNearScrollBottom(388, 100, 500)).toBe(true);
    expect(isNearScrollBottom(387, 100, 500)).toBe(false);
    expect(isNearScrollBottom(120, 100, 500)).toBe(false);
  });
});

describe("thinkingPreview", () => {
  it("collapses whitespace and truncates", () => {
    expect(thinkingPreview("先看结构\n再写回", 20)).toBe("先看结构 再写回");
    expect(thinkingPreview("用户想分析销售数据趋势。我需要先读取表格了解数据结构。", 12)).toBe(
      "用户想分析销售数据趋势。…",
    );
  });
});

describe("normalizeThinkingEffortOptions", () => {
  it("filters unknown values, removes duplicates, and restores canonical order", () => {
    expect(normalizeThinkingEffortOptions(["max", "low", "unknown", "low"])).toEqual([
      "low",
      "max",
    ]);
  });

  it("falls back to all levels when no valid choice remains", () => {
    expect(normalizeThinkingEffortOptions([])).toEqual([
      "none",
      "minimal",
      "low",
      "medium",
      "high",
      "xhigh",
      "max",
    ]);
  });
});
