"use client";

import { useEffect, useRef, useCallback, useState } from "react";
import { buildDirectHealthUrl } from "@/lib/backend-origin";

/**
 * 版本轮询 hook — 定时检查后端 /api/v1/health 中的版本标识，
 * 检测到新版本或 API schema 不兼容时通知调用方。
 *
 * 轮询间隔默认 60 秒，页面不可见时暂停。
 */

interface VersionPollState {
  /** 检测到前端 build_id 变化（新版本可用） */
  newVersionAvailable: boolean;
  /** 检测到 api_schema_version 不兼容（远端 > 本地） */
  apiIncompatible: boolean;
  /** 远端后端版本号（用于提示） */
  remoteVersion: string | null;
}

interface HealthVersionFields {
  version?: string;
  build_id?: string | null;
  api_schema_version?: number;
  git_commit?: string | null;
  status?: string;
}

const POLL_INTERVAL_MS = 60_000;

export function useVersionPoll() {
  const [state, setState] = useState<VersionPollState>({
    newVersionAvailable: false,
    apiIncompatible: false,
    remoteVersion: null,
  });

  // 记录首次获取到的基线值
  const baselineRef = useRef<{
    buildId: string | null;
    apiSchemaVersion: number | null;
    initialized: boolean;
  }>({ buildId: null, apiSchemaVersion: null, initialized: false });

  // 用户手动关闭提示后不再弹出（直到页面刷新）
  const dismissedRef = useRef(false);

  const dismiss = useCallback(() => {
    dismissedRef.current = true;
    setState((prev) => ({ ...prev, newVersionAvailable: false }));
  }, []);

  const refreshNow = useCallback(() => {
    window.location.reload();
  }, []);

  useEffect(() => {
    let timer: ReturnType<typeof setInterval> | null = null;
    let aborted = false;

    const poll = async () => {
      if (aborted || document.hidden) return;
      try {
        const resp = await fetch(buildDirectHealthUrl(), {
          method: "GET",
          signal: AbortSignal.timeout(10_000),
        });
        if (!resp.ok) return;
        const data: HealthVersionFields = await resp.json();
        if (data.status === "draining") return; // 后端正在排空，跳过

        const remoteBuildId = data.build_id ?? null;
        const remoteSchema = data.api_schema_version ?? null;

        // 首次：记录基线
        if (!baselineRef.current.initialized) {
          baselineRef.current = {
            buildId: remoteBuildId,
            apiSchemaVersion: remoteSchema,
            initialized: true,
          };
          return;
        }

        const baseline = baselineRef.current;

        // API schema 不兼容检测
        if (
          remoteSchema !== null &&
          baseline.apiSchemaVersion !== null &&
          remoteSchema > baseline.apiSchemaVersion
        ) {
          setState({
            newVersionAvailable: false,
            apiIncompatible: true,
            remoteVersion: data.version ?? null,
          });
          // 3 秒后强制刷新
          setTimeout(() => {
            if (!aborted) window.location.reload();
          }, 3000);
          return;
        }

        // build_id 变化检测（前端有新版本）
        if (
          !dismissedRef.current &&
          remoteBuildId !== null &&
          baseline.buildId !== null &&
          remoteBuildId !== baseline.buildId
        ) {
          setState({
            newVersionAvailable: true,
            apiIncompatible: false,
            remoteVersion: data.version ?? null,
          });
        }
      } catch {
        // 网络错误静默忽略，等下次轮询
      }
    };

    // 延迟首次轮询，避免与页面加载竞争
    const initTimer = setTimeout(() => {
      if (!aborted) {
        poll();
        timer = setInterval(poll, POLL_INTERVAL_MS);
      }
    }, 5000);

    // 页面可见性变化时立即轮询
    const onVisibilityChange = () => {
      if (!document.hidden && baselineRef.current.initialized) {
        poll();
      }
    };
    document.addEventListener("visibilitychange", onVisibilityChange);

    return () => {
      aborted = true;
      clearTimeout(initTimer);
      if (timer) clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisibilityChange);
    };
  }, []);

  return {
    ...state,
    dismiss,
    refreshNow,
  };
}
