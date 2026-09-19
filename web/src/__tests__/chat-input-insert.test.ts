import { describe, expect, it } from "vitest";
import {
  applyDisplayReplacements,
  detectAtMentionTrigger,
  formatFileMention,
  formatMentionDisplay,
  insertTokensIntoText,
  toDisplayMentionTokens,
  truncateMention,
} from "@/components/chat/chat-input-insert";

describe("truncateMention", () => {
  it("keeps short tokens unchanged", () => {
    expect(truncateMention("@file:scores.xlsx")).toEqual([
      "@file:scores.xlsx",
      "@file:scores.xlsx",
    ]);
  });

  it("shortens a long filename and keeps the range spec", () => {
    const full = "@file:学生成绩_2024_第一学期_期末考试.xlsx[Sheet1!A1:C10]";
    const [display, original] = truncateMention(full);
    expect(original).toBe(full);
    expect(display).toBe("@file:学生成绩_2…期末考试.xlsx[Sheet1!A1:C10]");
  });

  it("returns the original token when the pattern does not match", () => {
    expect(truncateMention("not-a-mention")).toEqual(["not-a-mention", "not-a-mention"]);
  });

  it("keeps directory and version suffix when shortening", () => {
    const full =
      "@file:uploads/学生成绩_2024_第一学期_期末考试.xlsx[Sheet1!A1:C10]@sha256:abcd";
    const [display, original] = truncateMention(full);
    expect(original).toBe(full);
    expect(display).toBe(
      "@file:uploads/学生成绩_2…期末考试.xlsx[Sheet1!A1:C10]@sha256:abcd",
    );
  });
});

describe("formatFileMention", () => {
  it("keeps relative path, range, and content version", () => {
    expect(
      formatFileMention({
        path: "./uploads/sales.xlsx",
        sheet: "Sheet1",
        range: "A1:B2",
        version: "sha256:abcd",
      }),
    ).toBe("@file:uploads/sales.xlsx[Sheet1!A1:B2]@sha256:abcd");
  });

  it("omits version when missing", () => {
    expect(
      formatFileMention({ path: "sales.xlsx", sheet: "Data", range: "A1" }),
    ).toBe("@file:sales.xlsx[Data!A1]");
  });
});

describe("insertTokensIntoText", () => {
  it("inserts at the start without a leading space", () => {
    expect(insertTokensIntoText("", 0, ["@a.xlsx"])).toEqual({
      newText: "@a.xlsx ",
      newCursorPos: "@a.xlsx ".length,
    });
  });

  it("adds a separating space when the cursor is after text", () => {
    expect(insertTokensIntoText("hello", 5, ["@a.xlsx"])).toEqual({
      newText: "hello @a.xlsx ",
      newCursorPos: "hello @a.xlsx ".length,
    });
  });

  it("joins multiple tokens with spaces", () => {
    expect(insertTokensIntoText("x", 1, ["@a", "@b"])).toEqual({
      newText: "x @a @b ",
      newCursorPos: "x @a @b ".length,
    });
  });
});

describe("formatMentionDisplay", () => {
  it("shows basename and collapsed single-cell range for the screenshot token", () => {
    const full = formatFileMention({
      path: "/web/public/samples/订单与产品.xlsx",
      sheet: "Sheet1",
      range: "B1:B1",
      version: "sha256:" + "a".repeat(64),
    });
    const { display } = formatMentionDisplay(full);
    expect(display).toBe("订单与产品.xlsx · Sheet1!B1");
    expect(display.length).toBeLessThan(30);
    expect(display).not.toContain("/web");
    expect(display).not.toContain("@file:");
    expect(display).not.toContain("sha256");
  });

  it("drops version and omits range when the token has no range spec", () => {
    const full = formatFileMention({
      path: "/web/public/samples/订单与产品.xlsx",
      version: "sha256:" + "a".repeat(64),
    });
    expect(formatMentionDisplay(full).display).toBe("订单与产品.xlsx");
  });

  it("returns the original token when the pattern does not match", () => {
    expect(formatMentionDisplay("not-a-mention")).toEqual({
      display: "not-a-mention",
      full: "not-a-mention",
    });
  });
});

describe("toDisplayMentionTokens", () => {
  it("appends a directory segment when two files share a basename", () => {
    const first = formatFileMention({
      path: "/web/public/samples/订单与产品.xlsx",
      sheet: "Sheet1",
      range: "B1:B1",
    });
    const second = formatFileMention({
      path: "/web/public/uploads/订单与产品.xlsx",
      sheet: "Sheet1",
      range: "B1:B1",
    });
    const tokenMap = new Map<string, string>();
    const displays = toDisplayMentionTokens([first, second], tokenMap);
    expect(displays[0]).toBe("订单与产品.xlsx · Sheet1!B1");
    expect(displays[1]).toBe("uploads/订单与产品.xlsx · Sheet1!B1");
    expect(tokenMap.get(displays[0])).toBe(first);
    expect(tokenMap.get(displays[1])).toBe(second);
  });
});

describe("applyDisplayReplacements", () => {
  it("replaces longer display keys first so a short label cannot pollute a longer one", () => {
    const shortDisplay = "订单与产品.xlsx";
    const longDisplay = "uploads/订单与产品.xlsx · Sheet1!B1";
    const shortFull = "@file:samples/订单与产品.xlsx";
    const longFull = "@file:uploads/订单与产品.xlsx[Sheet1!B1]";
    const tokenMap = new Map<string, string>([
      [shortDisplay, shortFull],
      [longDisplay, longFull],
    ]);

    expect(
      applyDisplayReplacements(`请对比 ${shortDisplay} 与 ${longDisplay}`, tokenMap),
    ).toBe(`请对比 ${shortFull} 与 ${longFull}`);

    expect(applyDisplayReplacements(`见 ${longDisplay}`, tokenMap)).toBe(`见 ${longFull}`);
  });
});

describe("detectAtMentionTrigger", () => {
  it("opens after a bare @", () => {
    expect(detectAtMentionTrigger("@")).toBe("");
  });

  it("returns the filter after @", () => {
    expect(detectAtMentionTrigger("see @fi")).toBe("fi");
  });

  it("ignores @ in the middle of a word", () => {
    expect(detectAtMentionTrigger("foo@bar")).toBeNull();
  });

  it("closes once a space follows the mention", () => {
    expect(detectAtMentionTrigger("@file hello")).toBeNull();
  });
});
