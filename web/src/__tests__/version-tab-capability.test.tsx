// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
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

afterEach(cleanup);
it("shows server upgrade capability even when local management endpoints are forbidden", async () => {
  mocks.get.mockImplementation(async (url: string) => {
    if (url === "/version/check") return { current: "1.8.0", latest: "1.9.0", has_update: true, commits_behind: 1 };
    if (url === "/version/upgrade/capability") return { supported: true, reason: null };
    throw new Error("403 Forbidden");
  });
  render(<VersionTab />);
  expect(await screen.findByRole("button", { name: "一键更新前后端" })).toBeTruthy();
  expect(screen.queryByText(/版本信息加载失败/)).toBeNull();
});
