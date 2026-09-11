import { describe, expect, it } from "vitest";
import { extractMentions, mentionCapsuleLabel } from "@/components/chat/mention-tokens";

describe("extractMentions", () => {
  it("matches CJK bare @filename mentions", () => {
    const text = "读取数据并用 Python 做回归分析 @广告与销售数据.csv";
    const tokens = extractMentions(text);
    expect(tokens).toHaveLength(1);
    expect(tokens[0]).toMatchObject({
      kind: "bare-file",
      value: "广告与销售数据.csv",
      raw: "@广告与销售数据.csv",
    });
  });

  it("matches typed @file: mentions before bare filenames", () => {
    const tokens = extractMentions("see @file:uploads/sales.xlsx[Sheet1!A1:C10]");
    expect(tokens).toHaveLength(1);
    expect(tokens[0]).toMatchObject({
      kind: "file",
      value: "uploads/sales.xlsx",
      rangeSpec: "Sheet1!A1:C10",
    });
  });
});

describe("mentionCapsuleLabel", () => {
  it("shows the filename instead of the raw @token", () => {
    expect(
      mentionCapsuleLabel({
        start: 0,
        end: 12,
        raw: "@广告与销售数据.csv",
        kind: "bare-file",
        value: "广告与销售数据.csv",
      }),
    ).toBe("广告与销售数据.csv");
  });
});
