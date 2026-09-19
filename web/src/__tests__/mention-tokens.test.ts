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

describe("versioned @file mentions", () => {
  const SCREENSHOT_HEX = "a".repeat(64);
  const SCREENSHOT_TOKEN =
    `@file:/web/public/samples/订单与产品.xlsx[Sheet1!B1:B1]@sha256:${SCREENSHOT_HEX}`;

  it("parses worksheet-selection tokens with range and sha256 suffix", () => {
    const tokens = extractMentions(SCREENSHOT_TOKEN);
    expect(tokens).toHaveLength(1);
    expect(tokens[0]).toMatchObject({
      kind: "file",
      value: "/web/public/samples/订单与产品.xlsx",
      rangeSpec: "Sheet1!B1:B1",
      version: `sha256:${SCREENSHOT_HEX}`,
    });
    const label = mentionCapsuleLabel(tokens[0]);
    expect(label).toBe("订单与产品.xlsx · Sheet1!B1");
    expect(label).not.toContain("sha256");
    expect(label).not.toContain("@file:");
    expect(label).not.toContain("/web");
  });

  it("does not swallow @sha256 into the path when range is absent", () => {
    const tokens = extractMentions("@file:uploads/sales.xlsx@sha256:abcd");
    expect(tokens).toHaveLength(1);
    expect(tokens[0]).toMatchObject({
      kind: "file",
      value: "uploads/sales.xlsx",
      rangeSpec: undefined,
      version: "sha256:abcd",
    });
  });
});
