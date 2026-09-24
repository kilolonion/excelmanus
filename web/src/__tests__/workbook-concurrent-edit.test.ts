import { afterEach, describe, expect, it, vi } from "vitest";
import {
  discardWorkbookEdits, enqueueExcelCellEdit, enqueueWorkbookCommand, flushWorkbookEdits,
  persistExcelCellEdits, resetExcelCellEditStateForTests, setPersistExcelCellEditsForTests, workbookEditDraft,
} from "@/lib/excel-cell-edit";
import { useExcelStore } from "@/stores/excel-store";

const file = { relative: "book.xlsx", workspaceKey: "id:ws", workspaceId: "ws" };
const edit = (cell: string, expectedVersion: string | null = "v1") => enqueueExcelCellEdit({
  path: file.relative, file, sessionId: "s1", cell, value: cell, expectedVersion,
});

afterEach(() => resetExcelCellEditStateForTests());

describe("concurrent workbook editing", () => {
  it("keeps failed, waiting and debounced edits available for export until explicit discard", async () => {
    let release!: () => void;
    const persist = vi.fn(async () => {
      await new Promise<void>((resolve) => { release = resolve; });
      return { kind: "conflict" as const, code: "VERSION_CONFLICT" };
    });
    setPersistExcelCellEditsForTests(persist);
    edit("A1");
    const first = flushWorkbookEdits(file);
    await vi.waitFor(() => expect(persist).toHaveBeenCalledTimes(1));
    edit("B1");
    const second = flushWorkbookEdits(file);
    edit("C1");
    release();
    await Promise.all([first, second]);
    await flushWorkbookEdits(file);
    const draft = JSON.parse(workbookEditDraft(file));
    expect(draft.batches.flatMap((b: { operations: { cells: { cell: string }[] }[] }) => b.operations.flatMap((op) => op.cells.map((c) => c.cell))))
      .toEqual(["A1", "B1", "C1"]);
    expect(persist).toHaveBeenCalledTimes(1);
    discardWorkbookEdits(file);
    expect(JSON.parse(workbookEditDraft(file)).batches).toEqual([]);
  });

  it("chains only acknowledged local versions across older and refreshed views", async () => {
    let n = 1;
    const persist = vi.fn(async () => ({ kind: "ok" as const, contentVersion: `v${++n}` }));
    setPersistExcelCellEditsForTests(persist);
    edit("A1", "v1");
    await flushWorkbookEdits(file);
    edit("B1", "v2");
    await flushWorkbookEdits(file);
    useExcelStore.getState().setContentVersion(file.relative, "remote-v99", file.workspaceKey);
    edit("C1", "v1");
    await flushWorkbookEdits(file);
    expect(persist).toHaveBeenNthCalledWith(3, expect.objectContaining({ expectedVersion: "v3" }), undefined);
    expect(JSON.parse(workbookEditDraft(file)).batches).toEqual([]);
  });

  it("does not replace a missing view version with a remote store version", async () => {
    const write = vi.fn();
    const result = await persistExcelCellEdits({path: file.relative, workspaceKey: file.workspaceKey,
      workspaceId: "ws", changes: [{cell:"A1",value:1}], expectedVersion: null}, {
      applyWorkbookChanges: write, getExpectedVersion: () => "remote-v99",
    });
    expect(result.kind).toBe("conflict");
    expect(write).not.toHaveBeenCalled();
  });

  it("preserves cell/structural/cell order inside a debounced batch", async () => {
    const persist = vi.fn(async () => ({ kind: "error" as const, message: "offline" }));
    setPersistExcelCellEditsForTests(persist);
    edit("A1");
    enqueueWorkbookCommand({path:file.relative,file,expectedVersion:"v1",operations:[{kind:"insert",axis:"row",at:1,count:1}]});
    edit("A2");
    await flushWorkbookEdits(file);
    const ops = JSON.parse(workbookEditDraft(file)).batches[0].operations;
    expect(ops.map((op: {kind:string}) => op.kind)).toEqual(["cells.patch","insert","cells.patch"]);
    expect(ops[0].cells[0].cell).toBe("A1");
    expect(ops[2].cells[0].cell).toBe("A2");
  });

  it("isolates drafts for the same relative path in different workspaces", async () => {
    setPersistExcelCellEditsForTests(async () => ({kind:"conflict",code:"VERSION_CONFLICT"}));
    edit("A1");
    await flushWorkbookEdits(file);
    expect(JSON.parse(workbookEditDraft({...file,workspaceKey:"id:other"})).batches).toEqual([]);
    discardWorkbookEdits({...file,workspaceKey:"id:other"});
    expect(JSON.parse(workbookEditDraft(file)).batches).toHaveLength(1);
  });
});

it.each(["remote-v3", undefined])("a late local save response preserves a remote notification (%s)", async (remoteVersion) => {
  useExcelStore.getState().notifyWorkbookChanged(file.relative, file.workspaceKey, "v1", "local");
  const setVersion = vi.fn();
  const result = await persistExcelCellEdits({path:file.relative,workspaceKey:file.workspaceKey,workspaceId:"ws",
    changes:[{cell:"A1",value:1}],expectedVersion:"v1"}, {
    applyWorkbookChanges: async () => {
      useExcelStore.getState().notifyWorkbookChanged(file.relative, file.workspaceKey, remoteVersion, "remote");
      return {status:"success",cells_written:1,content_version:"v2"};
    },
    setContentVersion: setVersion,
    invalidateCaches: vi.fn(),
  });
  expect(result).toEqual({kind:"ok",contentVersion:"v2"});
  expect(setVersion).not.toHaveBeenCalled();
  expect(useExcelStore.getState().getContentVersion(file.relative,file.workspaceKey)).toBe(remoteVersion ?? null);
});
