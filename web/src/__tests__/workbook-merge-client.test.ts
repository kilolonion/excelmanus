import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { captureWorkbookDraft, reviewWorkbookMerge } from "@/lib/workbook-merge";
import { apiFetch } from "@/lib/api";
import { enqueueExcelCellEdit, flushWorkbookEdits, isWorkbookEditPaused, resetExcelCellEditStateForTests, setPersistExcelCellEditsForTests, workbookEditDraft } from "@/lib/excel-cell-edit";
import { useSessionStore } from "@/stores/session-store";
import { captureWorkbookParameters, workbookCommandRange } from "@/lib/workbook-handoff";

vi.mock("@/lib/api", async (importOriginal) => ({ ...await importOriginal<typeof import("@/lib/api")>(), apiFetch: vi.fn() }));
const scope = { file: { relative: "book.xlsx", workspaceId: "w1", workspaceKey: "id:w1" }, sessionId: "s1" };
const edit = () => enqueueExcelCellEdit({ path: "book.xlsx", file: scope.file, sessionId: "s1", sheet: "Sheet1", cell: "A1", value: 42, expectedVersion: "v1" });

beforeEach(() => {
  vi.clearAllMocks();
  useSessionStore.setState({ activeSessionId: "s1", sessions: [{ id: "s1", workspaceId: "w1", title: "Book", messageCount: 0, inFlight: false }] });
  setPersistExcelCellEditsForTests(async () => ({ kind: "conflict", code: "VERSION_CONFLICT" }));
});
afterEach(resetExcelCellEditStateForTests);

it("retains the exact draft on stale review and clears it only after a successful merge", async () => {
  edit(); await flushWorkbookEdits(scope.file);
  const draft = await captureWorkbookDraft(scope);
  vi.mocked(apiFetch).mockResolvedValue(new Response(JSON.stringify({ error: "文件再次变化" }), { status: 409 }));
  await expect(reviewWorkbookMerge(scope, draft, { version: "v2", choices: {}, operationId: "1" })).rejects.toThrow("文件再次变化");
  expect(workbookEditDraft(scope.file)).toBe(draft);
  expect(isWorkbookEditPaused(scope.file)).toBe(true);
  vi.mocked(apiFetch).mockResolvedValue(new Response(JSON.stringify({ status: "merged", content_version: "v3" })));
  await reviewWorkbookMerge(scope, draft, { version: "v2", choices: {}, operationId: "2" });
  expect(isWorkbookEditPaused(scope.file)).toBe(false);
  expect(JSON.parse(workbookEditDraft(scope.file)).batches).toEqual([]);
});

it("rejects stale session scope before making a merge request", async () => {
  edit(); await flushWorkbookEdits(scope.file);
  const draft = await captureWorkbookDraft(scope);
  useSessionStore.setState({ activeSessionId: "s2" });
  await expect(reviewWorkbookMerge(scope, draft)).rejects.toThrow("已切换对话");
  expect(apiFetch).not.toHaveBeenCalled();
  expect(workbookEditDraft(scope.file)).toBe(draft);
});

it("exports the acknowledged baseline of a failed queued save", async () => {
  setPersistExcelCellEditsForTests(vi.fn().mockResolvedValueOnce({ kind: "ok", contentVersion: "v2" }).mockResolvedValue({ kind: "conflict", code: "VERSION_CONFLICT" }));
  edit(); await flushWorkbookEdits(scope.file);
  edit(); await flushWorkbookEdits(scope.file);
  expect(JSON.parse(await captureWorkbookDraft(scope)).batches[0].expected_version).toBe("v2");
});

it("captures immutable command parameters and prefers the operation's explicit range", () => {
  const raw = { unitId: "engine", subUnitId: "sheet-id", criteria: { condition: ">100" }, range: { startRow: 2, endRow: 8, startColumn: 1, endColumn: 3 } };
  const captured = captureWorkbookParameters(raw);
  raw.criteria.condition = "changed";
  expect(captured).toMatchObject({ criteria: { condition: ">100" } });
  expect(captured.unitId).toBeUndefined();
  expect(workbookCommandRange(raw, "A1")).toBe("B3:D9");
  expect(() => captureWorkbookParameters({ large: "a".repeat(64001) })).toThrow("参数较多");
});
