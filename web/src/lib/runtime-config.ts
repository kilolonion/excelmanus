/**
 * 运行时配置模块 — 解决 NEXT_PUBLIC_* 构建时固化的问题。
 *
 * Next.js 的 NEXT_PUBLIC_* 环境变量在 `next build` 时被内联到客户端 JS 中，
 * 导致同一个 Docker 镜像无法在运行时切换后端地址。
 *
 * 本模块通过 Server Component（layout.tsx）在 HTML 中注入 <script> 标签，
 * 将非 NEXT_PUBLIC_ 前缀的环境变量暴露到 `window.__EXCELMANUS_RUNTIME__`，
 * 实现真正的运行时配置。
 *
 * 优先级（以 backendOrigin 为例）：
 *   1. window.__EXCELMANUS_RUNTIME__.backendOrigin  （运行时注入，最高优先）
 *   2. process.env.NEXT_PUBLIC_BACKEND_ORIGIN       （构建时内联，向后兼容）
 *   3. 动态回退                                     （开发环境 http://{hostname}:8000）
 */

export interface ExcelManusRuntimeConfig {
  /** 后端直连地址（运行时）。等效于 NEXT_PUBLIC_BACKEND_ORIGIN。 */
  backendOrigin?: string;
  /** 认证是否启用（运行时）。等效于 NEXT_PUBLIC_AUTH_ENABLED。 */
  authEnabled?: string;
}

declare global {
  interface Window {
    __EXCELMANUS_RUNTIME__?: ExcelManusRuntimeConfig;
  }
}

/**
 * 获取运行时配置值。
 *
 * @param key   配置键
 * @param buildTimeEnv  对应的 NEXT_PUBLIC_* 构建时环境变量值（向后兼容）
 * @returns 配置值，未配置时返回 undefined
 */
export function getRuntimeConfig<K extends keyof ExcelManusRuntimeConfig>(
  key: K,
  buildTimeEnv?: string,
): string | undefined {
  // 1) 运行时注入（最高优先）
  if (typeof window !== "undefined" && window.__EXCELMANUS_RUNTIME__) {
    const val = window.__EXCELMANUS_RUNTIME__[key];
    if (val !== undefined && val !== null && val !== "") return val;
  }
  // 2) 构建时环境变量（向后兼容）
  if (buildTimeEnv !== undefined && buildTimeEnv !== null && buildTimeEnv !== "") {
    return buildTimeEnv;
  }
  // 3) 未配置
  return undefined;
}
