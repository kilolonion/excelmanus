import { afterEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";
import { GET, POST } from "@/app/api/mobile-pairing/route";

afterEach(() => vi.unstubAllEnvs());

describe("mobile pairing administration boundary", () => {
  it("does not treat a loopback Host as permission to enable LAN access", async () => {
    vi.stubEnv("EXCELMANUS_MANAGE_TOKEN", "");
    const response = await POST(new NextRequest("http://localhost:3000/api/mobile-pairing", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ action: "issue" }) }));
    expect(response.status).toBe(503);
    expect(response.headers.get("cache-control")).toBe("no-store");
  });
  it("requires the host management credential for status and mutations", async () => {
    vi.stubEnv("EXCELMANUS_MANAGE_TOKEN", "server-token-only-for-this-test");
    for (const method of ["GET", "POST"]) {
      const req = new NextRequest("http://localhost:3000/api/mobile-pairing", { method, headers: { Authorization: "Bearer untrusted-device-token", "Content-Type": "application/json" }, ...(method === "POST" ? { body: '{"action":"issue"}' } : {}) });
      expect((await (method === "POST" ? POST(req) : GET(req))).status).toBe(401);
    }
  });
});
