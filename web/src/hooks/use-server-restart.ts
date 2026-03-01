"use client";

import { useState, useCallback } from "react";

/**
 * 后端重启健康探测 hook。
 *
 * 使用方式：
 *   const { restarting, restartTimeout, triggerRestart } = useServerRestart();
 *   // 在保存配置后如果返回 restarting，调用 triggerRestart()
 *   // 组件中渲染 <ServerRestartOverlay restarting={restarting} restartTimeout={restartTimeout} />
 */
export function useServerRestart() {
  const [restarting, setRestarting] = useState(false);
  const [restartTimeout, setRestartTimeout] = useState(false);

  const triggerRestart = useCallback(async () => {
    setRestarting(true);
    setRestartTimeout(false);

    // 直连后端健康检查 URL（绕过 Next.js 代理和 auth 拦截）
    const configured = process.env.NEXT_PUBLIC_BACKEND_ORIGIN?.trim();
    const backendOrigin = configured
      ? configured.toLowerCase() === "same-origin"
        ? ""
        : configured.replace(/\/+$/, "")
      : `http://${window.location.hostname}:8000`;
    const healthUrl = `${backendOrigin}/api/v1/health`;

    const wait = (ms: number) => new Promise((r) => setTimeout(r, ms));
    const probe = async (): Promise<boolean> => {
      try {
        const r = await fetch(healthUrl, {
          method: "GET",
          signal: AbortSignal.timeout(2000),
        });
        return r.ok;
      } catch {
        return false;
      }
    };

    // Phase 1: 等待后端下线（最多 15 秒）
    await wait(2000);
    for (let i = 0; i < 26; i++) {
      if (!(await probe())) break;
      await wait(500);
    }

    // Phase 2: 等待后端上线（最多 60 秒）
    let online = false;
    for (let i = 0; i < 60; i++) {
      if (await probe()) {
        online = true;
        break;
      }
      await wait(1000);
    }

    if (online) {
      window.location.reload();
    } else {
      setRestartTimeout(true);
    }
  }, []);

  const reset = useCallback(() => {
    setRestarting(false);
    setRestartTimeout(false);
  }, []);

  return { restarting, restartTimeout, triggerRestart, reset };
}
