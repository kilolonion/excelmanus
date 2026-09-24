// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ModelTab } from "@/components/settings/ModelTab";
import CodexCallbackPage from "@/app/auth/codex/callback/page";
import { takePendingModelSubTab } from "@/components/settings/model/model-subtab";

const { apiGet, apiPost, searchParams } = vi.hoisted(() => ({ apiGet: vi.fn(), apiPost: vi.fn(), searchParams: new URLSearchParams("code=ok&state=test-state") }));
vi.mock("@/lib/api", () => ({ apiGet, apiPost, apiPut: vi.fn(), apiDelete: vi.fn() }));
vi.mock("next/navigation", () => ({ useSearchParams: () => searchParams }));
vi.mock("@/components/settings/model/useAdminModelSettings", () => ({ useAdminModelSettings: () => ({ config: { profiles: [] }, loading: false, loadError: null }) }));
vi.mock("@/components/settings/model/ProviderSection", () => ({ ProviderSection: () => <p>API 连接</p> }));
vi.mock("@/components/settings/model/JevProviderSection", () => ({ JevProviderSection: () => null }));
vi.mock("@/components/settings/model/RoleModelSection", () => ({ RoleModelSection: () => null }));
vi.mock("@/components/settings/model/JevRoleSection", () => ({ JevRoleSection: () => null }));
vi.mock("@/components/settings/model/AdvancedDiagnosticsPanel", () => ({ AdvancedDiagnosticsPanel: () => null }));

beforeEach(() => {
  vi.clearAllMocks();
  window.opener = null;
  vi.spyOn(window, "open").mockReturnValue(null);
  apiGet.mockImplementation(async (path) => ({ provider: path.split("/")[3], status: "disconnected" }));
  apiPost.mockResolvedValue({ authorize_url: "https://auth.openai.com/authorize", redirect_uri: "http://localhost/auth/callback", state: "test-state", mode: "popup" });
});
afterEach(() => { cleanup(); takePendingModelSubTab(); vi.restoreAllMocks(); });

describe("model settings navigation", () => {
  it("keeps an in-progress login alive when visiting another model settings subtab", async () => {
    render(<ModelTab />);
    expect(apiGet).not.toHaveBeenCalled(); // Subscription loading is lazy.
    fireEvent.click(screen.getByRole("tab", { name: /订阅账号/ }));
    fireEvent.click(screen.getByRole("button", { name: /GPT Codex/ }));
    const login = await screen.findByRole("button", { name: "使用 ChatGPT 账号登录" });
    fireEvent.click(login);
    await screen.findByRole("button", { name: "取消登录" });
    fireEvent.click(screen.getByRole("tab", { name: /供应商/ }));
    expect(screen.queryByRole("button", { name: "取消登录" })).toBeNull();
    fireEvent.click(screen.getByRole("tab", { name: /订阅账号/ }));
    expect(screen.getByRole("button", { name: "取消登录" })).toBeTruthy();
    expect(apiPost).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: "取消登录" }));
    expect(screen.getByRole("button", { name: "使用 ChatGPT 账号登录" })).toBeTruthy();
  });

  it("offers manual callback recovery when the callback has no opener", async () => {
    render(<CodexCallbackPage />);
    expect(screen.getByText("请复制完整回调地址，返回设置页粘贴以完成连接。")).toBeTruthy();
    expect(screen.queryByText("授权成功")).toBeNull();
    expect(screen.getByRole("button", { name: "复制回调地址" })).toBeTruthy();
  });

  it("sends callback data to the same origin and clears its close timer on unmount", async () => {
    vi.useFakeTimers();
    const postMessage = vi.fn();
    window.opener = { postMessage };
    const { unmount } = render(<CodexCallbackPage />);
    expect(postMessage).toHaveBeenCalledWith({ type: "codex-oauth-callback", code: "ok", state: "test-state" }, window.location.origin);
    unmount();
    expect(vi.getTimerCount()).toBe(0);
    vi.useRealTimers();
  });
});
