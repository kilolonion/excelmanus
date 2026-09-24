// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
const mocks = vi.hoisted(() => ({ get: vi.fn() }));
vi.mock("@/lib/api", () => ({
  apiGet: mocks.get, apiPost: vi.fn(), cleanupVersionBackups: vi.fn(), restoreVersionBackup: vi.fn(),
  migrateVersionData: vi.fn(), fetchDeployStatus: vi.fn().mockRejectedValue(new Error("forbidden")),
  buildFrontendArtifact: vi.fn(), executeRemoteDeploy: vi.fn(), startVersionUpgrade: vi.fn(),
  fetchVersionManifest: vi.fn().mockResolvedValue({}),
}));
vi.mock("@/stores/auth-config-store", () => ({ useAuthConfigStore: (selector: (s: object) => unknown) => selector({ deployMode: "server" }) }));
vi.mock("@/stores/connection-store", () => ({ useConnectionStore: (selector: (s: object) => unknown) => selector({ triggerRestart: vi.fn() }) }));
vi.mock("@/components/settings/RollbackPanel", () => ({ RollbackPanel: () => null }));
vi.mock("@/components/settings/ProjectLinks", () => ({ ProjectLinks: () => null }));
import { VersionTab } from "@/components/settings/VersionTab";

afterEach(() => { cleanup(); vi.clearAllMocks(); });
it("shows release checks even when local management endpoints are forbidden", async () => {
  mocks.get.mockImplementation(async (url: string) => {
    if (url === "/version/check") return { current: "1.8.1", latest: "1.9.0", has_update: true, commits_behind: 0, check_method: "github_release_api" };
    if (url === "/version/upgrade/capability") return { supported: true, reason: null };
    throw new Error("403 Forbidden");
  });
  render(<VersionTab />);
  expect(await screen.findByRole("button", { name: "检查更新" })).toBeTruthy();
  expect(screen.queryByText(/版本信息加载失败/)).toBeNull();
});

it("shows local controls while a release check is pending", async () => {
  let resolve!: (value: object) => void;
  const pending = new Promise(r => { resolve = r; });
  mocks.get.mockImplementation(async (url: string) => {
    if (url === "/version/check") return pending;
    if (url === "/version/upgrade/capability") return { supported: true };
    throw new Error("403 Forbidden");
  });
  render(<VersionTab />);
  expect(await screen.findByRole("button", { name: "检查更新" })).toBeTruthy();
  expect(screen.getByText("正在检查正式发布…")).toBeTruthy();
  expect(screen.queryByText(/已是最新版本/)).toBeNull();
  await act(async () => resolve({ current: "1.8.1", latest: "1.7.1", has_update: false,
    check_method: "github_release_api", release_url: "https://github.com/kilolonion/excelmanus/releases/tag/v1.7.1" }));
  expect(screen.getByText("暂无更新的正式发布（最新发布 v1.7.1）")).toBeTruthy();
  expect((screen.getByRole("button", { name: "检查更新" }) as HTMLButtonElement).disabled).toBe(false);
  expect(screen.getByRole("link", { name: "查看 GitHub Release 与下载" }).getAttribute("href")).toContain("/tag/v1.7.1");
});

it("keeps only the release action and does not request source update endpoints", async () => {
  mocks.get.mockImplementation(async (url: string) => {
    if (url === "/version/check") return { current: "1.8.1", latest: "1.8.0", has_update: false, check_method: "github_release_api" };
    if (url === "/version/upgrade/capability") return { supported: true };
    throw new Error("403 Forbidden");
  });
  render(<VersionTab />);
  const check = await screen.findByRole("button", { name: "检查更新" });
  expect(check.closest("section")?.querySelectorAll("button").length).toBe(1);
  expect(screen.queryByRole("button", { name: /源码分支|分支提交/ })).toBeNull();
  expect(mocks.get.mock.calls.some(([url]) => String(url).startsWith("/version/upgrade/"))).toBe(false);
});

it("refreshes release results on demand without starting an update", async () => {
  mocks.get.mockImplementation(async (url: string) => {
    if (url === "/version/check") return { current: "1.8.1", latest: "1.8.0", has_update: false, check_method: "github_release_api" };
    if (url === "/version/check?force=1") return { current: "1.8.1", latest: "1.9.0", has_update: true,
      check_method: "github_release_api", release_url: "https://github.com/kilolonion/excelmanus/releases/tag/v1.9.0" };
    throw new Error("403 Forbidden");
  });
  render(<VersionTab />);
  const check = await screen.findByRole("button", { name: "检查更新" });
  await waitFor(() => expect((check as HTMLButtonElement).disabled).toBe(false));
  fireEvent.click(check);
  await waitFor(() => expect(mocks.get).toHaveBeenCalledWith("/version/check?force=1"));
  expect(await screen.findByRole("link", { name: "查看 GitHub Release 与下载" })).toBeTruthy();
  expect(screen.queryByRole("button", { name: /更新当前源码分支/ })).toBeNull();
});

it("keeps network failure details visible without claiming the version is current", async () => {
  mocks.get.mockImplementation(async (url: string) => {
    if (url === "/version/check") return { current: "1.8.1", latest: "1.8.1", has_update: false,
      check_method: "github_release_api", check_failed: true, error: "GitHub API：连接超时" };
    if (url === "/version/upgrade/capability") return { supported: false, reason: "请通过启动脚本运行" };
    throw new Error("403 Forbidden");
  });
  render(<VersionTab />);
  expect(await screen.findByText("GitHub API：连接超时")).toBeTruthy();
  expect(screen.queryByText(/暂无更新的正式发布|已是最新版本/)).toBeNull();
});
