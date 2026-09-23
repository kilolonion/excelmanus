// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

const mocks = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }));

vi.mock("@/lib/api", () => ({
  apiGet: mocks.get,
  apiPost: mocks.post,
  cleanupVersionBackups: vi.fn(),
  restoreVersionBackup: vi.fn(),
  migrateVersionData: vi.fn(),
  fetchDeployStatus: vi.fn().mockRejectedValue(new Error("forbidden")),
  buildFrontendArtifact: vi.fn(),
  executeRemoteDeploy: vi.fn(),
  startVersionUpgrade: vi.fn(),
  fetchVersionManifest: vi.fn().mockResolvedValue({}),
}));
vi.mock("@/stores/auth-config-store", () => ({
  useAuthConfigStore: (selector: (state: object) => unknown) => selector({ deployMode: "standalone" }),
}));
vi.mock("@/stores/connection-store", () => ({
  useConnectionStore: (selector: (state: object) => unknown) => selector({ triggerRestart: vi.fn() }),
}));
vi.mock("@/components/settings/RollbackPanel", () => ({ RollbackPanel: () => null }));
vi.mock("@/components/settings/ProjectLinks", () => ({ ProjectLinks: () => null }));
import { VersionTab } from "@/components/settings/VersionTab";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

it("marks the current installation and removes only an old record", async () => {
  const current = "C:\\ExcelManus";
  const legacy = "D:\\ExcelManus-old";
  mocks.get.mockImplementation(async (url: string) => {
    if (url === "/version/check") return { current: "1.8.0", latest: "1.8.0", has_update: false, check_method: "github_release_api" };
    if (url === "/version/installations") {
      return {
        current_path: current,
        installations: [
          { path: current, version: "1.8.0", status: "current", is_current: true, exists: true },
          { path: legacy, version: "1.7.2", status: "available", is_current: false, exists: true },
        ],
      };
    }
    if (url === "/version/upgrade/capability") return { supported: false, reason: "disabled" };
    throw new Error("forbidden");
  });
  mocks.post.mockResolvedValue({ status: "ok", deleted_path: legacy, remaining: 1, directory_deleted: true });
  vi.stubGlobal("confirm", vi.fn(() => true));

  render(<VersionTab />);

  expect(await screen.findByText(current)).toBeTruthy();
  expect(screen.getByText("当前")).toBeTruthy();
  const removeButton = screen.getByRole("button", { name: "删除旧安装" });
  fireEvent.click(removeButton);
  await waitFor(() => expect(mocks.post).toHaveBeenCalledWith("/version/installations/delete", {
    path: legacy,
    delete_directory: true,
  }));
  expect(screen.queryByText(legacy)).toBeNull();
  expect(screen.getByText("已删除旧安装目录和记录")).toBeTruthy();
  expect((screen.getByRole("button", { name: "当前安装" }) as HTMLButtonElement).disabled).toBe(true);
});
