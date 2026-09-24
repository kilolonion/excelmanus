"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  connectSubscriptionProvider, disconnectSubscriptionProvider, refreshSubscriptionToken,
  fetchSubscriptionModels, type SubscriptionModelEntry,
} from "@/lib/auth-api";
import { createModelProfile, deleteModelProfile } from "@/lib/model-config-api";
import type { ProfileEntry, ProviderPreset } from "./types";
import { useSubscriptionAccount } from "./useSubscriptionAccount";
import { useUIStore } from "@/stores/ui-store";

type ConnectionPreset = Pick<ProviderPreset, "base_url" | "protocol" | "thinking_mode" | "model_family" | "label">;

export function useSubscriptionProvider(provider: string, profiles: ProfileEntry[], preset: ConnectionPreset, staticCatalog?: SubscriptionModelEntry[]) {
  const account = useSubscriptionAccount(provider);
  const version = useUIStore((s) => s.modelProfileVersion);
  const [error, setError] = useState("");
  const [tokenInput, setTokenInput] = useState("");
  const [showManual, setShowManual] = useState(false);
  const [action, setAction] = useState<string | null>(null);
  const [catalog, setCatalog] = useState<SubscriptionModelEntry[]>(staticCatalog || []);
  const [catalogLoading, setCatalogLoading] = useState(false);
  const [catalogError, setCatalogError] = useState("");
  const catalogRequest = useRef(0);
  const generation = useRef(0);
  const owner = useRef<symbol | null>(null);
  const lock = useMemo(() => ({
    acquire(key: symbol) {
      if (owner.current) return false;
      owner.current = key;
      return true;
    },
    release(key: symbol) { if (owner.current === key) owner.current = null; },
  }), []);

  const loadCatalog = useCallback(async () => {
    if (staticCatalog) return;
    const id = ++catalogRequest.current;
    setCatalogLoading(true);
    setCatalogError("");
    try {
      const data = await fetchSubscriptionModels(provider);
      if (catalogRequest.current === id) setCatalog(data.models || []);
    } catch (error) {
      if (catalogRequest.current === id) setCatalogError(error instanceof Error ? error.message : "无法读取模型目录");
    } finally {
      if (catalogRequest.current === id) setCatalogLoading(false);
    }
  }, [provider, staticCatalog]);

  useEffect(() => {
    if (account.status?.status === "connected") void loadCatalog();
    else if (account.status?.status === "disconnected") {
      setCatalog(staticCatalog || []);
      setCatalogError("");
      setCatalogLoading(false);
    }
    return () => { catalogRequest.current += 1; };
  }, [account.status?.status, loadCatalog, staticCatalog, version]);
  useEffect(() => () => { generation.current += 1; owner.current = null; }, [provider]);

  const perform = async <T,>(name: string, operation: () => Promise<T>, onSuccess?: (result: T) => void | Promise<void>) => {
    const key = Symbol(name);
    if (!lock.acquire(key)) return false;
    const id = generation.current;
    setAction(name);
    setError("");
    try {
      const result = await operation();
      if (generation.current !== id) return false;
      await onSuccess?.(result);
      return true;
    } catch (error) {
      if (generation.current === id) setError(error instanceof Error ? error.message : "操作失败，请重试");
      return false;
    } finally {
      lock.release(key);
      if (generation.current === id) setAction(null);
    }
  };

  const handleManualConnect = () => perform("connect", async () => {
    let parsed: unknown;
    try { parsed = JSON.parse(tokenInput.trim()); }
    catch { throw new Error("JSON 格式无效"); }
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) throw new Error("凭证必须是 JSON 对象");
    return connectSubscriptionProvider(provider, parsed as Record<string, unknown>);
  }, async () => {
    setTokenInput("");
    setShowManual(false);
    await account.completeLogin();
  });

  const handleDisconnect = () => perform("disconnect", () => disconnectSubscriptionProvider(provider), () => {
    catalogRequest.current += 1;
    setCatalog(staticCatalog || []);
    setCatalogLoading(false);
    setCatalogError("");
    account.setStatus({ provider, status: "disconnected" });
  });
  const handleRefresh = () => perform("refresh", () => refreshSubscriptionToken(provider), async (result) => {
    account.setStatus({ ...account.status, provider, status: "connected", expires_at: result.expires_at });
    await account.reloadStatus();
  });

  const providerProfiles = profiles.filter((p) => p.model.startsWith(`${provider}/`) ||
    (provider === "workbuddy-cn" && p.model.startsWith("workbuddy/")));
  const profileForModel = (entry: SubscriptionModelEntry) => {
    const model = entry.public_model_id || `${provider}/${entry.model}`;
    return providerProfiles.find((p) => p.model === model ||
      (provider === "workbuddy-cn" && p.model === `workbuddy/${entry.model}`));
  };
  const handleAddModel = (entry: SubscriptionModelEntry) => {
    const name = entry.profile_name || `${provider}/${entry.model}`;
    return perform(`add:${name}`, () => createModelProfile({
      name, model: entry.public_model_id || `${provider}/${entry.model}`,
      base_url: preset.base_url, protocol: preset.protocol,
      thinking_mode: preset.thinking_mode, model_family: preset.model_family,
      description: `${entry.display_name || entry.model} — ${preset.label} 订阅登录（无需 API Key）`,
    }));
  };
  const handleRemoveModel = (name: string) => perform(`remove:${name}`, () => deleteModelProfile(name));

  return {
    ...account, error, setError, tokenInput, setTokenInput, showManual, setShowManual,
    catalog, catalogLoading, catalogError, loadCatalog, providerProfiles, profileForModel,
    lock, busy: action !== null, connecting: action === "connect", disconnecting: action === "disconnect", refreshing: action === "refresh",
    addingModel: action?.startsWith("add:") ? action.slice(4) : null,
    removingModel: action?.startsWith("remove:") ? action.slice(7) : null,
    handleManualConnect, handleDisconnect, handleRefresh, handleAddModel, handleRemoveModel,
  };
}
