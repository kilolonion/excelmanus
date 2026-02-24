import type { NextConfig } from "next";
import os from "os";

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

const nextConfig: NextConfig = {
  output: "standalone",
  allowedDevOrigins: getLocalNetworkOrigins(),
  async rewrites() {
    const backend =
      process.env.BACKEND_INTERNAL_URL || "http://localhost:8000";
    return [
      {
        source: "/api/v1/:path*",
        destination: `${backend.replace(/\/+$/, "")}/api/v1/:path*`,
      },
    ];
  },
};

export default nextConfig;
