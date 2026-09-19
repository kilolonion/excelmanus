import { beforeEach, describe, expect, it, vi } from "vitest";

const prefetchWorkbookView = vi.fn();
const warmUniverModules = vi.fn();

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    prefetchWorkbookView: (...args: unknown[]) => prefetchWorkbookView(...args),
  };
});

vi.mock("@/lib/univer-modules", () => ({
  warmUniverModules: () => warmUniverModules(),
}));

import { prefetchExcelView } from "@/lib/excel-view-prefetch";

describe("prefetchExcelView", () => {
  beforeEach(() => {
    prefetchWorkbookView.mockReset();
    warmUniverModules.mockReset();
  });

  it("always warms Univer modules", () => {
    prefetchExcelView();
    expect(warmUniverModules).toHaveBeenCalledOnce();
    expect(prefetchWorkbookView).not.toHaveBeenCalled();
  });

  it("prefetches the workbook view when a path is known", () => {
    prefetchExcelView("./a.xlsx");
    expect(warmUniverModules).toHaveBeenCalledOnce();
    expect(prefetchWorkbookView).toHaveBeenCalledWith(
      expect.objectContaining({
        path: "a.xlsx",
        workspaceKey: "_",
      }),
    );
  });
});
