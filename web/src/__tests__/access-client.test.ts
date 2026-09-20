import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { apiFetch, AUTH_REQUIRED_EVENT } from "@/lib/api";
import { loginToInstance, logoutFromInstance, saveAccessSettings } from "@/lib/access-api";

const fetchMock = vi.fn();
const dispatchEvent = vi.fn();
const reload = vi.fn();
const removeItem = vi.fn();
const setItem = vi.fn();
const status = { auth_required: true, authenticated: true, login_method: "password", username: "admin" };
function json(body: unknown, status = 200, headers = {}) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json", ...headers } });
}

describe("single administrator client", () => {
  beforeEach(() => {
    vi.stubGlobal("window", { location: { origin: "https://instance.example", hostname: "instance.example", protocol: "https:", reload }, dispatchEvent });
    vi.stubGlobal("sessionStorage", { getItem: () => null, removeItem, setItem });
    vi.stubGlobal("fetch", fetchMock);
  });
  afterEach(() => { vi.unstubAllGlobals(); vi.resetAllMocks(); });

  it("validates the cookie with the server before entering and never stores the password", async () => {
    fetchMock.mockResolvedValueOnce(json({ authenticated: true })).mockResolvedValueOnce(json(status));
    expect(await loginToInstance("admin", "a-secure-password")).toEqual(status);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/v1/auth/login");
    expect(init.headers["X-Requested-With"]).toBe("ExcelManus");
    expect(JSON.parse(init.body)).toEqual({ username: "admin", password: "a-secure-password" });
    expect(fetchMock.mock.calls[1][0]).toBe("/api/v1/auth/status");
    expect(setItem).not.toHaveBeenCalled();
  });

  it("rejects a login response if the browser did not accept its cookie", async () => {
    fetchMock.mockResolvedValueOnce(json({ authenticated: true })).mockResolvedValueOnce(json({ ...status, authenticated: false }));
    await expect(loginToInstance("admin", "password")).rejects.toThrow("登录会话未保存");
  });

  it("shows wrong-password and rate-limit errors without opening the workspace", async () => {
    fetchMock.mockResolvedValueOnce(json({ detail: "账号或密码不正确" }, 401));
    await expect(loginToInstance("admin", "wrong")).rejects.toThrow("账号或密码不正确");
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(dispatchEvent).not.toHaveBeenCalled();
    fetchMock.mockResolvedValueOnce(json({ detail: "登录尝试过于频繁" }, 429));
    await expect(loginToInstance("admin", "wrong")).rejects.toThrow("登录尝试过于频繁");
  });

  it("locks the UI on an API authentication failure, including streamed requests", async () => {
    fetchMock.mockResolvedValueOnce(json({}, 401, { "X-ExcelManus-Auth": "required" }));
    const response = await apiFetch("/api/v1/chat/stream", { method: "POST" });
    expect(response.status).toBe(401);
    expect(dispatchEvent.mock.calls[0][0].type).toBe(AUTH_REQUIRED_EVENT);
    expect(removeItem).toHaveBeenCalledWith("excelmanus_manage_token");
  });

  it("revokes the session before reloading on logout", async () => {
    fetchMock.mockResolvedValueOnce(json({ authenticated: false }));
    await logoutFromInstance();
    expect(fetchMock.mock.calls[0][0]).toBe("/api/v1/auth/logout");
    expect(reload).toHaveBeenCalledOnce();
  });

  it("saves the switch and credentials using the protected settings contract", async () => {
    fetchMock.mockResolvedValueOnce(json({ enabled: false }));
    await saveAccessSettings({ enabled: false, username: "admin", password: "" });
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/v1/auth/settings");
    expect(init.method).toBe("PUT");
    expect(init.headers["X-Requested-With"]).toBe("ExcelManus");
    expect(JSON.parse(init.body)).toEqual({ enabled: false, username: "admin", password: "" });
  });
});
