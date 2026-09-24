"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { fetchModelConfig } from "@/lib/model-config-api";
import type { ModelConfig } from "@/lib/model-config";
import { settingsCache } from "@/lib/settings-cache";
import { useUIStore } from "@/stores/ui-store";

export function useModelConfig() {
  const version = useUIStore((s) => s.modelProfileVersion);
  const [config, setConfig] = useState<ModelConfig | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const request = useRef(0);

  const fetchConfig = useCallback(async (force = false) => {
    const id = ++request.current;
    const snapshot = useUIStore.getState().modelProfileVersion;
    setLoadError(null);
    const cached = !force && settingsCache.get<ModelConfig>("/config/models");
    if (cached) {
      setConfig(cached);
      setLoading(false);
      return;
    }
    setLoading(true);
    try {
      const next = await fetchModelConfig();
      if (request.current !== id || snapshot !== useUIStore.getState().modelProfileVersion) return;
      settingsCache.set("/config/models", next);
      setConfig(next);
    } catch (error) {
      if (request.current === id) setLoadError(error instanceof Error ? error.message : "无法读取模型配置");
    } finally {
      if (request.current === id) setLoading(false);
    }
  }, []);

  useEffect(() => {
    void fetchConfig();
    return () => { request.current += 1; };
  }, [fetchConfig, version]);

  return { config, loading, loadError, fetchConfig };
}
