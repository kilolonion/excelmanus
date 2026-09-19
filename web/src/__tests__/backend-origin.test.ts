import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { getRuntimeConfig } from "@/lib/runtime-config";
import { resolveDirectBackendOrigin } from "@/lib/backend-origin";

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

  it("falls back to the current loopback hostname on port 8000", () => {
    mockLocation("127.0.0.1");
    expect(resolveDirectBackendOrigin()).toBe("http://127.0.0.1:8000");
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

  it("brackets IPv6 loopback in the fallback URL", () => {
    mockLocation("::1");
    expect(resolveDirectBackendOrigin()).toBe("http://[::1]:8000");
  });
});
