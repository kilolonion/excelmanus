import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import { BackgroundTaskCard } from "@/components/chat/BackgroundTasks";
import { SubagentBlock } from "@/components/chat/SubagentBlock";
import { controlSubagentRun, fetchSubagentRuns } from "@/lib/api";
import { completedSubagentFiles } from "@/lib/subagent-runs";
import { stopGeneration } from "@/lib/chat-actions";
import { useChatStore } from "@/stores/chat-store";
import type { SubagentRun } from "@/lib/types";

const run: SubagentRun = {
  run_id: "run-1", agent_name: "explorer", task: "汇总销售额", file_paths: [],
  background: true, status: "running", created_at: 1, started_at: 2, finished_at: null,
  iteration: 2, tool_calls: 3, last_tool: "inspect_spreadsheet", result: null, resumed_from: null,
};

function renderCard(patch: Partial<SubagentRun> = {}) {
  return renderToStaticMarkup(React.createElement(BackgroundTaskCard, {
    run: { ...run, ...patch }, onControl: vi.fn(), onOpenFile: vi.fn(),
  }));
}

describe("background task display", () => {
  it("offers steering, pause and cancel for a live task", () => {
    const html = renderCard();
    for (const text of ["汇总销售额", "进行中", "2 轮", "发送指令", "暂停", "取消任务", "读取明细"]) {
      expect(html).toContain(text);
    }
    expect(html).not.toContain("继续任务");
  });

  it.each(["paused", "interrupted", "error", "completed"] as const)("can continue a %s run", (status) => {
    const html = renderCard({ status });
    expect(html).toContain("继续任务");
    expect(html).not.toContain("取消任务");
    expect(html).not.toContain("发送指令");
  });

  it("shows a multi-select question with a free-text answer", () => {
    const html = renderCard({ status: "waiting_input", pending_question: {
      question_id: "q1", header: "统计范围", text: "选择需要统计的区域", multi_select: true,
      options: [
        { label: "华东", value: "east", description: "江浙沪", is_other: false },
        { label: "华南", value: "south", description: "粤桂琼", is_other: false },
      ],
    } });
    expect(html).toContain("等待回答");
    expect(html).toContain("选择需要统计的区域");
    expect(html).toContain('type="checkbox"');
    expect(html).toContain("填写回答");
    expect(html).toContain("提交回答");
  });

  it("exposes result text and every committed file after an interrupted execution", () => {
    const html = renderCard({ status: "paused", changed_files: ["./sales.xlsx"], result: {
      stop_reason: "aborted", output: "已完成区域汇总", diagnostic: null, iterations: 2, tool_calls_count: 3,
      structured_changes: [{ path: "./sales.xlsx", tool_name: "edit_spreadsheet", change_type: "write", sheets_affected: [] }],
      observed_files: [],
    } });
    expect(html).toContain("已完成区域汇总");
    expect(html).toContain("已保留对话和完成的文件改动");
    expect(html.match(/title="\.\/sales.xlsx"/g)).toHaveLength(1);
  });

  it.each(["paused", "interrupted", "waiting_input", "queued"] as const)("does not call %s completed or failed in the chat card", (status) => {
    const html = renderToStaticMarkup(React.createElement(SubagentBlock, {
      name: "explorer", reason: "统计", iterations: 1, toolCalls: 1, status: "done", success: false,
      background: true, runStatus: status,
    }));
    expect(html).toContain("后台");
    expect(html).not.toContain("已完成");
    expect(html).not.toContain(">失败<");
    expect(html).not.toContain("子代理执行失败");
  });
});

describe("background task API", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("loads tasks for the explicit session, including after SSE has ended", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ runs: [run] })));
    vi.stubGlobal("fetch", fetchMock);
    expect(await fetchSubagentRuns("session/a")).toEqual([run]);
    expect(fetchMock.mock.calls[0][0]).toBe("/api/v1/sessions/session%2Fa/subagents");
  });

  it("returns the new identity from resume and submits the exact message once", async () => {
    const resumed = { ...run, run_id: "run-2", resumed_from: "run-1" };
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ run: resumed })));
    vi.stubGlobal("fetch", fetchMock);
    expect(await controlSubagentRun("session/a", "run/1", "resume", "补齐第四季度")).toEqual(resumed);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0][0]).toBe("/api/v1/sessions/session%2Fa/subagents/run%2F1");
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({ action: "resume", message: "补齐第四季度" });
  });

  it("surfaces a failed control request without replaying it", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: "子任务已结束" }), { status: 409 }));
    vi.stubGlobal("fetch", fetchMock);
    await expect(controlSubagentRun("s", "r", "send", "改为华东")).rejects.toThrow("子任务已结束");
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});

describe("background task file refresh", () => {
  it("refreshes completed writes once, including files committed before pause", () => {
    const paused = { ...run, status: "paused" as const, finished_at: 3, changed_files: ["./sales.xlsx"] };
    expect(completedSubagentFiles([run], [paused])).toEqual(["./sales.xlsx"]);
    expect(completedSubagentFiles([], [paused])).toEqual(["./sales.xlsx"]);
    expect(completedSubagentFiles([paused], [{ ...paused }])).toEqual([]);
    expect(completedSubagentFiles([], [{ ...run, changed_files: ["./sales.xlsx"] }])).toEqual([]);
  });
});

it("stopping the main generation leaves its background card running", () => {
  const previous = useChatStore.getState();
  const controller = new AbortController();
  try {
    useChatStore.setState({ loadedSessionId: null, isStreaming: true, abortController: controller, saveCurrentSession: vi.fn() });
    useChatStore.getState().setMessages([{ id: "stop-test", role: "assistant", timestamp: 1, blocks: [{
      type: "subagent", name: "explorer", reason: "统计", conversationId: "run-1", background: true,
      status: "running", iterations: 1, toolCalls: 0, tools: [],
    }] }]);
    stopGeneration();
    expect(controller.signal.aborted).toBe(true);
    expect(useChatStore.getState().isStreaming).toBe(false);
    const message = useChatStore.getState().messages[0];
    expect(message.role === "assistant" && message.blocks[0]).toMatchObject({ background: true, status: "running" });
  } finally {
    useChatStore.setState(previous, true);
  }
});
