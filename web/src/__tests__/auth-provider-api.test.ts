import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/idb-cache", () => ({
  clearAllCachedMessages: vi.fn().mockResolvedValue(undefined),
}));

vi.mock("@/stores/auth-store", () => ({
  useAuthStore: {
    getState: () => ({
      accessToken: "test-access-token",
      refreshToken: "test-refresh-token",
      user: null,
      setTokens: vi.fn(),
      setUser: vi.fn(),
      logout: vi.fn(),
    }),
  },
}));

vi.mock("@/stores/recent-accounts-store", () => ({
  useRecentAccountsStore: {
    getState: () => ({
      recordLogin: vi.fn(),
      updateSavedPassword: vi.fn(),
    }),
  },
}));

vi.mock("@/stores/session-store", () => ({
  useSessionStore: {
    getState: () => ({
      setSessions: vi.fn(),
      setActiveSession: vi.fn(),
    }),
  },
}));

vi.mock("@/stores/chat-store", () => ({
  useChatStore: {
    getState: () => ({
      switchSession: vi.fn(),
    }),
  },
}));

vi.mock("@/stores/excel-store", () => ({
  useExcelStore: {
    getState: () => ({
      clearAllRecentFiles: vi.fn(),
      clearSession: vi.fn(),
    }),
    setState: vi.fn(),
  },
}));

vi.mock("@/lib/api", () => ({
  buildApiUrl: vi.fn((path: string) => `/api/v1${path}`),
}));

import * as authApi from "@/lib/auth-api";

type ProviderApi = {
  fetchProviderDescriptors: () => Promise<{ providers: Array<{ id: string; label: string }> }>;
  providerOAuthStart: (
    providerName: string,
    redirectUri?: string,
  ) => Promise<{ authorize_url: string; state: string; redirect_uri: string; mode: "popup" | "paste" }>;
  connectProvider: (
    providerName: string,
    tokenData: Record<string, unknown>,
  ) => Promise<{ status: string; provider: string }>;
};

const providerApi = authApi as unknown as ProviderApi;

describe("auth-api provider helpers", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    global.fetch = vi.fn();
  });

  it("exposes generic provider helper functions", () => {
    expect(typeof providerApi.fetchProviderDescriptors).toBe("function");
    expect(typeof providerApi.providerOAuthStart).toBe("function");
    expect(typeof providerApi.connectProvider).toBe("function");
  });

  it("fetches provider descriptors with bearer auth", async () => {
    vi.mocked(global.fetch).mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        providers: [{ id: "google-gemini", label: "Google Gemini" }],
      }),
    } as Response);

    const result = await providerApi.fetchProviderDescriptors();

    expect(global.fetch).toHaveBeenCalledWith("/api/v1/auth/providers/descriptors", {
      headers: { Authorization: "Bearer test-access-token" },
    });
    expect(result.providers[0]?.id).toBe("google-gemini");
  });

  it("starts provider oauth with optional localhost redirect uri", async () => {
    vi.mocked(global.fetch).mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        authorize_url: "https://accounts.google.com/o/oauth2/v2/auth?client_id=test",
        state: "state-123",
        redirect_uri: "http://localhost:1455/auth/gemini/callback",
        mode: "popup",
      }),
    } as Response);

    const result = await providerApi.providerOAuthStart(
      "google-gemini",
      "http://localhost:1455/auth/gemini/callback",
    );

    expect(global.fetch).toHaveBeenCalledWith("/api/v1/auth/providers/google-gemini/oauth/start", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: "Bearer test-access-token",
      },
      body: JSON.stringify({
        redirect_uri: "http://localhost:1455/auth/gemini/callback",
      }),
    });
    expect(result.mode).toBe("popup");
  });
});
