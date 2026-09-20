import { beforeEach, describe, expect, it, vi } from "vitest";

const apiGet = vi.hoisted(() => vi.fn());
const apiPost = vi.hoisted(() => vi.fn());
const apiDelete = vi.hoisted(() => vi.fn());

vi.mock("@/lib/api", () => ({ apiGet, apiPost, apiDelete }));

import * as authApi from "@/lib/auth-api";

describe("auth-api Codex OAuth helpers", () => {
  beforeEach(() => {
    vi.clearAllMocks();
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

  it("starts Codex OAuth through the shared runtime API client", async () => {
    apiPost.mockResolvedValueOnce({
      authorize_url: "https://auth.openai.com/oauth/authorize?client_id=test",
      state: "state-123",
      redirect_uri: "http://localhost:1455/auth/callback",
      mode: "popup",
    });

    const result = await authApi.codexOAuthStart("http://localhost:3000/auth/callback");

    expect(apiPost).toHaveBeenCalledWith(
      "/auth/providers/openai-codex/oauth/start",
      { redirect_uri: "http://localhost:3000/auth/callback" },
    );
    expect(result.mode).toBe("popup");
    expect(result.redirect_uri).toBe("http://localhost:1455/auth/callback");
  });

  it("fetches Codex status through the shared runtime API client", async () => {
    apiGet.mockResolvedValueOnce({
      status: "connected",
      provider: "openai-codex",
      email: "user@example.com",
    });

    const result = await authApi.fetchCodexStatus();

    expect(apiGet).toHaveBeenCalledWith("/auth/providers/openai-codex/status");
    expect(result.provider).toBe("openai-codex");
    expect(result.email).toBe("user@example.com");
  });
});
