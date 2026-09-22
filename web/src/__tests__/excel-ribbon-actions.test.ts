import { describe, expect, it } from "vitest";
import {
  buildGridAskPrompt,
  buildRibbonAskPrompt,
  buildSelectionAgentPrompt,
  nativeRibbonTabFromLabel,
  readNativeRibbonTab,
  ribbonAskActions,
  SELECTION_AGENT_ACTIONS,
  setHistoryRibbonMode,
  setRibbonToolbarHidden,
} from "@/lib/excel-ribbon-actions";

describe("nativeRibbonTabFromLabel", () => {
  it("maps Univer tab titles", () => {
    expect(nativeRibbonTabFromLabel("公式")).toBe("formula");
    expect(nativeRibbonTabFromLabel("数据")).toBe("data");
    expect(nativeRibbonTabFromLabel("开始")).toBe("home");
  });

  it("hides native tab when history is selected", () => {
    expect(nativeRibbonTabFromLabel("数据", true)).toBeNull();
  });

  it("readNativeRibbonTab ignores native selection while history is active", () => {
    const tablist = {
      querySelector(sel: string) {
        if (sel.includes("[data-em-ribbon=\"history\"]")) return null;
        if (sel.includes('[role="tab"][aria-selected="true"]')) {
          return { getAttribute: (name: string) => (name === "title" ? "数据" : null), textContent: "数据" };
        }
        return null;
      },
    } as unknown as HTMLElement;
    expect(readNativeRibbonTab(tablist)).toBe("data");
    expect(readNativeRibbonTab(tablist, true)).toBeNull();
  });

  it("treats history aria-selected as exclusive", () => {
    const tablist = {
      querySelector(sel: string) {
        if (sel.includes("[data-em-ribbon=\"history\"]")) return {};
        if (sel.includes('[role="tab"][aria-selected="true"]')) {
          return { getAttribute: () => "开始", textContent: "开始" };
        }
        return null;
      },
    } as unknown as HTMLElement;
    expect(readNativeRibbonTab(tablist)).toBeNull();
  });

  it("returns null for unknown labels", () => {
    expect(nativeRibbonTabFromLabel("视图")).toBeNull();
  });
});

describe("ribbonAskActions", () => {
  it("only exposes ask actions on formula and data", () => {
    expect(ribbonAskActions("home")).toEqual([]);
    expect(ribbonAskActions("formula").map((a) => a.kind)).toEqual([
      "explain-formula",
      "trace-formula",
      "generate-formula",
    ]);
    expect(ribbonAskActions("data").map((a) => a.kind)).toEqual([
      "data-quality",
      "filter-analyze",
      "chart",
      "pivot",
      "dedupe",
      "sort",
    ]);
  });
});

describe("buildRibbonAskPrompt", () => {
  it("embeds file mention with sheet and range", () => {
    const prompt = buildRibbonAskPrompt("explain-formula", {
      path: "uploads/订单.xlsx",
      sheet: "订单",
      range: "G2",
    });
    expect(prompt).toContain("@file:uploads/订单.xlsx[订单!G2]");
    expect(prompt).toContain("不要改表");
    expect(prompt).toContain("不是已经重算");
  });

  it("falls back to path-only mention", () => {
    const prompt = buildRibbonAskPrompt("data-quality", { path: "sales.xlsx" });
    expect(prompt).toContain("@file:sales.xlsx");
    expect(prompt).not.toContain("[");
    expect(prompt).toContain("不要改表");
  });

  it("includes content version when provided", () => {
    const prompt = buildRibbonAskPrompt("explain-formula", {
      path: "sales.xlsx",
      sheet: "Data",
      range: "A1",
      version: "sha256:abcd",
    });
    expect(prompt).toContain("@file:sales.xlsx[Data!A1]@sha256:abcd");
    expect(prompt).toContain("不要改表");
  });

  it("does not pretend AutoFilter exists", () => {
    const prompt = buildRibbonAskPrompt("filter-analyze", { path: "a.xlsx", sheet: "S", range: "A1:D10" });
    expect(prompt).toContain("@file:a.xlsx[S!A1:D10]");
    expect(prompt).toContain("不是表格上的自动筛选");
  });
});

describe("selection Agent actions", () => {
  it("offers grid actions and preserves the exact selection mention", () => {
    expect(SELECTION_AGENT_ACTIONS.map((action) => action.kind)).toEqual([
      "analyze-selection",
      "explain-selection",
      "data-quality-selection",
      "clean-selection",
    ]);
    const prompt = buildSelectionAgentPrompt("data-quality-selection", {
      path: "uploads/订单.xlsx",
      sheet: "明细",
      range: "B2:F20",
      version: "sha256:1234",
    });
    expect(prompt).toContain("@file:uploads/订单.xlsx[明细!B2:F20]@sha256:1234");
    expect(prompt).toContain("不要修改表格");
  });
});

describe("grid ask prompts", () => {
  const ctx = { path: "sales.xlsx", sheet: "明细", range: "C2", version: "sha256:v1" };

  it("delegates prompts to the formula or selection builders", () => {
    expect(buildGridAskPrompt("explain-formula", ctx)).toBe(buildRibbonAskPrompt("explain-formula", ctx));
    expect(buildGridAskPrompt("trace-formula", ctx)).toBe(buildRibbonAskPrompt("trace-formula", ctx));
    expect(buildGridAskPrompt("clean-selection", ctx)).toBe(buildSelectionAgentPrompt("clean-selection", ctx));
  });
});

describe("history ribbon exclusivity", () => {
  it("marks the tablist so native tabs can be visually idle", () => {
    const attrs: Record<string, string> = {};
    const tablist = {
      setAttribute: (key: string, value: string) => {
        attrs[key] = value;
      },
      removeAttribute: (key: string) => {
        delete attrs[key];
      },
      getAttribute: (key: string) => attrs[key] ?? null,
      hasAttribute: (key: string) => key in attrs,
    } as unknown as HTMLElement;
    setHistoryRibbonMode(tablist, true);
    expect(tablist.getAttribute("data-em-ribbon-mode")).toBe("history");
    setHistoryRibbonMode(tablist, false);
    expect(tablist.hasAttribute("data-em-ribbon-mode")).toBe(false);
  });

  it("hides the native toolbar and removes the ask-AI host", () => {
    const attrs: Record<string, string> = {};
    let hidden = false;
    let host: { remove: () => void } | null = { remove: () => { host = null; } };
    const toolbar = {
      get hidden() {
        return hidden;
      },
      set hidden(value: boolean) {
        hidden = value;
      },
      setAttribute: (key: string, value: string) => {
        attrs[key] = value;
      },
      removeAttribute: (key: string) => {
        delete attrs[key];
      },
      hasAttribute: (key: string) => key in attrs,
      querySelector: (sel: string) => (sel.includes("data-em-ribbon-commands") ? host : null),
    } as unknown as HTMLElement;

    setRibbonToolbarHidden(toolbar, true);
    expect(toolbar.hidden).toBe(true);
    expect(toolbar.hasAttribute("data-em-ribbon-toolbar-hidden")).toBe(true);
    expect(toolbar.querySelector("[data-em-ribbon-commands]")).toBeNull();

    setRibbonToolbarHidden(toolbar, false);
    expect(toolbar.hidden).toBe(false);
    expect(toolbar.hasAttribute("data-em-ribbon-toolbar-hidden")).toBe(false);
  });
});
