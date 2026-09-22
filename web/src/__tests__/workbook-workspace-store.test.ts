// @vitest-environment jsdom
import { beforeEach, describe, expect, it } from "vitest";
import { useWorkbookWorkspaceStore, visibleWorkbookPaths } from "@/stores/workbook-workspace-store";

const key = "session|id:workspace";

beforeEach(() => {
  localStorage.clear();
  useWorkbookWorkspaceStore.setState({ workspaces: {} });
});

describe("multi-workbook workspace", () => {
  it("keeps the first opened workbook as primary while revealing at most three panes", () => {
    const store = useWorkbookWorkspaceStore.getState();
    store.open(key, "main.xlsx", "Sheet1");
    store.open(key, "input.xlsx", "Data");
    store.open(key, "output.xlsx", "Summary");
    store.open(key, "archive.xlsx", "Sheet1");

    const workspace = useWorkbookWorkspaceStore.getState().workspaces[key];
    expect(workspace.files.map((file) => file.path)).toEqual(["main.xlsx", "input.xlsx", "output.xlsx", "archive.xlsx"]);
    expect(workspace.files[0].path).toBe("main.xlsx");
    expect(visibleWorkbookPaths(workspace)).toEqual(["main.xlsx", "input.xlsx", "archive.xlsx"]);
  });

  it("promotes a reference without losing the remaining references", () => {
    const store = useWorkbookWorkspaceStore.getState();
    store.open(key, "main.xlsx");
    store.open(key, "input.xlsx");
    store.open(key, "output.xlsx");
    store.promote(key, "output.xlsx");
    const workspace = useWorkbookWorkspaceStore.getState().workspaces[key];
    expect(workspace.files[0].path).toBe("output.xlsx");
    expect(visibleWorkbookPaths(workspace, 3)).toEqual(["output.xlsx", "main.xlsx", "input.xlsx"]);
  });

  it("only creates cross-pane navigation when the user enabled linking", () => {
    const store = useWorkbookWorkspaceStore.getState();
    store.open(key, "main.xlsx");
    store.open(key, "input.xlsx");
    store.focus(key, "main.xlsx");
    store.navigate(key, "main.xlsx", "Data", "A2:C8");
    expect(useWorkbookWorkspaceStore.getState().workspaces[key].navigation).toBeUndefined();
    store.setLinkSelection(key, true);
    store.navigate(key, "main.xlsx", "Data", "A2:C8");
    expect(useWorkbookWorkspaceStore.getState().workspaces[key].navigation?.range).toBe("A2:C8");
    store.navigate(key, "input.xlsx", "Data", "D4");
    expect(useWorkbookWorkspaceStore.getState().workspaces[key].navigation?.range).toBe("A2:C8");
  });

  it("restores layout and sheets without reusing transient selections or unsafe identities", async () => {
    localStorage.setItem("excelmanus-workbook-workspaces", JSON.stringify({ version: 0, state: { workspaces: {
      [key]: { files: [{ path: "./main.xlsx", sheet: "明细" }, { path: "main.xlsx" }, { path: "ref.xlsx" },
        { path: "../outside.xlsx" }, { path: "C:/outside.xlsx" }], focused: "ref.xlsx", slots: ["ref.xlsx"],
        paneCount: 2, linkSelection: true, navigation: { sheet: "明细", range: "D5" }, visible: ["old.xlsx"] },
    } } }));
    await useWorkbookWorkspaceStore.persist.rehydrate();
    const restored = useWorkbookWorkspaceStore.getState().workspaces[key];
    expect(restored.files).toEqual([{ path: "main.xlsx", sheet: "明细" }, { path: "ref.xlsx" }]);
    expect(visibleWorkbookPaths(restored)).toEqual(["main.xlsx", "ref.xlsx"]);
    expect(visibleWorkbookPaths(restored, 1)).toEqual(["ref.xlsx"]);
    expect(restored.navigation).toBeUndefined();
    expect(restored.visible).toBeUndefined();
  });
});
