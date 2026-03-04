"use client";

import { useEffect, useRef, useCallback, useState } from "react";
import { buildDirectHealthUrl } from "@/lib/backend-origin";

/**
 * 版本轮询 hook — 定时检查后端 /api/v1/health 中的版本标识，
 * 检测到新版本或 API schema 不兼容时通知调用方。
 *
 * 自适应轮询间隔：首次 2s 延迟 → 15s 快速确认 → 30s 稳定轮询。
 * 页面不可见时暂停，可见时立即轮询。
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
  version_fingerprint?: string | null;
  api_schema_version?: number;
  git_commit?: string | null;
  status?: string;
  min_frontend_build_id?: string | null;
  min_backend_version?: string | null;
}

const INIT_DELAY_MS = 2_000;
const QUICK_CONFIRM_MS = 15_000;
const STEADY_INTERVAL_MS = 30_000;

export function useVersionPoll() {
  const [state, setState] = useState<VersionPollState>({
    newVersionAvailable: false,
    apiIncompatible: false,
    remoteVersion: null,
  });

  // 记录首次获取到的基线值
  const baselineRef = useRef<{
    buildId: string | null;
    fingerprint: string | null;
    apiSchemaVersion: number | null;
    initialized: boolean;
  }>({ buildId: null, fingerprint: null, apiSchemaVersion: null, initialized: false });

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
    let timer: ReturnType<typeof setTimeout> | null = null;
    let aborted = false;
    let pollCount = 0;

    const schedulePoll = () => {
      if (aborted) return;
      const interval = pollCount <= 1 ? QUICK_CONFIRM_MS : STEADY_INTERVAL_MS;
      timer = setTimeout(() => {
        poll().then(schedulePoll);
      }, interval);
    };

    const poll = async () => {
      if (aborted || document.hidden) return;
      pollCount++;
      try {
        const resp = await fetch(buildDirectHealthUrl(), {
          method: "GET",
          signal: AbortSignal.timeout(10_000),
        });
        if (!resp.ok) return;
        const data: HealthVersionFields = await resp.json();
        if (data.status === "draining") return; // 后端正在排空，跳过

        const remoteBuildId = data.build_id ?? null;
        const remoteFingerprint = data.version_fingerprint ?? null;
        const remoteSchema = data.api_schema_version ?? null;

        // 首次：记录基线
        if (!baselineRef.current.initialized) {
          baselineRef.current = {
            buildId: remoteBuildId,
            fingerprint: remoteFingerprint,
            apiSchemaVersion: remoteSchema,
            initialized: true,
          };
          return;
        }

        const baseline = baselineRef.current;

        // API schema 不兼容检测（不自动刷新，交给用户决定）
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
          return;
        }

        // 细粒度兼容性：后端声明了最低前端 build_id 要求
        const minBuildId = data.min_frontend_build_id ?? null;
        if (
          minBuildId !== null &&
          baseline.buildId !== null &&
          baseline.buildId !== minBuildId &&
          baseline.buildId < minBuildId
        ) {
          setState({
            newVersionAvailable: false,
            apiIncompatible: true,
            remoteVersion: data.version ?? null,
          });
          return;
        }

        // build_id 变化检测（前端有新版本）
        // 回退链：build_id → version_fingerprint（split topology 下 build_id 双端可能为 null）
        let changed = false;
        if (remoteBuildId !== null && baseline.buildId !== null) {
          changed = remoteBuildId !== baseline.buildId;
        } else if (remoteFingerprint !== null && baseline.fingerprint !== null) {
          changed = remoteFingerprint !== baseline.fingerprint;
        }

        if (!dismissedRef.current && changed) {
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
        poll().then(schedulePoll);
      }
    }, INIT_DELAY_MS);

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
      if (timer) clearTimeout(timer);
      document.removeEventListener("visibilitychange", onVisibilityChange);
    };
  }, []);

  return {
    ...state,
    dismiss,
    refreshNow,
  };
}
