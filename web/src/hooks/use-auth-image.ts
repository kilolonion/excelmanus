"use client";

import { useEffect, useRef, useState } from "react";
import { buildApiUrl, directFetch, getAuthHeaders } from "@/lib/api";
import { formatApiErrorMessage } from "@/lib/api-error";

/**
 * 通过 fetch + Authorization header 加载图片为 blob URL。
 *
 * 浏览器原生 `<img src>` 无法携带 Authorization header，
 * 当后端启用认证时，直接拼 API URL 会返回 401。
 * 本 hook 用 JS fetch 携带 Bearer token 获取图片二进制流，
 * 转为 ObjectURL 供 `<img src>` 使用。
 *
 * @param apiPath  不含 /api/v1 前缀的路径，如 `/files/image?path=...&session_id=...`
 * @param enabled  是否启用加载（默认 true），用于延迟加载场景
 */
export function useAuthImage(apiPath: string | undefined, enabled = true) {
  const [blobUrl, setBlobUrl] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const activeUrlRef = useRef<string | null>(null);

  useEffect(() => {
    if (!apiPath || !enabled) {
      setBlobUrl(null);
      setLoading(false);
      setError(null);
      return;
    }

    // 桌面版后端端口由 Electron 启动时动态分配。图片必须显式走运行时
    // 后端地址，不能落到 Next.js 构建时固化的同源 rewrite。
    const url = buildApiUrl(apiPath, { direct: true });

    let cancelled = false;
    let objectUrl: string | null = null;
    const controller = new AbortController();

    const load = async () => {
      setLoading(true);
      setError(null);
      setBlobUrl(null);

      try {
        const res = await directFetch(url, {
          headers: { ...getAuthHeaders() },
          signal: controller.signal,
        });
        if (!res.ok) {
          const data = await res.json().catch(() => ({}));
          throw new Error(formatApiErrorMessage(data, res.status));
        }
        const contentType = (res.headers.get("content-type") || "").toLowerCase();
        if (!contentType.startsWith("image/")) {
          throw new Error(`服务器返回了非图片内容（${contentType || "未知类型"}）`);
        }

        const blob = await res.blob();
        if (cancelled) return;

        objectUrl = URL.createObjectURL(blob);
        activeUrlRef.current = objectUrl;
        setBlobUrl(objectUrl);
        setLoading(false);
      } catch (err) {
        if (!cancelled) {
          setLoading(false);
          setError(err instanceof Error ? err.message : "图片请求失败");
        }
      }
    };

    load();

    return () => {
      cancelled = true;
      controller.abort();
      if (objectUrl) {
        URL.revokeObjectURL(objectUrl);
        if (activeUrlRef.current === objectUrl) activeUrlRef.current = null;
      }
    };
  }, [apiPath, enabled]);

  // 兜底清理：正常路径由上面的 effect cleanup 回收。
  useEffect(() => {
    return () => {
      if (activeUrlRef.current) URL.revokeObjectURL(activeUrlRef.current);
    };
  }, []);

  return { blobUrl, loading, error };
}
