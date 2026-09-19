import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  SET_RANGE_VALUES_MUTATION_ID,
  enqueueExcelCellEdit,
  extractCellEditsFromSheetValueChanged,
  flushExcelCellEditsForTests,
  isExcelWriteConflict,
  persistExcelCellEdits,
  resetExcelCellEditStateForTests,
  setPersistExcelCellEditsForTests,
  type PersistExcelCellEditsDeps,
} from "@/lib/excel-cell-edit";
import type { ExcelWriteResponse } from "@/lib/api";

function rangeStub(opts: {
  sheet: string;
  startRow: number;
  startColumn: number;
  endRow: number;
  endColumn: number;
  values?: unknown[][];
  cellDatas?: Array<Array<{ v?: unknown; f?: string } | null>>;
}) {
  return {
    getSheetName: () => opts.sheet,
    getRange: () => ({
      startRow: opts.startRow,
      startColumn: opts.startColumn,
      endRow: opts.endRow,
      endColumn: opts.endColumn,
    }),
    getValues: () => opts.values ?? [],
    getCellDatas: () => opts.cellDatas ?? [],
  };
}

function mockDeps(writeImpl: (opts: unknown) => Promise<ExcelWriteResponse>) {
  const setContentVersion = vi.fn();
  const invalidate = vi.fn();
  const deps: PersistExcelCellEditsDeps = {
    writeExcelCells: vi.fn(writeImpl),
    getSessionId: () => "sess-active",
    getExpectedVersion: vi.fn(() => "sha256:current"),
    setContentVersion,
    invalidateCaches: invalidate,
  };
  return { deps, setContentVersion, invalidate };
}

describe("extractCellEditsFromSheetValueChanged", () => {
  it("extracts A1 from set-range-values payload", () => {
    const edits = extractCellEditsFromSheetValueChanged({
      payload: {
        id: SET_RANGE_VALUES_MUTATION_ID,
        params: {
          cellValue: { "0": { "1": { v: "hello" } } },
        },
      },
      effectedRanges: [
        rangeStub({ sheet: "销售", startRow: 0, startColumn: 1, endRow: 0, endColumn: 1 }),
      ],
    });
    expect(edits).toEqual([{ sheet: "销售", cell: "B1", value: "hello" }]);
  });

  it("writes formulas with leading =", () => {
    const edits = extractCellEditsFromSheetValueChanged({
      payload: {
        id: SET_RANGE_VALUES_MUTATION_ID,
        params: { cellValue: { "1": { "0": { f: "A1+1" } } } },
      },
      effectedRanges: [
        rangeStub({ sheet: "Sheet1", startRow: 1, startColumn: 0, endRow: 1, endColumn: 0 }),
      ],
    });
    expect(edits[0]).toMatchObject({ cell: "A2", value: "=A1+1" });
  });

  it("skips formula calculation updates that only change v", () => {
    const edits = extractCellEditsFromSheetValueChanged({
      payload: {
        id: SET_RANGE_VALUES_MUTATION_ID,
        params: { cellValue: { "0": { "0": { v: 99 } } } },
      },
      effectedRanges: [
        rangeStub({
          sheet: "Sheet1",
          startRow: 0,
          startColumn: 0,
          endRow: 0,
          endColumn: 0,
          cellDatas: [[{ v: 99, f: "=A2" }]],
        }),
      ],
    });
    expect(edits).toEqual([]);
  });

  it("skips merge/style mutations", () => {
    const edits = extractCellEditsFromSheetValueChanged({
      payload: { id: "sheet.mutation.add-worksheet-merge", params: { cellValue: { "0": { "0": { v: 1 } } } } },
      effectedRanges: [rangeStub({ sheet: "Sheet1", startRow: 0, startColumn: 0, endRow: 0, endColumn: 0 })],
    });
    expect(edits).toEqual([]);
  });

  it("clears a cell when mutation value is null", () => {
    const edits = extractCellEditsFromSheetValueChanged({
      payload: {
        id: SET_RANGE_VALUES_MUTATION_ID,
        params: { cellValue: { "2": { "0": null } } },
      },
      effectedRanges: [
        rangeStub({ sheet: "Sheet1", startRow: 2, startColumn: 0, endRow: 2, endColumn: 0 }),
      ],
    });
    expect(edits).toEqual([{ sheet: "Sheet1", cell: "A3", value: null }]);
  });
});

