import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const resolveDirectBackendOrigin = vi.hoisted(() => vi.fn());

vi.mock("@/lib/backend-origin", () => ({ resolveDirectBackendOrigin }));

import { apiFetch, apiGet, apiPost, buildApiUrl, uploadFile, fetchWorkbookView, workspaceCreateFile } from "@/lib/api";

describe("runtime API client", () => {
  it("retains HTTP status so name conflicts are distinguishable from failed writes", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false, status: 409, json: async () => ({ detail: "模型名称已存在" }) }));
    await expect(apiPost("/config/models/profiles", {})).rejects.toMatchObject({ message: "模型名称已存在", status: 409 });
  });
  beforeEach(() => {
    resolveDirectBackendOrigin.mockReturnValue("http://127.0.0.1:54321");
    vi.stubGlobal("window", {
      location: { origin: "http://127.0.0.1:3000" },
      __EXCELMANUS_RUNTIME__: { backendOrigin: "http://127.0.0.1:54321" },
    });
    vi.stubGlobal("sessionStorage", {
      getItem: vi.fn(() => "manage-secret"),
      setItem: vi.fn(),
      removeItem: vi.fn(),
    });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("uses the runtime port and preserves management authentication cross-origin", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ sessions: [] }),
    } as Response);
    vi.stubGlobal("fetch", fetchMock);

    await apiGet("/sessions");

    expect(fetchMock).toHaveBeenCalledWith(
      "http://127.0.0.1:54321/api/v1/sessions",
      expect.objectContaining({
        credentials: "include",
        headers: { Authorization: "Bearer manage-secret" },
      }),
    );
  });

  it("keeps startup probes on the configured runtime origin", async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce({
      ok: true,
      json: async () => ({ status: "ok" }),
    } as Response);
    vi.stubGlobal("fetch", fetchMock);

    await expect(apiGet("/health", { direct: true, timeoutMs: 100 })).resolves.toEqual({ status: "ok" });
    expect(fetchMock.mock.calls.map(([url]) => url)).toEqual(["http://127.0.0.1:54321/api/v1/health"]);
  });

  it("uses the same-origin API path when no runtime backend is configured", async () => {
    delete window.__EXCELMANUS_RUNTIME__;
    resolveDirectBackendOrigin.mockReturnValue("");
    const fetchMock = vi.fn().mockResolvedValueOnce({
      ok: true,
      json: async () => ({ status: "ok" }),
    } as Response);
    vi.stubGlobal("fetch", fetchMock);

    await expect(apiGet("/health", { direct: true })).resolves.toEqual({ status: "ok" });
    expect(fetchMock.mock.calls[0][0]).toBe("/api/v1/health");
  });

  it("keeps ordinary Web REST requests on the same origin when runtime config is absent", () => {
    delete window.__EXCELMANUS_RUNTIME__;
    resolveDirectBackendOrigin.mockReturnValue("");
    expect(buildApiUrl("/sessions")).toBe("/api/v1/sessions");
    expect(buildApiUrl("/chat/stream", { direct: true })).toBe("/api/v1/chat/stream");
  });

  it("honors explicit same-origin runtime configuration", () => {
    window.__EXCELMANUS_RUNTIME__ = { backendOrigin: "same-origin" };
    resolveDirectBackendOrigin.mockReturnValue("");
    expect(buildApiUrl("/sessions")).toBe("/api/v1/sessions");
  });

  it("includes credentials for multipart uploads, workbook previews and file mutations", async () => {
    const fetchMock = vi.fn().mockImplementation(async () => new Response("{}", { headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);
    await uploadFile(new File(["test"], "中文.txt"), "session", "workspace");
    await fetchWorkbookView({ path: "报表.xlsx", sessionId: "session", workspaceKey: "_" }).catch(() => {});
    await workspaceCreateFile("新建.txt", "session", "workspace");
    expect(fetchMock).toHaveBeenCalledTimes(3);
    for (const [url, init] of fetchMock.mock.calls) {
      expect(url).toMatch(/^http:\/\/127\.0\.0\.1:54321\/api\/v1\//);
      expect(init.credentials).toBe("include");
      expect(init.headers.Authorization).toBe("Bearer manage-secret");
    }
    expect(fetchMock.mock.calls[0][1].body).toBeInstanceOf(FormData);
    expect(fetchMock.mock.calls[0][1].headers["Content-Type"]).toBeUndefined();
  });

  it("preserves explicit credentials and never replays failed mutations", async () => {
    const fetchMock = vi.fn().mockRejectedValue(new TypeError("network failure"));
    vi.stubGlobal("fetch", fetchMock);
    await expect(apiFetch("http://127.0.0.1:54321/api/v1/upload", { method: "POST", credentials: "omit" })).rejects.toThrow("network failure");
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0][1].credentials).toBe("omit");
  });
});
