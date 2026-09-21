import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { getRuntimeConfig } from "@/lib/runtime-config";
import { resolveDirectBackendOrigin } from "@/lib/backend-origin";
import { buildApiUrl } from "@/lib/api";

vi.mock("@/lib/runtime-config", () => ({
  getRuntimeConfig: vi.fn(),
}));

const mockedGetRuntimeConfig = vi.mocked(getRuntimeConfig);

function mockLocation(hostname: string, protocol = "http:") {
  const host = hostname.includes(":") && !hostname.startsWith("[") ? `[${hostname}]` : hostname;
  vi.stubGlobal("window", {
    location: {
      hostname,
      protocol,
      origin: `${protocol}//${host}:3000`,
    },
  });
}

describe("resolveDirectBackendOrigin", () => {
  beforeEach(() => {
    mockedGetRuntimeConfig.mockReset();
    mockedGetRuntimeConfig.mockReturnValue(undefined);
  });

  afterEach(() => {
    vi.unstubAllEnvs();
    vi.unstubAllGlobals();
  });

  it("does not guess a backend port when no runtime origin is configured", () => {
    mockLocation("127.0.0.1");
    expect(resolveDirectBackendOrigin()).toBe("");
  });

  it("remaps configured localhost onto 127.0.0.1 when the page uses 127", () => {
    mockedGetRuntimeConfig.mockReturnValue("http://localhost:8000");
    mockLocation("127.0.0.1");
    expect(resolveDirectBackendOrigin()).toBe("http://127.0.0.1:8000");
  });

  it("remaps configured 127.0.0.1 onto localhost when the page uses localhost", () => {
    mockedGetRuntimeConfig.mockReturnValue("http://127.0.0.1:8000");
    mockLocation("localhost");
    expect(resolveDirectBackendOrigin()).toBe("http://localhost:8000");
  });

  it("keeps a configured loopback origin when the page hostname matches", () => {
    mockedGetRuntimeConfig.mockReturnValue("http://localhost:8000");
    mockLocation("localhost");
    expect(resolveDirectBackendOrigin()).toBe("http://localhost:8000");
  });

  it("routes ordinary desktop REST calls to the runtime-assigned backend port", () => {
    mockedGetRuntimeConfig.mockReturnValue("http://127.0.0.1:54321");
    mockLocation("127.0.0.1");
    expect(buildApiUrl("/files/read?path=notes.txt")).toBe(
      "http://127.0.0.1:54321/api/v1/files/read?path=notes.txt",
    );
  });

  it("keeps the API relative when runtime config explicitly requests same-origin", () => {
    mockedGetRuntimeConfig.mockReturnValue("same-origin");
    mockLocation("example.com", "https:");
    expect(buildApiUrl("/sessions")).toBe("/api/v1/sessions");
  });

  it("keeps same-origin mode for IPv6 loopback without configuration", () => {
    mockLocation("::1");
    expect(resolveDirectBackendOrigin()).toBe("");
  });
});
