import { afterEach, beforeEach, expect, it } from "vitest";
import { prepareWorkbookMessage } from "@/lib/workbook-conversation";
import { enqueueExcelCellEdit, resetExcelCellEditStateForTests, setPersistExcelCellEditsForTests } from "@/lib/excel-cell-edit";
import { useSessionStore } from "@/stores/session-store";
import { useExcelStore } from "@/stores/excel-store";
import { useWorkbookConversationStore } from "@/stores/workbook-conversation-store";

const file = { relative: "uploads/book.xlsx", workspaceKey: "id:ws", workspaceId: "ws" };

beforeEach(() => {
  useSessionStore.setState({ activeSessionId: "s1", sessions: [{ id: "s1", workspaceId: "ws", title: "Book", messageCount: 0, inFlight: false }] });
  useWorkbookConversationStore.setState({ targets: {}, views: {} });
  const state = useWorkbookConversationStore.getState();
  state.bind("s1", file, "Sheet1");
  state.observe("s1", file, { status: "ready", sheet: "Sheet1", version: "sha256:aaaa" });
});
afterEach(() => resetExcelCellEditStateForTests());

it("waits for local edits and sends their acknowledged version even before the grid reloads", async () => {
  setPersistExcelCellEditsForTests(async () => ({ kind: "ok", contentVersion: "sha256:bbbb" }));
  enqueueExcelCellEdit({ path: file.relative, file, sessionId: "s1", sheet: "Sheet1", cell: "A2", value: 42, expectedVersion: "sha256:aaaa" });
  const message = await prepareWorkbookMessage("分析刚改的数字", "s1");
  expect(message).toContain("@file:uploads/book.xlsx@sha256:bbbb");
});

it("does not replace the viewed version with an unseen remote notification", async () => {
  useExcelStore.getState().notifyWorkbookChanged(file.relative, file.workspaceKey, "sha256:cccc", "remote");
  const message = await prepareWorkbookMessage("分析这张表", "s1");
  expect(message).toContain("@file:uploads/book.xlsx@sha256:aaaa");
  expect(message).not.toContain("sha256:cccc");
});
