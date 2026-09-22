// @vitest-environment jsdom
import React from "react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { WorkbookWorkflowDialogs } from "@/components/excel/WorkbookWorkflowDialogs";
import { useWorkbookWorkflowStore as workflows } from "@/stores/workbook-workflow-store";
import { useSessionStore } from "@/stores/session-store";
import { sendWorkbookHandoff } from "@/lib/workbook-handoff";
import { captureWorkbookDraft, reviewWorkbookMerge } from "@/lib/workbook-merge";

vi.mock("@/lib/workbook-handoff", () => ({ sendWorkbookHandoff: vi.fn(), WORKBOOK_OPERATION_LABELS: { chart: "图表" } }));
vi.mock("@/lib/workbook-merge", () => ({ captureWorkbookDraft: vi.fn(), reviewWorkbookMerge: vi.fn() }));
const scope = { file: { relative: "book.xlsx", workspaceId: "w1", workspaceKey: "id:w1" }, sessionId: "s1" };
const review = { status: "review" as const, content_version: "v2", safe_count: 1, conflict_count: 1,
  cells: [{ id: "a1", sheet: "Sheet1", cell: "A1", field: "内容", base: 1, local: 10, remote: 30, conflict: true }] };

beforeEach(() => {
  vi.resetAllMocks();
  useSessionStore.setState({ activeSessionId: "s1" });
  workflows.setState({ handoff: null, conflict: null });
  vi.mocked(captureWorkbookDraft).mockResolvedValue('{"batches":[]}');
  vi.mocked(reviewWorkbookMerge).mockResolvedValue(review);
  vi.mocked(sendWorkbookHandoff).mockResolvedValue(true);
});
afterEach(cleanup);

it("collects chart fields and sends the captured workbook and range without filling the chat composer", async () => {
  workflows.getState().openHandoff({ ...scope, operation: "chart", sheet: "Sheet1", range: "C1:D8", version: "v1", parameters: { captured: { title: "Sales" } } });
  render(<WorkbookWorkflowDialogs />);
  fireEvent.change(screen.getByLabelText("图表类型"), { target: { value: "柱状图" } });
  fireEvent.change(screen.getByLabelText("数据列"), { target: { value: "销售额" } });
  fireEvent.click(screen.getByRole("button", { name: "让 Agent 生成方案" }));
  await waitFor(() => expect(sendWorkbookHandoff).toHaveBeenCalledWith(expect.objectContaining({ ...scope, range: "C1:D8", version: "v1" }), "",
    { captured: { title: "Sales" }, requested: { chart_type: "柱状图", series: "销售额" } }));
  await waitFor(() => expect(workflows.getState().handoff).toBeNull());
});

it("requires conflict choices before saving and passes the reviewed version", async () => {
  workflows.getState().openConflict(scope);
  render(<WorkbookWorkflowDialogs />);
  const merge = await screen.findByRole("button", { name: "确认合并并保存" });
  expect((merge as HTMLButtonElement).disabled).toBe(true);
  fireEvent.change(screen.getByRole("combobox"), { target: { value: "local" } });
  expect((merge as HTMLButtonElement).disabled).toBe(false);
  vi.mocked(reviewWorkbookMerge).mockResolvedValue({ status: "merged", content_version: "v3" });
  fireEvent.click(merge);
  await waitFor(() => expect(reviewWorkbookMerge).toHaveBeenLastCalledWith(expect.objectContaining(scope), '{"batches":[]}',
    expect.objectContaining({ version: "v2", choices: { a1: "local" } })));
});

it("keeps the dialog and draft on a second conflict and requires a new review", async () => {
  workflows.getState().openConflict(scope);
  render(<WorkbookWorkflowDialogs />);
  await screen.findByRole("combobox");
  fireEvent.change(screen.getByRole("combobox"), { target: { value: "remote" } });
  vi.mocked(reviewWorkbookMerge).mockRejectedValue(new Error("文件再次变化，请重新核对冲突"));
  fireEvent.click(screen.getByRole("button", { name: "确认合并并保存" }));
  expect(await screen.findByRole("alert")).toHaveProperty("textContent", "文件再次变化，请重新核对冲突");
  expect(screen.queryByRole("button", { name: "确认合并并保存" })).toBeNull();
  expect(workflows.getState().conflict).not.toBeNull();
});

it("passes the unsaved draft and comparison to Agent replanning", async () => {
  workflows.getState().openConflict(scope);
  render(<WorkbookWorkflowDialogs />);
  await screen.findByRole("combobox");
  fireEvent.click(screen.getByRole("button", { name: "让 Agent 重新规划" }));
  await waitFor(() => expect(sendWorkbookHandoff).toHaveBeenCalledWith(expect.objectContaining({ ...scope, operation: "conflict-replan", version: "v2" }),
    expect.any(String), expect.objectContaining({ draft: { batches: [] }, comparison: review.cells, current_version: "v2" })));
});
