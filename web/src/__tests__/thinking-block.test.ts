import { describe, expect, it } from "vitest";
import { formatThinkingDuration, thinkingPreview } from "@/lib/thinking";

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

describe("thinkingPreview", () => {
  it("collapses whitespace and truncates", () => {
    expect(thinkingPreview("先看结构\n再写回", 20)).toBe("先看结构 再写回");
    expect(thinkingPreview("用户想分析销售数据趋势。我需要先读取表格了解数据结构。", 12)).toBe(
      "用户想分析销售数据趋势。…",
    );
  });
});
