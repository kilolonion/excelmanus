"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { apiGet, apiPut } from "@/lib/api";
import { settingsCache } from "@/lib/settings-cache";
import { useJevStore } from "@/stores/jev-store";
import {
  EMPTY_JEV_DRAFT,
  JEV_DEFAULT_MODEL,
  JEV_PROVIDER_KEYS,
  JEV_ROLE_KEYS,
  buildJevPayload,
  jevConfiguredFromRuntime,
  jevChatEnabledFromRuntime,
  parseJevGate,
  parseJevProtocol,
  type JevDraft,
  type JevProviderPublic,
} from "@/lib/jev-settings";

export type JevRuntime = {
  jev_experimental_enabled: boolean;
  jev_enabled: string;
  jev_exposure: string;
  jev_mode_hint: boolean;
  jev_observation: string;
  jev_verification: string;
  jev_recovery: string;
  jev_ui_hint: boolean;
  jev_model: string;
  jev_timeout_seconds: number;
  jev_enforce_ready?: boolean;
  jev_active_provider?: string;
  jev_providers?: JevProviderPublic[];
  model_canonical_match_enabled?: boolean;
  ai_gateway?: { configured: boolean; last4: string };
  typesafe?: { configured: boolean; last4: string };
};

function snapshotFromRuntime(data: JevRuntime): JevDraft {
  return {
    jev_enabled: parseJevGate(data.jev_enabled),
    jev_exposure: parseJevGate(data.jev_exposure),
    jev_observation: parseJevGate(data.jev_observation),
    jev_verification: parseJevGate(data.jev_verification),
    jev_recovery: parseJevGate(data.jev_recovery),
    jev_mode_hint: Boolean(data.jev_mode_hint),
    jev_ui_hint: Boolean(data.jev_ui_hint),
    jev_model: data.jev_model || JEV_DEFAULT_MODEL,
    jev_timeout_seconds: data.jev_timeout_seconds ?? 1.5,
    jev_active_provider: data.jev_active_provider || "",
    ai_gateway_api_key: "",
    model_canonical_match_enabled: data.model_canonical_match_enabled ?? true,
  };
}

function normalizeProviders(raw: JevProviderPublic[] | undefined): JevProviderPublic[] {
  if (!raw) return [];
  return raw.map((item) => ({
    ...item,
    protocol: parseJevProtocol(item.protocol),
  }));
}

export function useJevSettings() {
  const [runtime, setRuntime] = useState<JevRuntime | null>(null);
  const [draft, setDraft] = useState<JevDraft>(EMPTY_JEV_DRAFT);
  const [baseline, setBaseline] = useState<JevDraft>(EMPTY_JEV_DRAFT);
  const [providers, setProviders] = useState<JevProviderPublic[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const saveInFlight = useRef(false);
  const savedTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => () => { if (savedTimer.current) clearTimeout(savedTimer.current); }, []);

  const applyRuntime = useCallback((data: JevRuntime) => {
    const next = snapshotFromRuntime(data);
    setRuntime(data);
    setDraft(next);
    setBaseline(next);
    setProviders(normalizeProviders(data.jev_providers));
    // 关掉总闸/密钥后立即断开对话侧 Jev：清 traces、取消钉住、隐藏 chrome。
    useJevStore.getState().setChatEnabled(jevChatEnabledFromRuntime(data));
  }, []);

  const loadRuntime = useCallback(async () => {
    setError(null);
    const cached = settingsCache.get<JevRuntime>("/config/runtime");
    if (cached?.jev_providers) {
      applyRuntime(cached);
      setLoading(false);
      return;
    }
    setLoading(true);
    try {
      const data = await apiGet<JevRuntime>("/config/runtime", { direct: true });
      settingsCache.set("/config/runtime", data);
      applyRuntime(data);
    } catch {
      setError("无法读取 Jev 配置");
    } finally {
      setLoading(false);
    }
  }, [applyRuntime]);

  useEffect(() => {
    void loadRuntime();
  }, [loadRuntime]);

  const persist = useCallback(async (payload: Record<string, unknown>) => {
    if (saveInFlight.current) return false;
    if (Object.keys(payload).length === 0) return true;
    saveInFlight.current = true;
    setSaving(true);
    setSaved(false);
    setError(null);
    try {
      await apiPut("/config/runtime", payload, { direct: true });
      settingsCache.delete("/config/runtime");
      const data = await apiGet<JevRuntime>("/config/runtime", { direct: true });
      settingsCache.set("/config/runtime", data);
      applyRuntime(data);
      setSaved(true);
      if (savedTimer.current) clearTimeout(savedTimer.current);
      savedTimer.current = setTimeout(() => setSaved(false), 2500);
      return true;
    } catch (err) {
      setError(err instanceof Error ? err.message : "保存失败");
      return false;
    } finally {
      saveInFlight.current = false;
      setSaving(false);
    }
  }, [applyRuntime]);

  const save = useCallback(async (keys: readonly (keyof JevDraft)[]) => {
    return persist(buildJevPayload(draft, baseline, keys));
  }, [baseline, draft, persist]);

  const saveProviders = useCallback(async (
    next: JevProviderPublic[],
    extras?: {
      activeId?: string;
      apiKey?: string;
      providerId?: string;
      clearKey?: boolean;
      timeout?: number;
      model?: string;
    },
  ) => {
    const payload: Record<string, unknown> = {
      jev_providers_replace: true,
      jev_providers: next.map((item) => ({
        id: item.id,
        name: item.name,
        protocol: item.protocol,
        base_url: item.base_url,
        model: item.model,
        api_key: extras?.providerId === item.id ? extras.apiKey : undefined,
        clear_api_key: extras?.providerId === item.id && extras.clearKey ? true : undefined,
      })),
    };
    if (extras?.activeId !== undefined) payload.jev_active_provider = extras.activeId;
    if (extras?.timeout !== undefined) payload.jev_timeout_seconds = extras.timeout;
    if (extras?.model !== undefined) payload.jev_model = extras.model;
    return persist(payload);
  }, [persist]);

  const providerPayload = useMemo(
    () => buildJevPayload(draft, baseline, JEV_PROVIDER_KEYS),
    [baseline, draft],
  );
  const rolePayload = useMemo(
    () => buildJevPayload(draft, baseline, JEV_ROLE_KEYS),
    [baseline, draft],
  );

  const activeProvider = providers.find((item) => item.id === draft.jev_active_provider) || providers.find((item) => item.configured) || providers[0] || null;
  const configured = runtime ? jevConfiguredFromRuntime({ ...runtime, jev_active_provider: draft.jev_active_provider }) : false;

  return {
    runtime,
    draft,
    setDraft,
    providers,
    activeProvider,
    loading,
    saving,
    saved,
    error,
    configured,
    hasProviderChanges: Object.keys(providerPayload).length > 0,
    hasRoleChanges: Object.keys(rolePayload).length > 0,
    saveProvider: () => save(JEV_PROVIDER_KEYS),
    saveRole: () => save(JEV_ROLE_KEYS),
    saveProviders,
    reload: loadRuntime,
    resetDraft: () => { setDraft(baseline); setError(null); setSaved(false); },
  };
}
