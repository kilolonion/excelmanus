import { describe, expect, it } from "vitest";
import { historyAssistantContent, restoreTaskLists } from "@/lib/history-blocks";
import type { AssistantBlock, Message } from "@/lib/types";

describe("durable assistant content", () => {
  it("restores one visible reasoning stream from provider aliases", () => {
    expect(historyAssistantContent({ reasoning_content: "先核对金额", thinking: "先核对金额", content: "已完成" }))
      .toEqual({ thinking: "先核对金额", text: "已完成" });
  });

  it("separates typed reasoning and text without rendering replay metadata", () => {
    expect(historyAssistantContent({ content: [
      { type: "thinking", thinking: "核对合计", signature: "opaque" },
      { type: "redacted_thinking", data: "secret", text: "redacted" },
      { type: "output_text", text: "已" }, { type: "text", text: "完成" },
    ], replay_state: { encrypted_content: "secret" } })).toEqual({ thinking: "核对合计", text: "已完成" });
    expect(historyAssistantContent({ reasoning_details: [
      { type: "reasoning.encrypted", text: "opaque" },
      { type: "reasoning.summary", summary: [{ type: "summary_text", text: "可见摘要" }] },
    ] })).toEqual({ thinking: "可见摘要", text: "" });
  });
});

const tool = (name: string, args: Record<string, unknown>, status: "success" | "error" = "success"): AssistantBlock =>
  ({ type: "tool_call", name, args, status });
const reply = (id: string, blocks: AssistantBlock[]): Message => ({ id, role: "assistant", blocks });
const taskItems = (message: Message) => message.role === "assistant"
  ? message.blocks.filter((block) => block.type === "tool_call" && block.taskList).map((block) => block.type === "tool_call" ? block.taskList! : []) : [];

describe("historical task cards", () => {
  it("preserves the snapshot of each creation and update, excluding failed operations", () => {
    const messages = [reply("a", [
      tool("task_create", { subtasks: ["读取原图", { title: "核对金额", verification: { expected: "合计一致" } }] }),
      tool("task_update", { task_index: 0, status: "completed" }),
      tool("task_update", { task_index: 1, status: "completed" }, "error"),
    ])];
    const restored = restoreTaskLists(messages);
    expect(taskItems(restored[0])).toEqual([[
      { index: 0, content: "读取原图", status: "pending", verification: undefined },
      { index: 1, content: "核对金额", status: "pending", verification: "合计一致" },
    ], [
      { index: 0, content: "读取原图", status: "completed", verification: undefined },
      { index: 1, content: "核对金额", status: "pending", verification: "合计一致" },
    ]]);
    expect(restoreTaskLists(restored)).toEqual(restored);
  });

  it("replays updates across turns and pages without changing earlier cards", () => {
    const older = reply("a", [tool("task_create", { subtasks: ["读取", "写入"] })]);
    const newer = reply("b", [tool("task_update", { task_index: 0, status: "completed" })]);
    expect(taskItems(restoreTaskLists([newer])[0])).toEqual([]);
    const restored = restoreTaskLists([older, { id: "u", role: "user", content: "继续" }, newer]);
    expect(taskItems(restored[0])[0].map((item) => item.status)).toEqual(["pending", "pending"]);
    expect(taskItems(restored[2])[0].map((item) => item.status)).toEqual(["completed", "pending"]);
  });

  it("does not create a card for a rejected task creation", () => {
    const message = reply("a", [tool("task_create", { subtasks: ["未创建"] }, "error")]);
    expect(restoreTaskLists([message])).toEqual([message]);
  });
});
