import { describe, expect, it } from "vitest";
import { formatFileMention } from "@/components/chat/chat-input-insert";
import { backfillPendingUploadFull } from "@/components/chat/use-chat-upload";

describe("backfillPendingUploadFull", () => {
  it("rewrites pending @file:basename to the resolved upload path", () => {
    const pending = formatFileMention({ path: "报告.xlsx" });
    expect(pending).toBe("@file:报告.xlsx");

    const tokenMap = new Map<string, string>([["报告.xlsx", pending]]);
    const hit = backfillPendingUploadFull(
      tokenMap,
      "报告.xlsx",
      "./uploads/ab12cd34_报告.xlsx",
    );

    expect(hit).toBe(true);
    expect(tokenMap.get("报告.xlsx")).toBe("@file:uploads/ab12cd34_报告.xlsx");
    expect([...tokenMap.keys()]).toEqual(["报告.xlsx"]);
  });

  it("returns false and leaves the map unchanged when no pending full matches", () => {
    const tokenMap = new Map<string, string>([
      ["其他.xlsx", "@file:uploads/deadbeef_其他.xlsx"],
    ]);
    const snapshot = new Map(tokenMap);

    const hit = backfillPendingUploadFull(
      tokenMap,
      "报告.xlsx",
      "./uploads/ab12cd34_报告.xlsx",
    );

    expect(hit).toBe(false);
    expect(tokenMap).toEqual(snapshot);
  });

  it("replaces every entry that shares the pending full token", () => {
    // 同名并发上传共用最后完成路径，属已知限制。
    const pending = formatFileMention({ path: "报告.xlsx" });
    const tokenMap = new Map<string, string>([
      ["报告.xlsx", pending],
      ["uploads/报告.xlsx", pending],
    ]);

    const hit = backfillPendingUploadFull(
      tokenMap,
      "报告.xlsx",
      "./uploads/ab12cd34_报告.xlsx",
    );

    expect(hit).toBe(true);
    const resolved = "@file:uploads/ab12cd34_报告.xlsx";
    expect(tokenMap.get("报告.xlsx")).toBe(resolved);
    expect(tokenMap.get("uploads/报告.xlsx")).toBe(resolved);
  });

  it("is idempotent once full is already an uploads path", () => {
    const tokenMap = new Map<string, string>([
      ["报告.xlsx", "@file:uploads/ab12cd34_报告.xlsx"],
    ]);

    const hit = backfillPendingUploadFull(
      tokenMap,
      "报告.xlsx",
      "./uploads/ff00aa11_报告.xlsx",
    );

    expect(hit).toBe(false);
    expect(tokenMap.get("报告.xlsx")).toBe("@file:uploads/ab12cd34_报告.xlsx");
  });
});
