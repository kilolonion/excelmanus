import type { ViewRect, WorkbookObservation } from "@/lib/workbook-observation";
import { cellRefFromIndex } from "@/lib/excel-cell-edit";

/**
 * Small, style-free opening window used by every workbook entry point.
 *
 * Keeping this value in one module is important: a hover prefetch and the
 * editor's first request can then share the same in-flight/cache entry instead
 * of reading two different rectangles before the grid is even visible.
 */
export const INITIAL_WORKBOOK_VIEW_RECT = "A1:Z80";

export function rangeIsLoaded(view: WorkbookObservation, sheet: string, rect: ViewRect): boolean {
  return firstUnloadedCell(view, sheet, rect) === null;
}

export function firstUnloadedCell(view: WorkbookObservation, sheet: string, rect: ViewRect): { row: number; col: number } | null {
  const remaining = uncoveredRects(rect, view.coverage.loaded.filter((r) => r.sheet === sheet));
  return remaining.length ? { row: remaining[0].r0 - 1, col: remaining[0].c0 - 1 } : null;
}

function uncoveredRects(rect: ViewRect, loaded: ViewRect[]): ViewRect[] {
  let remaining = [rect];
  for (const cover of loaded) {
    remaining = remaining.flatMap((r) => {
      const r0 = Math.max(r.r0, cover.r0), r1 = Math.min(r.r1, cover.r1);
      const c0 = Math.max(r.c0, cover.c0), c1 = Math.min(r.c1, cover.c1);
      if (r0 > r1 || c0 > c1) return [r];
      return [
        { ...r, r1: r0 - 1 }, { ...r, r0: r1 + 1 },
        { r0, r1, c0: r.c0, c1: c0 - 1 }, { r0, r1, c0: c1 + 1, c1: r.c1 },
      ].filter((p) => p.r0 <= p.r1 && p.c0 <= p.c1);
    });
  }
  return remaining;
}

export function pageForCell(row: number, col: number): { rect: ViewRect; address: string } {
  const r0 = Math.floor(row / 200) * 200;
  const c0 = Math.floor(col / 50) * 50;
  const r1 = Math.min(r0 + 199, 1048575), c1 = Math.min(c0 + 49, 16383);
  return { rect: { r0: r0 + 1, c0: c0 + 1, r1: r1 + 1, c1: c1 + 1 },
    address: `${cellRefFromIndex(r0, c0)}:${cellRefFromIndex(r1, c1)}` };
}

export function mergeViewWindows(current: WorkbookObservation, next: WorkbookObservation): WorkbookObservation {
  if (current.content_version !== next.content_version || current.file.workspaceKey !== next.file.workspaceKey || current.file.relative !== next.file.relative) {
    throw new Error("STALE_VIEW: 不能合并不同文件或版本的范围");
  }
  const scoped = (view: WorkbookObservation) => view.regions.map((win) => ({ ...win,
    coverage: win.coverage ?? { presentation: { status: view.request?.facets.includes("presentation") ? "complete" : "not_requested" } },
  }));
  let windows = scoped(current);
  for (const win of scoped(next)) {
    // Refresh and the style pass replace a matching page even if its values
    // were already loaded. Old versions are rejected above, never relabelled.
    windows = windows.filter((old) => !(old.sheet === win.sheet
      && old.rect.r0 === win.rect.r0 && old.rect.c0 === win.rect.c0
      && old.rect.r1 === win.rect.r1 && old.rect.c1 === win.rect.c1));
    windows.push(win);
  }
  const loaded = windows.map((win) => ({ ...win.rect, sheet: win.sheet }));
  return { ...next, regions: windows, coverage: { ...next.coverage, loaded,
    unloaded: next.sheets.flatMap((s) => uncoveredRects(
      { sheet: s.name, r0: 1, c0: 1, r1: s.used.rows, c1: s.used.cols },
      loaded.filter((r) => r.sheet === s.name),
    )).filter((r) => r.r1 >= r.r0 && r.c1 >= r.c0),
  } };
}

/** Include every page intersecting the visible rectangle, including boundaries. */
export function pagesForViewport(range: { startRow: number; endRow: number; startColumn: number; endColumn: number }) {
  const pages: ReturnType<typeof pageForCell>[] = [];
  for (let r = Math.floor(range.startRow / 200) * 200; r <= range.endRow; r += 200) {
    for (let c = Math.floor(range.startColumn / 50) * 50; c <= range.endColumn; c += 50) {
      pages.push(pageForCell(r, c));
    }
  }
  return pages;
}


/** A data-only prefetch in another window cannot revoke already loaded styling. */
export function rangeHasPresentation(view: WorkbookObservation, sheet: string, rect: ViewRect): boolean {
  const loaded = view.regions.filter((r) => r.sheet === sheet &&
    (r.coverage ? ["complete", "partial"].includes(r.coverage.presentation?.status) : view.request?.facets.includes("presentation")));
  return uncoveredRects(rect, loaded.map((r) => r.rect)).length === 0;
}
