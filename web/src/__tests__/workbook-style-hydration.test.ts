import { describe, expect, it } from "vitest";
import { ObjectMatrix, Styles, type ICellData, type IAccessor, type IStyleData, type IObjectMatrixPrimitiveType } from "@univerjs/core";
import { SetRangeValuesMutation } from "@univerjs/sheets";
import { windowCellPatch, type WorkbookRegion } from "@/lib/workbook-observation";

describe("restored styles through the installed Univer mutation", () => {
  it.each([true, false])("replaces old formatting, including removed border sides (style ID: %s)", (useId) => {
    const oldStyle = { bl: 1, bg: { rgb: "#155A8A" }, bd: { t: { s: 1, cl: { rgb: "#000000" } }, b: { s: 7, cl: { rgb: "#000000" } } } } satisfies IStyleData;
    const styles = new Styles({ old: oldStyle });
    const previous = { 0: { 0: { v: "old", s: useId ? "old" : oldStyle } } };
    const cells = new ObjectMatrix<ICellData>(previous);
    const win: WorkbookRegion = {
      sheet: "Receipt", rect: { r0: 1, c0: 1, r1: 1, c1: 1 },
      cells: { "1,1": { t: "s", v: "restored", cached: "yes", s: { fs: 11, bd: { b: { s: 1 } } } } },
    };
    const accessor = { get: () => ({ getUnit: () => ({
      getSheetBySheetId: () => ({ getCellMatrix: () => cells }),
      getStyles: () => styles,
    }) }) } as unknown as IAccessor;
    SetRangeValuesMutation.handler(accessor, {
      unitId: "book", subUnitId: "receipt",
      cellValue: windowCellPatch(win, previous, styles.toJSON()) as IObjectMatrixPrimitiveType<ICellData | null>,
    });
    const restored = cells.getValue(0, 0)!;
    expect(restored.v).toBe("restored");
    expect(styles.getStyleByCell(restored)).toEqual({ fs: 11, bd: { b: { s: 1 } } });
    expect(oldStyle.bg.rgb).toBe("#155A8A");
  });
});
