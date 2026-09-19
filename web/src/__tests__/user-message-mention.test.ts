import { describe, expect, it } from "vitest";
import { formatFileMention } from "@/components/chat/chat-input-insert";
import {
  findAtomicMentionDeletion,
  insertEditMentionTokens,
  restoreFullMentions,
} from "@/components/chat/UserMessage";

const PROTOCOL =
  "@file:/web/public/samples/订单与产品.xlsx[Sheet1!A1:C10]@sha256:abcd1234";
const DISPLAY = "订单与产品.xlsx · Sheet1!A1:C10";

describe("restoreFullMentions", () => {
  it("keeps visible text as short labels and restores the send protocol", () => {
    const map = new Map([[DISPLAY, PROTOCOL]]);
    const visible = `请分析 ${DISPLAY} `;

    expect(visible).not.toMatch(/@file:/);
    expect(visible).not.toMatch(/sha256/);
    expect(visible).not.toMatch(/\/web\/public\//);

    expect(restoreFullMentions(visible, map)).toBe(`请分析 ${PROTOCOL} `);
  });

  it("replaces every mapped display token", () => {
    const otherDisplay = "销售.xlsx";
    const otherFull = "@file:uploads/销售.xlsx@sha256:ffff";
    const map = new Map([
      [DISPLAY, PROTOCOL],
      [otherDisplay, otherFull],
    ]);
    const visible = `${DISPLAY} 对比 ${otherDisplay}`;

    expect(restoreFullMentions(visible, map)).toBe(`${PROTOCOL} 对比 ${otherFull}`);
  });

  it("is a no-op when the map is empty", () => {
    expect(restoreFullMentions(`请分析 ${DISPLAY}`, new Map())).toBe(
      `请分析 ${DISPLAY}`,
    );
  });

  it("replaces longer display tokens before shorter substring keys", () => {
    const shortDisplay = "订单与产品.xlsx";
    const shortFull = "@file:订单与产品.xlsx";
    const longDisplay = "uploads/订单与产品.xlsx · Sheet1!B1";
    const longFull = "@file:uploads/订单与产品.xlsx[Sheet1!B1]";
    const map = new Map([
      [shortDisplay, shortFull],
      [longDisplay, longFull],
    ]);
    const visible = `${longDisplay} 和 ${shortDisplay}`;

    expect(restoreFullMentions(visible, map)).toBe(`${longFull} 和 ${shortFull}`);
    expect(restoreFullMentions(visible, map)).not.toMatch(/uploads\/@file:/);
  });
});

describe("insertEditMentionTokens", () => {
  it("roundtrips formatFileMention through the display map back to protocol", () => {
    const full = formatFileMention({
      path: "/web/public/samples/订单与产品.xlsx",
      sheet: "Sheet1",
      range: "A1:C10",
      version: "abcd1234",
    });
    const map = new Map<string, string>();
    const { newText, displayTokens } = insertEditMentionTokens("请分析", 3, [full], map);

    expect(full).toContain("@file:");
    expect(full).toContain("@sha256:");
    expect(restoreFullMentions(newText, map)).toContain(full);
    expect(displayTokens).toHaveLength(1);
  });

  it("treats /mentions relative paths as formatFileMention paths", () => {
    const full = formatFileMention({ path: "uploads/sales.xlsx" });
    expect(full).toBe("@file:uploads/sales.xlsx");

    const map = new Map<string, string>();
    const { newText } = insertEditMentionTokens("", 0, [full], map);
    expect(restoreFullMentions(newText, map)).toContain("@file:uploads/sales.xlsx");
  });
});

describe("findAtomicMentionDeletion", () => {
  it("removes the whole display token and trailing space on Backspace at the token end", () => {
    const text = `请看 ${DISPLAY} 后续`;
    const tokenStart = text.indexOf(DISPLAY);
    const cursor = tokenStart + DISPLAY.length;
    const hit = findAtomicMentionDeletion(text, cursor, "Backspace", [DISPLAY]);

    expect(hit).toEqual({
      newText: "请看 后续",
      token: DISPLAY,
      newCursor: tokenStart,
    });
  });

  it("removes the whole display token on Delete inside the token", () => {
    const text = `请看 ${DISPLAY} 后续`;
    const tokenStart = text.indexOf(DISPLAY);
    const hit = findAtomicMentionDeletion(text, tokenStart, "Delete", [DISPLAY]);

    expect(hit).toEqual({
      newText: "请看 后续",
      token: DISPLAY,
      newCursor: tokenStart,
    });
  });

  it("returns null when the cursor is outside every confirmed token", () => {
    expect(
      findAtomicMentionDeletion(`请看 ${DISPLAY} `, 0, "Backspace", [DISPLAY]),
    ).toBeNull();
  });
});