describe("isExcelWriteConflict / persistExcelCellEdits", () => {
  afterEach(() => {
    resetExcelCellEditStateForTests();
  });

  it("detects conflict responses", () => {
    expect(isExcelWriteConflict({ status: "conflict", cells_written: 0, code: "VERSION_CONFLICT" })).toBe(true);
    expect(isExcelWriteConflict({ status: "success", cells_written: 1 })).toBe(false);
  });

  it("sends session id and expected version, then records content_version without treating it as a reload", async () => {
    const { deps, setContentVersion, invalidate } = mockDeps(async () => ({
      status: "success",
      cells_written: 1,
      content_version: "sha256:next",
    }));

    const result = await persistExcelCellEdits(
      { path: "./book.xlsx", sheet: "Sheet1", changes: [{ cell: "A1", value: 3 }], workspaceKey: "id:ws-a" },
      deps,
    );

    expect(result).toEqual({ kind: "ok", contentVersion: "sha256:next" });
    expect(deps.writeExcelCells).toHaveBeenCalledWith(
      expect.objectContaining({
        path: "./book.xlsx",
        sheet: "Sheet1",
        changes: [],
        operations: [{ op: "set_values", sheet: "Sheet1", cells: [{ cell: "A1", value: 3, style: undefined }] }],
        sessionId: "sess-active",
        expectedVersion: "sha256:current",
      }),
    );
    expect(setContentVersion).toHaveBeenCalledWith("./book.xlsx", "sha256:next", expect.anything());
    expect(invalidate).toHaveBeenCalledWith({ workspaceKey: "id:ws-a", relative: "./book.xlsx" });
  });

  it("returns conflict on VERSION_CONFLICT and does not update version", async () => {
    const { deps, setContentVersion } = mockDeps(async () => ({
      status: "conflict",
      cells_written: 0,
      code: "VERSION_CONFLICT",
    }));

    const result = await persistExcelCellEdits(
      { path: "./book.xlsx", changes: [{ cell: "A1", value: 1 }] },
      deps,
    );

    expect(result).toEqual({ kind: "conflict", code: "VERSION_CONFLICT" });
    expect(setContentVersion).not.toHaveBeenCalled();
  });

  it("refuses to write when snapshot version is missing", async () => {
    const { deps } = mockDeps(async () => ({ status: "success", cells_written: 1 }));
    deps.getExpectedVersion = vi.fn(() => null);
    const result = await persistExcelCellEdits(
      { path: "./book.xlsx", changes: [{ cell: "A1", value: 1 }] },
      deps,
    );
    expect(result).toEqual({ kind: "conflict", code: "VERSION_CONFLICT" });
    expect(deps.writeExcelCells).not.toHaveBeenCalled();
  });

  it("skips demo files", async () => {
    const { deps } = mockDeps(async () => ({ status: "success", cells_written: 1 }));
    const result = await persistExcelCellEdits(
      { path: "__demo__/示例销售数据.xlsx", changes: [{ cell: "A1", value: 1 }] },
      deps,
    );
    expect(result.kind).toBe("skipped");
    expect(deps.writeExcelCells).not.toHaveBeenCalled();
  });
});

describe("enqueueExcelCellEdit", () => {
  beforeEach(() => {
    resetExcelCellEditStateForTests();
  });
  afterEach(() => {
    resetExcelCellEditStateForTests();
  });

  it("batches same-file edits into one write", async () => {
    const persist = vi.fn(async () => ({ kind: "ok" as const, contentVersion: "sha256:batched" }));
    setPersistExcelCellEditsForTests(persist);

    enqueueExcelCellEdit({ path: "./book.xlsx", sheet: "Sheet1", cell: "A1", value: 1 });
    enqueueExcelCellEdit({ path: "./book.xlsx", sheet: "Sheet1", cell: "B1", value: 2 });
    await flushExcelCellEditsForTests("./book.xlsx");

    expect(persist).toHaveBeenCalledTimes(1);
    expect(persist).toHaveBeenCalledWith(
      expect.objectContaining({
        path: "./book.xlsx",
        sheet: "Sheet1",
        changes: [
          { cell: "A1", value: 1, sheet: "Sheet1" },
          { cell: "B1", value: 2, sheet: "Sheet1" },
        ],
      }),
      undefined,
    );
  });

  it("keeps same-address edits on different sheets", async () => {
    const persist = vi.fn(async () => ({ kind: "ok" as const, contentVersion: "sha256:sheets" }));
    setPersistExcelCellEditsForTests(persist);

    enqueueExcelCellEdit({ path: "./book.xlsx", sheet: "Sheet1", cell: "A1", value: "one" });
    enqueueExcelCellEdit({ path: "./book.xlsx", sheet: "Sheet2", cell: "A1", value: "two" });
    await flushExcelCellEditsForTests("./book.xlsx");

    expect(persist).toHaveBeenCalledTimes(1);
    expect(persist).toHaveBeenCalledWith(
      expect.objectContaining({
        path: "./book.xlsx",
        sheet: "Sheet1",
        changes: [
          { cell: "A1", value: "one", sheet: "Sheet1" },
          { cell: "A1", value: "two", sheet: "Sheet2" },
        ],
      }),
      undefined,
    );
  });

  it("pauses further writes after a version conflict", async () => {
    const persist = vi.fn(async () => ({ kind: "conflict" as const, code: "VERSION_CONFLICT" }));
    setPersistExcelCellEditsForTests(persist);
    const onConflict = vi.fn();

    enqueueExcelCellEdit({
      path: "./book.xlsx",
      cell: "A1",
      value: 1,
      onConflict,
    });
    await flushExcelCellEditsForTests("./book.xlsx");
    expect(onConflict).toHaveBeenCalledTimes(1);

    enqueueExcelCellEdit({ path: "./book.xlsx", cell: "A2", value: 2, onConflict });
    await flushExcelCellEditsForTests("./book.xlsx");
    expect(persist).toHaveBeenCalledTimes(1);
  });

  it("serializes overlapping flushes for the same path", async () => {
    let release!: () => void;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    let calls = 0;
    const persist = vi.fn(async () => {
      calls += 1;
      if (calls === 1) await gate;
      return { kind: "ok" as const, contentVersion: `sha256:${calls}` };
    });
    setPersistExcelCellEditsForTests(persist);

    enqueueExcelCellEdit({ path: "./book.xlsx", cell: "A1", value: 1 });
    const first = flushExcelCellEditsForTests("./book.xlsx");
    await vi.waitFor(() => expect(persist).toHaveBeenCalledTimes(1));

    enqueueExcelCellEdit({ path: "./book.xlsx", cell: "B1", value: 2 });
    const second = flushExcelCellEditsForTests("./book.xlsx");
    await Promise.resolve();
    expect(persist).toHaveBeenCalledTimes(1);

    release();
    await Promise.all([first, second]);
    expect(persist).toHaveBeenCalledTimes(2);
    const persistCalls = persist.mock.calls as unknown as Array<[unknown]>;
    expect(persistCalls[1]?.[0]).toMatchObject({
      changes: [{ cell: "B1", value: 2 }],
    });
  });
});
