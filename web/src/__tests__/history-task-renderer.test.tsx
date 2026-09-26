import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { AssistantBlockRenderer } from "@/components/chat/assistant-blocks/AssistantBlockRenderer";

describe("historical task operation rendering", () => {
  it.each(["task_create", "task_update", "write_plan"])("renders %s as a progress card", (name) => {
    const html = renderToStaticMarkup(<AssistantBlockRenderer messageId="reply" blockIndex={0} block={{
      type: "tool_call", name, args: {}, status: "success", taskList: [
        { index: 0, content: "读取图片", status: "completed" },
        { index: 1, content: "核对金额", status: "pending", verification: "合计一致" },
      ],
    }} />);
    expect(html).toContain("任务进度");
    expect(html).toContain("1/2");
    expect(html).toContain("读取图片");
    expect(html).toContain("核对金额");
    expect(html).toContain("合计一致");
    expect(html).not.toContain("tool-call-toggle");
  });
});
