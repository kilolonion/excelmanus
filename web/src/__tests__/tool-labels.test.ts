import { describe, expect, it } from "vitest";
import {
  activityGroupTitle,
  approvalCopy,
  extractToolContext,
  formatToolContextLine,
  toolActionTitle,
} from "@/lib/tool-labels";

describe("extractToolContext", () => {
  it("takes filename sheet and range from args", () => {
    const ctx = extractToolContext({
      file_path: "/tmp/月度销售.xlsx",
      sheet: "区域汇总",
      range: "B2:G5",
    });
    expect(ctx.filename).toBe("月度销售.xlsx");
    expect(ctx.sheet).toBe("区域汇总");
    expect(ctx.range).toBe("B2:G5");
    expect(ctx.cellCount).toBe(24);
  });

  it("does not invent cell counts when range is missing", () => {
    const ctx = extractToolContext({ file_path: "a.xlsx" });
    expect(ctx.cellCount).toBeUndefined();
    expect(formatToolContextLine(ctx)).toBe("a.xlsx");
  });
});

describe("toolActionTitle", () => {
  it("uses inspect mode for a readable title", () => {
    expect(toolActionTitle("inspect_spreadsheet", { mode: "overview" })).toBe("读取工作表结构");
    expect(toolActionTitle("inspect_spreadsheet", { mode: "range" })).toBe("读取明细");
  });

  it("labels write operations as writing back the sheet", () => {
    expect(
      toolActionTitle("edit_spreadsheet", {
        operations: [{ kind: "write", values: [[1]] }],
      }),
    ).toBe("写回原工作表");
  });

  it("uses human titles for file tools", () => {
    expect(toolActionTitle("delete_file")).toBe("删除文件");
    expect(toolActionTitle("copy_file")).toBe("复制文件");
    expect(toolActionTitle("offer_download")).toBe("提供下载");
    expect(toolActionTitle("rename_file")).toBe("重命名文件");
  });
});

describe("activityGroupTitle", () => {
  it("uses waiting title for pending writes", () => {
    expect(
      activityGroupTitle([{ name: "edit_spreadsheet", status: "pending" }]),
    ).toBe("更新工作表");
  });

  it("groups read-only tools", () => {
    expect(
      activityGroupTitle([
        { name: "inspect_spreadsheet", status: "success" },
        { name: "analyze_spreadsheet", status: "success" },
      ]),
    ).toBe("读取数据");
  });
});

describe("approvalCopy", () => {
  it("asks to write the original file for spreadsheet edits", () => {
    const copy = approvalCopy("edit_spreadsheet", { sheet: "区域汇总" });
    expect(copy.title).toBe("允许写入原文件？");
    expect(copy.description).toContain("区域汇总");
  });
});
