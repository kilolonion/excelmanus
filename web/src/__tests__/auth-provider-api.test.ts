import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api", () => ({
  buildApiUrl: vi.fn((path: string) => `/api/v1${path}`),
}));

import * as authApi from "@/lib/auth-api";

describe("auth-api Codex OAuth helpers", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    global.fetch = vi.fn();
  });

  it("exposes Codex OAuth helpers and no identity login helpers", () => {
    expect(typeof authApi.codexOAuthStart).toBe("function");
    expect(typeof authApi.codexOAuthExchange).toBe("function");
    expect(typeof authApi.codexDeviceCodeStart).toBe("function");
    expect(typeof authApi.codexDeviceCodePoll).toBe("function");
    expect("login" in authApi).toBe(false);
    expect("register" in authApi).toBe(false);
    expect("fetchChannelLinks" in authApi).toBe(false);
  });

  it("starts Codex OAuth without bearer auth", async () => {
    vi.mocked(global.fetch).mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        authorize_url: "https://auth.openai.com/oauth/authorize?client_id=test",
        state: "state-123",
        redirect_uri: "http://localhost:3000/auth/codex/callback",
        mode: "popup",
      }),
    } as Response);

    const result = await authApi.codexOAuthStart("http://localhost:3000/auth/codex/callback");

    expect(global.fetch).toHaveBeenCalledWith("/api/v1/auth/providers/openai-codex/oauth/start", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        redirect_uri: "http://localhost:3000/auth/codex/callback",
      }),
    });
    expect(result.mode).toBe("popup");
    expect(result.redirect_uri).toContain("/auth/codex/callback");
  });

  it("fetches Codex status without bearer auth", async () => {
    vi.mocked(global.fetch).mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        status: "connected",
        provider: "openai-codex",
        email: "user@example.com",
      }),
    } as Response);

    const result = await authApi.fetchCodexStatus();

    expect(global.fetch).toHaveBeenCalledWith("/api/v1/auth/providers/openai-codex/status");
    expect(result.provider).toBe("openai-codex");
    expect(result.email).toBe("user@example.com");
  });
});
