import { describe, expect, it } from "vitest";
import {
  rememberFullViewTarget,
  resolveWorkspaceSurface,
  workspaceKeepAliveLayerClass,
} from "@/lib/workspace-surface";

describe("resolveWorkspaceSurface", () => {
  it("prefers word, then compare, then excel, then chat", () => {
    expect(
      resolveWorkspaceSurface({
        wordFullViewPath: "a.docx",
        compareMode: true,
        fullViewPath: "a.xlsx",
      }),
    ).toBe("word");
    expect(
      resolveWorkspaceSurface({
        wordFullViewPath: null,
        compareMode: true,
        fullViewPath: "a.xlsx",
      }),
    ).toBe("compare");
    expect(
      resolveWorkspaceSurface({
        wordFullViewPath: null,
        compareMode: false,
        fullViewPath: "a.xlsx",
      }),
    ).toBe("excel");
    expect(
      resolveWorkspaceSurface({
        wordFullViewPath: null,
        compareMode: false,
        fullViewPath: null,
      }),
    ).toBe("chat");
  });
});

describe("rememberFullViewTarget", () => {
  it("captures the live full-view target", () => {
    expect(
      rememberFullViewTarget(
        { path: "./a.xlsx", sheet: "明细" },
        { path: "./old.xlsx", sheet: "旧" },
      ),
    ).toEqual({ path: "./a.xlsx", sheet: "明细" });
  });

  it("keeps the last workbook when the full view is hidden", () => {
    expect(
      rememberFullViewTarget(
        { path: null, sheet: null },
        { path: "./a.xlsx", sheet: "明细" },
      ),
    ).toEqual({ path: "./a.xlsx", sheet: "明细" });
  });

  it("returns null before any workbook has been opened", () => {
    expect(rememberFullViewTarget({ path: null, sheet: null }, null)).toBeNull();
  });

  it("does not keep the last workbook after switching workspace", () => {
    expect(
      rememberFullViewTarget(
        { path: null, sheet: null, workspaceKey: "id:b" },
        { path: "./a.xlsx", sheet: "明细", workspaceKey: "id:a" },
      ),
    ).toBeNull();
  });
});

describe("workspaceKeepAliveLayerClass", () => {
  it("keeps inactive layers in-layout so Univer retains its size", () => {
    expect(workspaceKeepAliveLayerClass(true)).toContain("relative");
    expect(workspaceKeepAliveLayerClass(false)).toContain("invisible");
    expect(workspaceKeepAliveLayerClass(false)).toContain("absolute inset-0");
    expect(workspaceKeepAliveLayerClass(false)).not.toContain("hidden");
  });
});
