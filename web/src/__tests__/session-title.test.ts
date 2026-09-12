import { describe, expect, it } from "vitest";
import {
  deriveSessionTitleFromMessages,
  instantSessionTitle,
} from "@/lib/session-title";
import type { Message } from "@/lib/types";

describe("instantSessionTitle", () => {
  it("keeps the full first line", () => {
    expect(instantSessionTitle("识别截图中的表格，还原数据")).toBe(
      "识别截图中的表格，还原数据",
    );
  });

  it("strips upload notices and keeps the caption", () => {
    expect(
      instantSessionTitle("[已上传文件: ./uploads/sales.xlsx]\n\n用一段 Markdown 写说明"),
    ).toBe("用一段 Markdown 写说明");
  });

  it("keeps the upload notice when there is no caption", () => {
    expect(instantSessionTitle("[已上传图片: ./uploads/receipt.png]")).toBe(
      "[已上传图片: ./uploads/receipt.png]",
    );
  });
});

describe("deriveSessionTitleFromMessages", () => {
  it("uses the full user prompt, not a 12-character slice", () => {
    const messages = [
      { id: "1", role: "user", content: "ExcelManus 简介请写完整一点" },
    ] as Message[];
    expect(deriveSessionTitleFromMessages(messages)).toBe(
      "ExcelManus 简介请写完整一点",
    );
  });
});
