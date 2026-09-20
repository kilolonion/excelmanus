import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const resolveDirectBackendOrigin = vi.hoisted(() => vi.fn());

vi.mock("@/lib/backend-origin", () => ({ resolveDirectBackendOrigin }));

import { apiGet } from "@/lib/api";

describe("runtime API client", () => {
  beforeEach(() => {
    resolveDirectBackendOrigin.mockReturnValue("http://127.0.0.1:54321");
    vi.stubGlobal("window", {
      location: { origin: "http://127.0.0.1:3000" },
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
});
