import { beforeEach, describe, expect, it } from "vitest";
import { useExcelStore, type ExcelFileRef } from "@/stores/excel-store";
import { useSessionStore } from "@/stores/session-store";
import {
  recentFilesForWorkspace,
  sanitizeRecentFiles,
} from "@/lib/workspace-file-ref";

function bindSession(workspaceId: string) {
  useSessionStore.setState({
    sessions: [{
      id: "s1",
      title: "t",
      messageCount: 0,
      inFlight: false,
      workspaceId,
    }],
    activeSessionId: "s1",
  });
  useExcelStore.setState({ activeWorkspaceKey: `id:${workspaceId}` });
}

describe("excel-store recent files", () => {
  beforeEach(() => {
    useSessionStore.setState({ sessions: [], activeSessionId: null });
    useExcelStore.setState({
      recentFiles: [],
      dismissedPaths: new Set(),
      activeWorkspaceKey: null,
    });
  });

  it("drops persisted items without a scoped workspaceKey", () => {
    const raw = [
      { path: "./old.xlsx", filename: "old.xlsx", lastUsedAt: 1 },
      { path: "./a.xlsx", filename: "a.xlsx", lastUsedAt: 2, workspaceKey: "_" },
      { path: "./b.xlsx", filename: "b.xlsx", lastUsedAt: 3, workspaceKey: "id:ws-a" },
    ] as ExcelFileRef[];
    expect(sanitizeRecentFiles(raw)).toEqual([raw[2]]);
    expect(recentFilesForWorkspace(raw, "id:ws-a")).toEqual([raw[2]]);
    expect(recentFilesForWorkspace(raw, "_")).toEqual([]);
  });

  it("does not keep unscoped leftovers when adding a file", () => {
    bindSession("ws-a");
    useExcelStore.setState({
      recentFiles: [
        { path: "./legacy.xlsx", filename: "legacy.xlsx", lastUsedAt: 1 },
        { path: "./kept.xlsx", filename: "kept.xlsx", lastUsedAt: 2, workspaceKey: "id:ws-b" },
      ],
    });
    useExcelStore.getState().addRecentFile({ path: "./new.xlsx", filename: "new.xlsx" });
    const recent = useExcelStore.getState().recentFiles;
    expect(recent.every((item) => item.workspaceKey && item.workspaceKey !== "_")).toBe(true);
    expect(recent.some((item) => item.path.includes("legacy"))).toBe(false);
    expect(recent.some((item) => item.path.includes("kept") && item.workspaceKey === "id:ws-b")).toBe(true);
    expect(recent[0]).toMatchObject({ path: "./new.xlsx", workspaceKey: "id:ws-a" });
  });

  it("refuses to add recent files without a bound workspace", () => {
    useExcelStore.getState().addRecentFile({ path: "./a.xlsx", filename: "a.xlsx" });
    expect(useExcelStore.getState().recentFiles).toEqual([]);
  });

  it("keeps non-spreadsheet mutation files out of the workbook recent list", () => {
    bindSession("ws-a");

    useExcelStore.getState().addRecentFile({ path: "./receipt_src.jpg", filename: "receipt_src.jpg" });
    useExcelStore.getState().addRecentFileIfNotDismissed(
      { path: "./notes.md", filename: "notes.md" },
      "id:ws-a",
    );
    useExcelStore.getState().mergeRecentFiles([
      { path: "./_work_receipt.jpg", filename: "_work_receipt.jpg", modifiedAt: 3 },
      { path: "./sales.xlsx", filename: "sales.xlsx", modifiedAt: 2 },
    ], "id:ws-a");

    expect(useExcelStore.getState().recentFiles.map((file) => file.path)).toEqual(["./sales.xlsx"]);
  });

  it("removes legacy image entries when sanitizing persisted workbook recents", () => {
    const raw = [
      { path: "./receipt_src.jpg", filename: "receipt_src.jpg", lastUsedAt: 3, workspaceKey: "id:ws-a" },
      { path: "./sales.xlsx", filename: "sales.xlsx", lastUsedAt: 2, workspaceKey: "id:ws-a" },
    ] as ExcelFileRef[];

    expect(sanitizeRecentFiles(raw).map((file) => file.path)).toEqual(["./sales.xlsx"]);
    expect(recentFilesForWorkspace(raw, "id:ws-a").map((file) => file.path)).toEqual(["./sales.xlsx"]);
  });

  it("only displays the current workspace bucket", () => {
    const files: ExcelFileRef[] = [
      { path: "./a.xlsx", filename: "a.xlsx", lastUsedAt: 2, workspaceKey: "id:ws-a" },
      { path: "./b.xlsx", filename: "b.xlsx", lastUsedAt: 1, workspaceKey: "id:ws-b" },
      { path: "./old.xlsx", filename: "old.xlsx", lastUsedAt: 3 },
    ];
    expect(recentFilesForWorkspace(files, "id:ws-a").map((item) => item.path)).toEqual(["./a.xlsx"]);
  });

  it("keys recovered file paths by the source session workspace, not the active one", () => {
    bindSession("ws-a");
    useExcelStore.getState().addRecentFileIfNotDismissed(
      { path: "outputs/bench-file.xlsx", filename: "bench-file.xlsx" },
      "id:ws-bench",
    );
    const recent = useExcelStore.getState().recentFiles;
    expect(recent[0]).toMatchObject({
      path: "outputs/bench-file.xlsx",
      workspaceKey: "id:ws-bench",
    });
    expect(recentFilesForWorkspace(recent, "id:ws-a")).toEqual([]);
    expect(
      recentFilesForWorkspace(recent, "id:ws-bench").map((f) => f.path),
    ).toEqual(["outputs/bench-file.xlsx"]);
  });

  it("evicts a missing-file entry only within its workspace bucket", () => {
    useExcelStore.setState({
      recentFiles: [
        { path: "./gone.xlsx", filename: "gone.xlsx", lastUsedAt: 1, workspaceKey: "id:ws-a" },
        { path: "./gone.xlsx", filename: "gone.xlsx", lastUsedAt: 2, workspaceKey: "id:ws-b" },
        { path: "./kept.xlsx", filename: "kept.xlsx", lastUsedAt: 3, workspaceKey: "id:ws-a" },
      ],
    });
    useExcelStore.getState().evictRecentFile("gone.xlsx", "id:ws-a");
    const recent = useExcelStore.getState().recentFiles;
    expect(recent.map((f) => `${f.workspaceKey}|${f.path}`)).toEqual([
      "id:ws-b|./gone.xlsx",
      "id:ws-a|./kept.xlsx",
    ]);
    // 不写 dismissedPaths：文件被重建后系统事件仍可重新加入
    expect(useExcelStore.getState().dismissedPaths.has("gone.xlsx")).toBe(false);
    expect(useExcelStore.getState().dismissedPaths.has("./gone.xlsx")).toBe(false);
  });
});
