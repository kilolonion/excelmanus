import type { NextConfig } from "next";
import os from "os";
import fs from "fs";
import path from "path";
import { randomUUID } from "node:crypto";

// Embed the same identity in the loaded browser bundle and the frontend's own
// version endpoint. Backend-only fingerprints cannot detect split deployments.
const webBuildId = process.env.EXCELMANUS_WEB_BUILD_ID || randomUUID();
process.env.EXCELMANUS_WEB_BUILD_ID = webBuildId;

function getDevFrontendPort(): number {
  const raw = process.env.PORT || process.env.EXCELMANUS_FRONTEND_PORT || "3000";
  const parsed = Number.parseInt(raw.split(",")[0] ?? "3000", 10);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : 3000;
}

function getLocalNetworkOrigins(port = 3000): string[] {
  const origins: string[] = [
    "localhost",
    "127.0.0.1",
    "[::1]",
    `http://localhost:${port}`,
    `http://127.0.0.1:${port}`,
    `http://[::1]:${port}`,
  ];
  // PRoot 沙箱下 os.networkInterfaces() 可能抛 EACCES，降级为仅回环地址
  let interfaces: ReturnType<typeof os.networkInterfaces> = {};
  try {
    interfaces = os.networkInterfaces();
  } catch {
    interfaces = {};
  }
  for (const addrs of Object.values(interfaces)) {
    if (!addrs) continue;
    for (const addr of addrs) {
      if (!addr.internal && addr.family === "IPv4") {
        origins.push(addr.address, `http://${addr.address}:${port}`);
      }
    }
  }
  return origins;
}

function getProjectVersion(): string {
  try {
    const tomlPath = path.resolve(process.cwd(), "..", "pyproject.toml");
    const content = fs.readFileSync(tomlPath, "utf-8");
    const match = content.match(/^version\s*=\s*"([^"]+)"/m);
    return match?.[1] ?? "0.0.0";
  } catch {
    return "0.0.0";
  }
}

const nextConfig: NextConfig = {
  generateBuildId: async () => webBuildId,
  output: "standalone",
  // 固定 tracing 根目录为 web/，防止上级目录中的残留 lockfile
  // 被误判为 workspace root，导致 standalone 产物嵌套错位。
  outputFileTracingRoot: __dirname,
  typescript: { ignoreBuildErrors: false },
  allowedDevOrigins: getLocalNetworkOrigins(getDevFrontendPort()),
  env: {
    NEXT_PUBLIC_APP_VERSION: getProjectVersion(),
    NEXT_PUBLIC_WEB_BUILD_ID: webBuildId,
  },
  async rewrites() {
    // BACKEND_INTERNAL_URL: Next.js 服务端 rewrite 代理的目标地址。
    // 从 web/.env.production（生产）或 web/.env.local（开发）读取。
    // 前后端同机: http://localhost:8000
    // 前后端分离: http://<后端IP>:8000
    const backend =
      process.env.BACKEND_INTERNAL_URL || "http://127.0.0.1:8000";
    return [
      {
        source: "/api/v1/:path*",
        destination: `${backend.replace(/\/+$/, "")}/api/v1/:path*`,
      },
    ];
  },
};

export default nextConfig;
