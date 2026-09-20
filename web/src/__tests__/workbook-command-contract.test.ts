import { describe, expect, it } from "vitest";
import { extractWorkbookOpsFromMutation } from "@/lib/excel-cell-edit";
import { firstUnloadedCell, mergeViewWindows, pageForCell, pagesForViewport, rangeIsLoaded } from "@/lib/workbook-window";
import { demoWorkbookView, viewSnapshotToUniver, windowCellPatch } from "@/lib/workbook-view";

describe("installed Univer command contracts", () => {
  const ranges = [{ startRow: 0, endRow: 0, startColumn: 0, endColumn: 1 }];
  it("persists dedicated column width and row height mutations", () => {
    expect(extractWorkbookOpsFromMutation({id:"sheet.mutation.set-worksheet-col-width",sheet:"Second",params:{ranges,colWidth:120}}))
      .toEqual([{op:"set_dims",sheet:"Second",columns:{A:16,B:16},rows:{}}]);
    expect(extractWorkbookOpsFromMutation({id:"sheet.mutation.set-worksheet-row-height",sheet:"Second",params:{ranges,rowHeight:40}}))
      .toEqual([{op:"set_dims",sheet:"Second",columns:{},rows:{"1":30}}]);
  });
  it("maps merge ranges and insert ranges without guessing indices", () => {
    expect(extractWorkbookOpsFromMutation({id:"sheet.mutation.add-worksheet-merge",sheet:"Second",params:{ranges}}))
      .toEqual([{op:"merge",sheet:"Second",range:"A1:B1"}]);
    expect(extractWorkbookOpsFromMutation({id:"sheet.mutation.insert-row",sheet:"Second",params:{range:{...ranges[0],startRow:10,endRow:12}}}))
      .toEqual([{op:"insert_axis",sheet:"Second",axis:"row",index:11,count:3}]);
  });
  it("distinguishes style clearing from value clearing", () => {
    expect(extractWorkbookOpsFromMutation({id:"sheet.mutation.set-range-values",sheet:"S",params:{cellValue:{0:{0:{s:null},1:{v:null}}}}}))
      .toEqual([{op:"set_values",sheet:"S",cells:[{cell:"B1",value:null}]},{op:"set_styles",sheet:"S",cells:[{cell:"A1",style:null}]}]);
  });
});

describe("version-bound range loading", () => {
  it("keeps unopened sheets sparse, without synthesizing unloaded cells", () => {
    const view = demoWorkbookView("book.xlsx");
    view.sheets.push({ name: "Huge", sheet_id: "Huge", used: { rows: 1000000, cols: 100 } });
    view.coverage.unloaded = [{ sheet: "Huge", r0: 1, c0: 1, r1: 1000000, c1: 100 }];
    const mapped = viewSnapshotToUniver(view, "id") as { sheets: Record<string, { cellData: unknown; rowCount: number }> };
    expect(mapped.sheets["sheet-Huge"].cellData).toEqual({});
    expect(mapped.sheets["sheet-Huge"].rowCount).toBe(1000000);
  });

  it("replaces loaded windows when styles arrive and explicitly clears deleted cells", () => {
    const view = demoWorkbookView("book.xlsx");
    const next = structuredClone(view);
    next.windows[0].cells = { "1,1": { t: "n", v: 9, cached: "yes", s: { bl: 1 } } };
    const merged = mergeViewWindows(view, next);
    expect(merged.windows).toHaveLength(1);
    expect(merged.windows[0].cells["1,1"].s).toEqual({ bl: 1 });
    const patch = windowCellPatch(next.windows[0], { "0": { "0": { f: "=2" }, "1": { v: "deleted" } }, "500": { "0": { v: "outside" } } });
    expect(patch[0][0]).toMatchObject({ v: 9, f: null, s: { bl: 1 }, p: null });
    expect(patch[0][1]).toBeNull();
    expect(patch[500]).toBeUndefined();
  });

  it("loads both sides of a viewport straddling row and column boundaries", () => {
    expect(pagesForViewport({ startRow: 195, endRow: 220, startColumn: 48, endColumn: 55 }).map((p) => p.address))
      .toEqual(["A1:AX200", "AY1:CV200", "A201:AX400", "AY201:CV400"]);
  });
  it("combines adjacent windows and locates holes", () => {
    const view = demoWorkbookView("book.xlsx");
    view.coverage.loaded = [{sheet:"Sheet1",r0:1,c0:1,r1:200,c1:50},{sheet:"Sheet1",r0:201,c0:1,r1:400,c1:50}];
    expect(rangeIsLoaded(view,"Sheet1",{r0:100,c0:1,r1:300,c1:30})).toBe(true);
    expect(firstUnloadedCell(view,"Sheet1",{r0:401,c0:1,r1:420,c1:10})).toEqual({row:400,col:0});
    expect(pageForCell(450,60).address).toBe("AY401:CV600");
  });
  it("does not merge a different scope or version", () => {
    const view=demoWorkbookView("book.xlsx");
    expect(() => mergeViewWindows(view,{...view,content_version:"sha256:other"})).toThrow("STALE_VIEW");
    expect(() => mergeViewWindows(view,{...view,file:{...view.file,workspaceKey:"other"}})).toThrow("STALE_VIEW");
  });
});
