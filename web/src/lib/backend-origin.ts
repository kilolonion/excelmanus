import { getRuntimeConfig } from "@/lib/runtime-config";

function trimTrailingSlash(value: string): string {
  return value.replace(/\/+$/, "");
}

function isLoopback(hostname: string): boolean {
  const h = hostname.toLowerCase();
  return h === "localhost" || h === "127.0.0.1" || h.startsWith("127.") || h === "::1";
}

function isPrivateIpv4(hostname: string): boolean {
  const m = hostname.match(/^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$/);
  if (!m) return false;
  const a = Number(m[1]);
  const b = Number(m[2]);
  if (a === 10) return true;
  if (a === 192 && b === 168) return true;
  if (a === 172 && b >= 16 && b <= 31) return true;
  return false;
}

function shouldUseLocalPortFallback(hostname: string, protocol: string): boolean {
  if (protocol !== "http:") return false;
  const h = hostname.toLowerCase();
  if (isLoopback(h)) return true;
  if (isPrivateIpv4(h)) return true;
  if (h.endsWith(".local")) return true;
  return false;
}

/**
 * 解析后端直连地址（用于 SSE/健康探测等直连场景）。
 *
 * 优先级：
 * 1) EXCELMANUS_RUNTIME_BACKEND_ORIGIN（运行时）
 * 2) NEXT_PUBLIC_BACKEND_ORIGIN（构建时）
 * 3) 本地开发回退（仅 http 且 localhost/局域网主机时）→ http://{hostname}:8000
 * 4) 其他场景默认同源（返回空字符串）
 */
export function resolveDirectBackendOrigin(): string {
  const configured = getRuntimeConfig("backendOrigin", process.env.NEXT_PUBLIC_BACKEND_ORIGIN?.trim());
  if (configured) {
    if (configured.toLowerCase() === "same-origin") return "";

    if (typeof window !== "undefined") {
      try {
        const cfgUrl = new URL(configured);
        // HTTPS 页面不能直连 HTTP 后端，回退同源避免 mixed-content。
        if (window.location.protocol === "https:" && cfgUrl.protocol === "http:") {
          return "";
        }
        // 配置成 loopback 但页面并非本机访问时，改用当前主机名 + 原端口。
        if (isLoopback(cfgUrl.hostname) && !isLoopback(window.location.hostname)) {
          const port = cfgUrl.port || "8000";
          return `${cfgUrl.protocol}//${window.location.hostname}:${port}`;
        }
      } catch {
        // 非 URL 字符串（如裸主机名）按原值使用
      }
    }

    return trimTrailingSlash(configured);
  }

  if (typeof window !== "undefined") {
    if (shouldUseLocalPortFallback(window.location.hostname, window.location.protocol)) {
      return `http://${window.location.hostname}:8000`;
    }
  }
  return "";
}

export function buildDirectHealthUrl(): string {
  return `${resolveDirectBackendOrigin()}/api/v1/health`;
}
