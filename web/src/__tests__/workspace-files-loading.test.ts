import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api", async (original) => ({
  ...await original<typeof import("@/lib/api")>(),
  fetchWorkspaceFiles: vi.fn(),
}));

import { fetchWorkspaceFiles, type WorkspaceFileList } from "@/lib/api";
import { useExcelStore } from "@/stores/excel-store";
import { useSessionStore } from "@/stores/session-store";

const scan = vi.mocked(fetchWorkspaceFiles);
const result = (path: string): WorkspaceFileList => ({ files: [{ path, filename: path, modified_at: 1 }], truncated: false });
const refresh = () => useExcelStore.getState().refreshWorkspaceFiles;

describe("workspace file scans", () => {
  beforeEach(() => {
    scan.mockReset();
    useSessionStore.setState({ activeSessionId: "a", sessions: [
      { id: "a", title: "A", messageCount: 0, inFlight: false, workspaceId: "ws-a" },
      { id: "b", title: "B", messageCount: 0, inFlight: false, workspaceId: "ws-b" },
    ] });
    useExcelStore.setState({ workspaceFiles: [], wsFilesLoaded: false, workspaceFilesSessionId: undefined,
      workspaceFilesVersion: 0, workspaceFilesLoadedVersion: -1, workspaceFilesLoadedAt: 0, workspaceFilesError: null, recentFiles: [] });
  });

  it("shares in-flight scans and reuses a fresh snapshot across panel remounts", async () => {
    let resolve!: (value: WorkspaceFileList) => void;
    scan.mockImplementationOnce(() => new Promise((done) => { resolve = done; }));
    const first = refresh()("a");
    const second = refresh()("a", { cached: true });
    expect(scan).toHaveBeenCalledTimes(1);
    resolve(result("one.xlsx"));
    await Promise.all([first, second]);
    await refresh()("a", { cached: true });
    expect(scan).toHaveBeenCalledTimes(1);
    expect(useExcelStore.getState().recentFiles[0]).toMatchObject({ path: "one.xlsx", workspaceKey: "id:ws-a" });
  });

  it("invalidates the cache on file changes and permits an explicit refresh", async () => {
    scan.mockResolvedValue(result("one.xlsx"));
    await refresh()("a");
    useExcelStore.getState().bumpWorkspaceFilesVersion();
    await refresh()("a", { cached: true });
    await refresh()("a");
    expect(scan).toHaveBeenCalledTimes(3);
  });

  it("does not let a slower old-session scan overwrite the current workspace", async () => {
    let resolveA!: (value: WorkspaceFileList) => void;
    scan.mockImplementationOnce(() => new Promise((done) => { resolveA = done; }));
    const pending = refresh()("a");
    useSessionStore.getState().setActiveSession("b");
    scan.mockResolvedValueOnce(result("b.xlsx"));
    await refresh()("b");
    resolveA(result("a.xlsx"));
    await pending;
    expect(useExcelStore.getState().workspaceFiles.map((f) => f.path)).toEqual(["b.xlsx"]);
    expect(useExcelStore.getState().recentFiles.every((f) => f.workspaceKey === "id:ws-b")).toBe(true);
  });

  it("retains row identities on unchanged scans and retains data on errors", async () => {
    scan.mockResolvedValue(result("one.xlsx"));
    await refresh()("a");
    const files = useExcelStore.getState().workspaceFiles;
    await refresh()("a");
    expect(useExcelStore.getState().workspaceFiles).toBe(files);
    scan.mockRejectedValueOnce(new Error("unavailable"));
    await refresh()("a");
    expect(useExcelStore.getState().workspaceFiles).toBe(files);
    expect(useExcelStore.getState().workspaceFilesError).toBe("unavailable");
  });

  it("clears old workspace files even when the file panel is unmounted", async () => {
    scan.mockResolvedValue(result("a.xlsx"));
    await refresh()("a");
    useExcelStore.getState().rebindSession("id:ws-a", "id:ws-b");
    expect(useExcelStore.getState().workspaceFiles).toEqual([]);
    expect(useExcelStore.getState().wsFilesLoaded).toBe(false);
  });

  it("revalidates expired snapshots even if the version has not changed", async () => {
    scan.mockResolvedValue(result("one.xlsx"));
    await refresh()("a");
    useExcelStore.setState({ workspaceFilesLoadedAt: Date.now() - 31_000 });
    await refresh()("a", { cached: true });
    expect(scan).toHaveBeenCalledTimes(2);
  });
});
