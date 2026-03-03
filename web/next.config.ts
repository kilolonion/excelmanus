import type { NextConfig } from "next";
import os from "os";
import fs from "fs";
import path from "path";

function getLocalNetworkOrigins(port = 3000): string[] {
  const origins: string[] = [];
  for (const addrs of Object.values(os.networkInterfaces())) {
    if (!addrs) continue;
    for (const addr of addrs) {
      if (!addr.internal && addr.family === "IPv4") {
        origins.push(`http://${addr.address}:${port}`);
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
  output: "standalone",
  typescript: { ignoreBuildErrors: true },
  allowedDevOrigins: getLocalNetworkOrigins(),
  env: {
    NEXT_PUBLIC_APP_VERSION: getProjectVersion(),
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
