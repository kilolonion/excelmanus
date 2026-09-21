import { getRuntimeConfig } from "@/lib/runtime-config";

function trimTrailingSlash(value: string): string {
  return value.replace(/\/+$/, "");
}

function isLoopback(hostname: string): boolean {
  const h = hostname.toLowerCase();
  return h === "localhost" || h === "127.0.0.1" || h.startsWith("127.") || h === "::1" || h === "[::1]";
}

function canonicalHost(hostname: string): string {
  const h = hostname.toLowerCase();
  return h === "[::1]" ? "::1" : h;
}

/**
 * 所有 API（含 health、登录和 SSE）共用同一个后端地址。
 *
 * 优先级：
 * 1) EXCELMANUS_RUNTIME_BACKEND_ORIGIN（运行时）
 * 2) NEXT_PUBLIC_BACKEND_ORIGIN（构建时）
 * 3) 默认同源（返回空字符串），由前端代理决定后端端口。
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
        // 配置成 loopback 时对齐到当前页面主机名：
        // 局域网访问要避开写死的 localhost；localhost 与 127.0.0.1 也是不同源。
        if (
          isLoopback(cfgUrl.hostname)
          && canonicalHost(cfgUrl.hostname) !== canonicalHost(window.location.hostname)
        ) {
          cfgUrl.hostname = window.location.hostname;
          return trimTrailingSlash(cfgUrl.href);
        }
      } catch {
        // 非 URL 字符串（如裸主机名）按原值使用
      }
    }

    return trimTrailingSlash(configured);
  }

  return "";
}

export function buildDirectHealthUrl(): string {
  return `${resolveDirectBackendOrigin()}/api/v1/health`;
}
