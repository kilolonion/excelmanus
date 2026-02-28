"use client";

import { useEffect, useRef, useState } from "react";
import { buildApiUrl } from "@/lib/api";
import { useAuthStore } from "@/stores/auth-store";

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
  const [error, setError] = useState(false);
  const prevUrlRef = useRef<string | null>(null);

  useEffect(() => {
    if (!apiPath || !enabled) {
      setBlobUrl(null);
      setLoading(false);
      setError(false);
      return;
    }

    const url = buildApiUrl(apiPath);

    // 避免重复请求同一 URL
    if (url === prevUrlRef.current && blobUrl) return;

    let cancelled = false;
    let objectUrl: string | null = null;

    const load = async () => {
      setLoading(true);
      setError(false);

      try {
        const token = useAuthStore.getState().accessToken;
        const headers: Record<string, string> = {};
        if (token) headers["Authorization"] = `Bearer ${token}`;

        const res = await fetch(url, { headers });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);

        const blob = await res.blob();
        if (cancelled) return;

        objectUrl = URL.createObjectURL(blob);
        prevUrlRef.current = url;
        setBlobUrl(objectUrl);
        setLoading(false);
      } catch {
        if (!cancelled) {
          setLoading(false);
          setError(true);
        }
      }
    };

    load();

    return () => {
      cancelled = true;
      if (objectUrl) {
        URL.revokeObjectURL(objectUrl);
      }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [apiPath, enabled]);

  // 组件卸载时清理 blob URL
  useEffect(() => {
    return () => {
      if (blobUrl) URL.revokeObjectURL(blobUrl);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return { blobUrl, loading, error };
}
