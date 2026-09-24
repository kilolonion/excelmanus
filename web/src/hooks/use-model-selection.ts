"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { activateModelProfile, fetchAvailableModels } from "@/lib/model-config-api";
import type { ModelInfo } from "@/lib/types";
import { useUIStore } from "@/stores/ui-store";

/** Shared behavior for the chat and sidebar pickers. */
export function useModelSelection() {
  const currentModel = useUIStore((s) => s.currentModel);
  const version = useUIStore((s) => s.modelProfileVersion);
  const [models, setModels] = useState<ModelInfo[]>([]);
  const [switching, setSwitching] = useState(false);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [switchError, setSwitchError] = useState<string | null>(null);
  const request = useRef(0);
  const pending = useRef(false);
  const reload = useCallback(async () => {
    const id = ++request.current;
    const snapshot = useUIStore.getState().modelProfileVersion;
    setLoading(true);
    try {
      const data = await fetchAvailableModels();
      if (request.current !== id || snapshot !== useUIStore.getState().modelProfileVersion) return;
      setModels(data.models);
      setLoadError(null);
      const active = data.models.find((m) => m.active);
      useUIStore.getState().setCurrentModel(active?.name || "");
      useUIStore.getState().setVisionCapable(active?.supports_vision ?? null);
    } catch (error) {
      if (request.current === id) setLoadError(error instanceof Error ? error.message : "无法读取模型列表");
    } finally {
      if (request.current === id) setLoading(false);
    }
  }, []);
  useEffect(() => {
    void reload();
    return () => { request.current += 1; };
  }, [reload, version]);

  const selectModel = async (name: string) => {
    if (pending.current) return false;
    setSwitchError(null);
    if (name === useUIStore.getState().currentModel) return true;
    pending.current = true;
    setSwitching(true);
    try {
      await activateModelProfile(name);
      return true;
    } catch (error) {
      setSwitchError(error instanceof Error ? error.message : "切换失败");
      return false;
    } finally {
      pending.current = false;
      setSwitching(false);
    }
  };
  return { currentModel, models, switching, loading, loadError, switchError, error: switchError || loadError, reload, selectModel };
}
