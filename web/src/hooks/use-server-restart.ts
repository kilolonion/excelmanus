"use client";

import { useState, useCallback } from "react";
import { buildDirectHealthUrl } from "@/lib/backend-origin";

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
    const healthUrl = buildDirectHealthUrl();

    const wait = (ms: number) => new Promise((r) => setTimeout(r, ms));

    interface ProbeResult {
      ok: boolean;
      fingerprint?: string;
      gitCommit?: string;
    }

    const probe = async (): Promise<ProbeResult> => {
      try {
        const r = await fetch(healthUrl, {
          method: "GET",
          signal: AbortSignal.timeout(2000),
        });
        if (!r.ok) return { ok: false };
        try {
          const data = await r.json();
          return {
            ok: true,
            fingerprint: data.version_fingerprint ?? undefined,
            gitCommit: data.git_commit ?? undefined,
          };
        } catch {
          return { ok: true };
        }
      } catch {
        return { ok: false };
      }
    };

    // 捕获重启前的版本指纹作为 baseline
    const baseline = await probe();
    const baselineFingerprint = baseline.fingerprint;
    const baselineCommit = baseline.gitCommit;

    const hasVersionChanged = (p: ProbeResult): boolean => {
      if (!p.ok) return false;
      if (baselineFingerprint && p.fingerprint && p.fingerprint !== baselineFingerprint) return true;
      if (baselineCommit && p.gitCommit && p.gitCommit !== baselineCommit) return true;
      return false;
    };

    // Phase 1: 等待后端下线或版本变化（最多 15 秒）
    await wait(2000);
    let versionChanged = false;
    for (let i = 0; i < 26; i++) {
      const p = await probe();
      if (hasVersionChanged(p)) { versionChanged = true; break; }
      if (!p.ok) break;
      await wait(500);
    }

    // 快速重启：版本已变化，直接刷新
    if (versionChanged) {
      window.location.reload();
      return;
    }

    // Phase 2: 等待后端上线（最多 60 秒）
    let online = false;
    for (let i = 0; i < 60; i++) {
      const p = await probe();
      if (p.ok) {
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
