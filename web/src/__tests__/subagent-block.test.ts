import React from "react";
import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { formatSubagentPreview, SubagentBlock } from "@/components/chat/SubagentBlock";

describe("SubagentBlock", () => {
  it("uses the same hairline shell and human-readable step titles as tool calls", () => {
    const html = renderToStaticMarkup(
      React.createElement(SubagentBlock, {
        name: "explorer",
        reason: "探查销售明细里的下降月份",
        iterations: 2,
        toolCalls: 3,
        status: "running",
        tools: [
          {
            index: 0,
            name: "inspect_spreadsheet",
            argsSummary: "overview",
            status: "success",
            args: { mode: "overview", file_path: "sales.xlsx" },
          },
          {
            index: 1,
            name: "analyze_spreadsheet",
            argsSummary: "trend",
            status: "running",
            args: { file_path: "sales.xlsx" },
          },
        ],
      }),
    );

    expect(html).toContain("委派给探索器");
    expect(html).toContain("进行中");
    expect(html).toContain("探查销售明细里的下降月份");
    expect(html).toContain("读取工作表结构");
    expect(html).toContain("分析表格");
    expect(html).toContain("rounded-2xl");
    expect(html).toContain("em-hairline");
    expect(html).not.toContain("violet");
    expect(html).not.toContain("bg-violet");
  });

  it("shows failure copy without the old status capsule", () => {
    const html = renderToStaticMarkup(
      React.createElement(SubagentBlock, {
        name: "subagent",
        reason: "写回汇总",
        iterations: 1,
        toolCalls: 1,
        status: "done",
        success: false,
        stopReason: "error",
        diagnostic: "工具连续失败",
        tools: [
          {
            index: 0,
            name: "edit_spreadsheet",
            argsSummary: "write",
            status: "error",
            error: "写入被拒绝",
            args: { file_path: "sales.xlsx" },
          },
          {
            index: 1,
            name: "inspect_spreadsheet",
            argsSummary: "overview",
            status: "running",
          },
        ],
      }),
    );

    expect(html).toContain("委派给通用子代理");
    expect(html).toContain("失败");
    expect(html).toContain("错误：工具连续失败");
    expect(html).toContain("部分工具调用的结束详情未收到");
    expect(html).not.toContain("animate-spin");
    expect(html).not.toContain("violet");
  });

  it("keeps the title and badge from collapsing, and strips markdown in the preview helper", () => {
    const preview = formatSubagentPreview(
      "工作区扫描完成(根目录 = `<path>/excelmanus`，为本项目仓库)。**根目录顶层条目** (19项)",
    );
    expect(preview).toContain("工作区扫描完成");
    expect(preview).toContain("<path>/excelmanus");
    expect(preview.endsWith("…")).toBe(true);
    expect(preview).not.toContain("`");
    expect(preview).not.toContain("**");

    const html = renderToStaticMarkup(
      React.createElement(SubagentBlock, {
        name: "explorer",
        reason: "扫描工作区",
        iterations: 4,
        toolCalls: 4,
        status: "running",
        summary: "工作区扫描完成(根目录 = `<path>/excelmanus`，为本项目仓库)。**根目录顶层**",
        tools: [],
      }),
    );

    expect(html).toContain("委派给探索器");
    expect(html).toContain("whitespace-nowrap");
    expect(html).toContain("shrink-0");
  });
});
