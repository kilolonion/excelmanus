"use client";

import { useEffect, useState, useCallback, useRef, useMemo } from "react";
import { apiGet, apiPut, apiPost, testModelConnection, listRemoteModels, getManageToken, buildApiUrl } from "@/lib/api";
import type { RemoteModelItem } from "@/lib/api";
import { settingsCache } from "@/lib/settings-cache";
import type { TestConnectionResult } from "@/lib/api";
import { useUIStore } from "@/stores/ui-store";
import {
  DEFAULT_THINKING_EFFORT_OPTIONS,
  normalizeThinkingEffortOptions,
  type ThinkingEffort,
} from "@/lib/thinking";
import { CODEX_MODELS } from "./constants";
import {
  EMPTY_PROFILE_DRAFT,
  isCodexProfile,
  isSubscriptionProfile,
  subscriptionModelPrefix,
  isMaskedApiKey,
  normalizeFetchedCapabilities,
  siblingDraftFromProfile,
  profileToDraft,
  uniqueSiblingProfileName,
} from "./helpers";
import type { ModelCapabilities, ProfileEntry, ProbeJobSnapshot } from "./types";
import type { ProviderPreset } from "./types";
import type { ModelProfileInput } from "@/lib/model-config";
import { activateModelProfile, createModelProfile, updateModelProfile, deleteModelProfile } from "@/lib/model-config-api";
import { useModelConfig } from "./useModelConfig";

type ThinkingSettings = {
  effort: string;
  budget: number;
  effective_budget: number;
  allowed_efforts?: string[];
};

// Codex（OAuth）档案没有 API Key，无法走远端 /models 探测；
// 模型目录是固定的订阅支持列表，直接作为「已检测」结果使用。
const CODEX_REMOTE_MODEL_ITEMS: RemoteModelItem[] = CODEX_MODELS.map((m) => ({
  id: m.publicId,
  owned_by: m.proOnly ? `${m.displayName} · Pro` : m.displayName,
}));

export function useAdminModelSettings() {
  const { config, loading, loadError, fetchConfig } = useModelConfig();
  const mutationPending = useRef(false);
  const remoteRequest = useRef(0);
  const connectionRequest = useRef(0);
  const capabilitiesRequest = useRef(0);
  const version = useUIStore((state) => state.modelProfileVersion);
  useEffect(() => () => {
    remoteRequest.current += 1;
    connectionRequest.current += 1;
    capabilitiesRequest.current += 1;
  }, []);
  const [showKeys, setShowKeys] = useState<Record<string, boolean>>({});
  const [newProfile, setNewProfile] = useState(false);
  const [editingProfile, setEditingProfile] = useState<string | null>(null);
  const [siblingSourceName, setSiblingSourceName] = useState<string | null>(null);
  const [profileError, setProfileError] = useState<string | null>(null);
  const [highlightProfile, setHighlightProfile] = useState<string | null>(null);
  const [addingProfile, setAddingProfile] = useState(false);
  const [deletingProfile, setDeletingProfile] = useState<string | null>(null);
  const [fetchingModels, setFetchingModels] = useState(false);
  const [remoteModels, setRemoteModels] = useState<RemoteModelItem[]>([]);
  const [modelDropdownTarget, setModelDropdownTarget] = useState<string | null>(null);
  const [remoteModelError, setRemoteModelError] = useState<string | null>(null);
  const [remoteModelHint, setRemoteModelHint] = useState<string | null>(null);
  const modelDropdownRef = useRef<HTMLDivElement>(null);
  const profileCardRefs = useRef<Record<string, HTMLDivElement | null>>({});
  const [profileDraft, setProfileDraft] = useState<ProfileEntry>({ ...EMPTY_PROFILE_DRAFT });
  // 按 "model|base_url" 或 profile 名索引的每模型能力
  const [capsMap, setCapsMap] = useState<Record<string, ModelCapabilities>>({});
  const [probingKey, setProbingKey] = useState<string | null>(null);
  const [probingAll, setProbingAll] = useState(false);
  const probeEsRef = useRef<EventSource | null>(null);
  const [activatingProfile, setActivatingProfile] = useState<string | null>(null);
  const [togglingFastProfile, setTogglingFastProfile] = useState<string | null>(null);
  const [saveToast, setSaveToast] = useState<{ msg: string; type: "success" | "error" } | null>(null);

  // 折叠/展开状态
  const [expandedSections, setExpandedSections] = useState<Record<string, boolean>>({});
  const toggleSection = useCallback((key: string) => {
    setExpandedSections((prev) => ({ ...prev, [key]: !prev[key] }));
  }, []);

  // 连通测试状态
  const [testingKey, setTestingKey] = useState<string | null>(null);
  const [testResult, setTestResult] = useState<Record<string, TestConnectionResult | null>>({});

  // Thinking 配置
  const [thinkingEffort, setThinkingEffort] = useState<string>("medium");
  const [thinkingEffortOptions, setThinkingEffortOptions] = useState<ThinkingEffort[]>(
    [...DEFAULT_THINKING_EFFORT_OPTIONS],
  );
  const [thinkingBudget, setThinkingBudget] = useState<string>("");
  const [thinkingEffectiveBudget, setThinkingEffectiveBudget] = useState<number>(0);
  const [thinkingSaving, setThinkingSaving] = useState(false);
  const [thinkingSaved, setThinkingSaved] = useState(false);

  const sortedProfiles = useMemo(() => {
    if (!config?.profiles) return [];
    const active = config.active;
    if (!active) return config.profiles;
    return [...config.profiles].sort((a, b) => {
      if (a.name === active && b.name !== active) return -1;
      if (a.name !== active && b.name === active) return 1;
      return 0;
    });
  }, [config]);

  const formRef = useRef<HTMLDivElement>(null);
  const [pendingScrollToForm, setPendingScrollToForm] = useState(0);
  const scrollToForm = useCallback(() => setPendingScrollToForm((n) => n + 1), []);
  useEffect(() => {
    if (!pendingScrollToForm) return;
    // 状态变更后等待表单挂载再滚动
    requestAnimationFrame(() => {
      formRef.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
    });
  }, [pendingScrollToForm]);

  const resetProfileFormUi = useCallback(() => {
    remoteRequest.current += 1;
    connectionRequest.current += 1;
    setFetchingModels(false);
    setTestingKey(null);
    setNewProfile(false);
    setEditingProfile(null);
    setProfileDraft({ ...EMPTY_PROFILE_DRAFT });
    setShowKeys({});
    setSiblingSourceName(null);
    setProfileError(null);
    setTestResult((prev) => ({ ...prev, _profile_form: null }));
    setRemoteModelError(null);
    setRemoteModelHint(null);
    setRemoteModels([]);
    setModelDropdownTarget(null);
  }, []);

  const beginNewProfile = useCallback(() => {
    if (mutationPending.current) return;
    resetProfileFormUi();
    setNewProfile(true);
  }, [resetProfileFormUi]);

  const beginEditProfile = useCallback((profile: ProfileEntry) => {
    if (mutationPending.current) return;
    resetProfileFormUi();
    setProfileDraft(profileToDraft(profile));
    setEditingProfile(profile.name);
  }, [resetProfileFormUi]);

  const applyPresetToProfileDraft = useCallback((preset: ProviderPreset, customName?: string, customDescription?: string) => {
    if (mutationPending.current) return;
    resetProfileFormUi();
    setNewProfile(true);
    setEditingProfile(null);
    setProfileDraft({
      name: customName || preset.label,
      model: preset.model,
      api_key: "",
      base_url: preset.base_url,
      description: customDescription || preset.description,
      protocol: preset.protocol,
      thinking_mode: preset.thinking_mode,
      service_tier: "",
      model_family: preset.model_family,
      custom_extra_body: "",
      custom_extra_headers: "",
      canonical_model: "",
    });
    setSiblingSourceName(null);
    setRemoteModelError(null);
    setRemoteModelHint(null);
    setTestResult((prev) => ({ ...prev, _profile_form: null }));
    scrollToForm();
  }, [scrollToForm, resetProfileFormUi]);

  const fetchThinkingConfig = useCallback(async (force = false) => {
    if (!force) {
      const cached = settingsCache.get<ThinkingSettings>("/thinking");
      if (cached) {
        setThinkingEffort(cached.effort);
        setThinkingBudget(cached.budget > 0 ? String(cached.budget) : "");
        setThinkingEffectiveBudget(cached.effective_budget);
        setThinkingEffortOptions(normalizeThinkingEffortOptions(cached.allowed_efforts));
        return;
      }
    }
    try {
      const data = await apiGet<ThinkingSettings>("/thinking", { direct: true });
      settingsCache.set("/thinking", data);
      setThinkingEffort(data.effort);
      setThinkingBudget(data.budget > 0 ? String(data.budget) : "");
      setThinkingEffectiveBudget(data.effective_budget);
      setThinkingEffortOptions(normalizeThinkingEffortOptions(data.allowed_efforts));
    } catch {
      // 后端未就绪
    }
  }, []);

  const handleSaveThinking = useCallback(async (allowedEfforts: ThinkingEffort[], budgetStr: string) => {
    setThinkingSaving(true);
    try {
      const body: Record<string, unknown> = { allowed_efforts: allowedEfforts };
      const budgetNum = parseInt(budgetStr, 10);
      if (!isNaN(budgetNum) && budgetNum >= 0) {
        body.budget = budgetNum;
      } else {
        body.budget = 0;
      }
      const data = await apiPut<ThinkingSettings>("/thinking", body, { direct: true });
      const nextOptions = normalizeThinkingEffortOptions(data.allowed_efforts);
      settingsCache.set("/thinking", data);
      setThinkingEffort(data.effort);
      setThinkingBudget(data.budget > 0 ? String(data.budget) : "");
      setThinkingEffectiveBudget(data.effective_budget);
      setThinkingEffortOptions(nextOptions);
      useUIStore.getState().setThinkingEffort(data.effort);
      useUIStore.getState().setThinkingEffortOptions(nextOptions);
      setThinkingSaved(true);
      setTimeout(() => setThinkingSaved(false), 2000);
      setSaveToast({ msg: "可调思考等级已更新", type: "success" });
    } catch (e) {
      setSaveToast({ msg: e instanceof Error ? e.message : "思考等级保存失败", type: "error" });
    } finally {
      setThinkingSaving(false);
    }
  }, []);

  const fetchAllCapabilities = useCallback(async (force = false) => {
    const id = ++capabilitiesRequest.current;
    const snapshot = useUIStore.getState().modelProfileVersion;
    if (!force) {
      const cached = settingsCache.get<Record<string, ModelCapabilities>>("_capsMap");
      if (cached) {
        const normalized: Record<string, ModelCapabilities> = {};
        for (const [name, caps] of Object.entries(cached)) {
          normalized[name] = normalizeFetchedCapabilities(caps);
        }
        setCapsMap(normalized);
        return;
      }
    }
    try {
      const data = await apiGet<{ items: { name: string; model: string; base_url: string; capabilities: ModelCapabilities | null }[] }>("/config/models/capabilities/all", { direct: true });
      if (capabilitiesRequest.current !== id || snapshot !== useUIStore.getState().modelProfileVersion) return;
      const map: Record<string, ModelCapabilities> = {};
      for (const item of data.items) {
        if (item.capabilities) {
          map[item.name] = normalizeFetchedCapabilities(item.capabilities);
        }
      }
      settingsCache.set("_capsMap", map);
      setCapsMap(map);
    } catch {
      // 后端未就绪
    }
  }, []);

  const subscribeToProbeJob = useCallback((jobId: string) => {
    probeEsRef.current?.close();
    const token = getManageToken();
    const esUrl =
      buildApiUrl(`/config/models/capabilities/jobs/${encodeURIComponent(jobId)}/events`, { direct: true }) +
      (token ? `?manage_token=${encodeURIComponent(token)}` : "");
    const es = new EventSource(esUrl, { withCredentials: true });
    probeEsRef.current = es;

    const onMessage = (e: MessageEvent) => {
      try {
        const snapshot: ProbeJobSnapshot = JSON.parse(e.data);
        for (const t of snapshot.targets) {
          if (t.capabilities) {
            setCapsMap((prev) => {
              const next = { ...prev, [t.name]: normalizeFetchedCapabilities(t.capabilities!) };
              settingsCache.set("_capsMap", next);
              return next;
            });
          }
        }
        if (["succeeded", "partial", "failed", "cancelled"].includes(snapshot.state)) {
          es.close();
          setProbingKey(null);
          setProbingAll(false);
        }
      } catch { /* ignore parse errors */ }
    };

    es.addEventListener("job_update", onMessage);
    es.onerror = () => {
      es.close();
      setProbingKey(null);
      setProbingAll(false);
    };
  }, []);

  // Cleanup EventSource on unmount
  useEffect(() => () => { probeEsRef.current?.close(); }, []);

  const handleProbeOne = useCallback(async (profileName: string) => {
    setProbingKey(profileName);
    try {
      const data = await apiPost<{ job_id: string }>("/config/models/capabilities/jobs", { name: profileName }, { direct: true });
      subscribeToProbeJob(data.job_id);
    } catch {
      setProbingKey(null);
    }
  }, [subscribeToProbeJob]);

  const handleProbeAll = useCallback(async () => {
    setProbingAll(true);
    try {
      const data = await apiPost<{ job_id: string }>("/config/models/capabilities/jobs", { all: true }, { direct: true });
      subscribeToProbeJob(data.job_id);
    } catch {
      setProbingAll(false);
    }
  }, [subscribeToProbeJob]);

  const handleTestConnection = useCallback(async (key: string, opts: { name?: string; model?: string; base_url?: string; api_key?: string }) => {
    const id = ++connectionRequest.current;
    setTestingKey(key);
    setTestResult((prev) => ({ ...prev, [key]: null }));
    try {
      const apiKey = opts.api_key && !isMaskedApiKey(opts.api_key) ? opts.api_key : undefined;
      const result = await testModelConnection({ ...opts, api_key: apiKey });
      if (connectionRequest.current === id) setTestResult((prev) => ({ ...prev, [key]: result }));
    } catch (e) {
      if (connectionRequest.current === id) setTestResult((prev) => ({ ...prev, [key]: { ok: false, error: e instanceof Error ? e.message : "测试失败", model: opts.model || "" } }));
    } finally {
      if (connectionRequest.current === id) setTestingKey(null);
    }
  }, []);

  const handleFetchRemoteModels = useCallback(async (
    target: string,
    baseUrl?: string,
    apiKey?: string,
    protocol?: string,
    name?: string,
  ) => {
    const id = ++remoteRequest.current;
    setFetchingModels(true);
    setRemoteModelError(null);
    setRemoteModelHint(null);
    setRemoteModels([]);
    setModelDropdownTarget(null);
    const namedProfile = name
      ? (config?.profiles || []).find((p) => p.name === name)
      : undefined;
    if (namedProfile && isCodexProfile(namedProfile)) {
      setRemoteModels(CODEX_REMOTE_MODEL_ITEMS);
      setFetchingModels(false);
      return;
    }
    try {
      const usableKey = apiKey && !isMaskedApiKey(apiKey) ? apiKey : undefined;
      const result = await listRemoteModels({
        name: name || undefined,
        base_url: baseUrl || undefined,
        api_key: usableKey,
        protocol: protocol || undefined,
      });
      if (remoteRequest.current !== id) return;
      if (result.error) {
        setRemoteModelError(result.error);
        setRemoteModelHint(result.hint || null);
      } else if (result.models.length === 0) {
        setRemoteModelError("未检测到可用模型");
        setRemoteModelHint("请确认地址和协议是否正确，或直接填写 Model ID。");
      } else {
        setRemoteModels(result.models);
      }
    } catch (e) {
      if (remoteRequest.current !== id) return;
      setRemoteModelError(e instanceof Error ? e.message : "检测失败");
      setRemoteModelHint(null);
    } finally {
      if (remoteRequest.current === id) setFetchingModels(false);
    }
  }, [config?.profiles]);

  const beginAddSiblingProfile = useCallback((source: ProfileEntry) => {
    if (mutationPending.current) return;
    resetProfileFormUi();
    const codex = isCodexProfile(source);
    const subscription = subscriptionModelPrefix(source) !== null;
    setNewProfile(true);
    setEditingProfile(null);
    setSiblingSourceName(source.name);
    // 订阅档案的 description 带有源模型名（如 "GPT-5.6 Sol — OAuth 登录"），
    // 添加新模型时不能沿用，交由选择模型时按 displayName 重新生成。
    setProfileDraft(subscription ? { ...siblingDraftFromProfile(source), description: "" } : siblingDraftFromProfile(source));
    setProfileError(null);
    setRemoteModelError(null);
    setRemoteModelHint(null);
    setTestResult((prev) => ({ ...prev, _profile_form: null }));
    setRemoteModels(codex ? CODEX_REMOTE_MODEL_ITEMS : []);
    setModelDropdownTarget(null);
    scrollToForm();
    if (!codex) {
      void handleFetchRemoteModels(
        "_profile",
        source.base_url || undefined,
        undefined,
        source.protocol || undefined,
        source.name,
      );
    }
  }, [handleFetchRemoteModels, scrollToForm, resetProfileFormUi]);

  const handleCapToggle = useCallback(async (profileName: string, model: string, base_url: string, field: string, value: boolean) => {
    try {
      const data = await apiPut<{ capabilities: ModelCapabilities | null }>("/config/models/capabilities", {
        model,
        base_url,
        overrides: { [field]: value },
      }, { direct: true });
      if (data.capabilities) {
        setCapsMap((prev) => {
          const next = { ...prev, [profileName]: data.capabilities! };
          settingsCache.set("_capsMap", next);
          return next;
        });
      }
    } catch {
      // 忽略
    }
  }, []);

  useEffect(() => {
    // 强制刷新能力探测结果，避免 Tab 切换/重进设置页时沿用旧的失败提示
    fetchAllCapabilities(true);
    fetchThinkingConfig();
  }, [fetchAllCapabilities, fetchThinkingConfig, version]);

  // 自动消失 saveToast
  useEffect(() => {
    if (!saveToast) return;
    const t = setTimeout(() => setSaveToast(null), 3000);
    return () => clearTimeout(t);
  }, [saveToast]);

  const handleActivateProfile = useCallback(async (profile: ProfileEntry) => {
    if (mutationPending.current) return;
    mutationPending.current = true;
    setActivatingProfile(profile.name);
    try {
      await activateModelProfile(profile.name);
      setSaveToast({ msg: `已激活「${profile.name}」`, type: "success" });
    } catch (e) {
      setSaveToast({ msg: e instanceof Error ? e.message : "激活失败", type: "error" });
    } finally {
      mutationPending.current = false;
      setActivatingProfile(null);
    }
  }, []);

  const handleToggleFastMode = useCallback(async (profile: ProfileEntry) => {
    if (mutationPending.current || isSubscriptionProfile(profile)) return;
    mutationPending.current = true;
    setTogglingFastProfile(profile.name);
    setProfileError(null);
    const serviceTier = profile.service_tier === "fast" ? "" : "fast";
    try {
      await updateModelProfile(profile.name, {
        ...profileToDraft(profile),
        service_tier: serviceTier,
      });
      if (editingProfile === profile.name) {
        setProfileDraft((draft) => ({ ...draft, service_tier: serviceTier }));
      }
      await fetchConfig(true);
      setSaveToast({ msg: `「${profile.name}」快速模式已${serviceTier ? "开启" : "关闭"}`, type: "success" });
    } catch (error) {
      setProfileError(error instanceof Error ? error.message : "快速模式切换失败，请重试");
    } finally {
      mutationPending.current = false;
      setTogglingFastProfile(null);
    }
  }, [editingProfile, fetchConfig]);

  const finishProfileSave = (name: string) => {
    resetProfileFormUi();
    setHighlightProfile(name);
    setTimeout(() => setHighlightProfile(null), 2000);
    requestAnimationFrame(() => {
      profileCardRefs.current[name]?.scrollIntoView({ behavior: "smooth", block: "nearest" });
    });
  };

  const saveProfile = async (originalName?: string) => {
    if (mutationPending.current) return;
    mutationPending.current = true;
    setAddingProfile(true);
    setProfileError(null);
    const existingNames = (config?.profiles || []).map((profile) => profile.name);
    const source = (config?.profiles || []).find((p) => p.name === siblingSourceName);
    const prefix = source ? subscriptionModelPrefix(source) : null;
    const modelId = profileDraft.model.trim();
    const model = prefix && modelId && !modelId.startsWith(prefix) ? `${prefix}${modelId}` : modelId;
    const name = profileDraft.name.trim() || (prefix && !existingNames.includes(model)
      ? model : uniqueSiblingProfileName(model, existingNames));
    const payload: ModelProfileInput = { ...profileDraft, name, model };
    if (!originalName && siblingSourceName && !profileDraft.api_key.trim() && !prefix) {
      payload.clone_from = siblingSourceName;
    }
    const previous = config?.profiles.find((p) => p.name === originalName);
    if ((!originalName && !profileDraft.canonical_model.trim()) ||
        (originalName && profileDraft.canonical_model === (previous?.canonical_model || ""))) {
      delete payload.canonical_model;
    }
    try {
      if (originalName) await updateModelProfile(originalName, payload);
      else await createModelProfile(payload);
      finishProfileSave(name);
      setSaveToast({ msg: `模型档案「${name}」已${originalName ? "更新" : "添加"}`, type: "success" });
    } catch (error) {
      // Keep the exact draft, credentials and editor open so the user can retry.
      setProfileError(error instanceof Error ? error.message : "保存失败，请重试");
    } finally {
      mutationPending.current = false;
      setAddingProfile(false);
    }
  };

  const handleAddProfile = () => saveProfile();
  const handleUpdateProfile = (name: string) => saveProfile(name);
  const handleDeleteProfile = async (name: string) => {
    if (mutationPending.current) return;
    mutationPending.current = true;
    setDeletingProfile(name);
    setProfileError(null);
    try {
      await deleteModelProfile(name);
      if (editingProfile === name) finishProfileSave("");
      setSaveToast({ msg: `模型档案「${name}」已删除`, type: "success" });
      setCapsMap((prev) => {
        const next = { ...prev };
        delete next[name];
        return next;
      });
    } catch (error) {
      setProfileError(error instanceof Error ? error.message : "删除失败");
    } finally {
      mutationPending.current = false;
      setDeletingProfile(null);
    }
  };

  return {
    config,
    loading,
    loadError,
    showKeys,
    setShowKeys,
    newProfile,
    setNewProfile,
    editingProfile,
    setEditingProfile,
    siblingSourceName,
    setSiblingSourceName,
    beginNewProfile,
    beginEditProfile,
    beginAddSiblingProfile,
    profileError,
    setProfileError,
    highlightProfile,
    addingProfile,
    deletingProfile,
    profileBusy: addingProfile || !!deletingProfile || !!activatingProfile || !!togglingFastProfile,
    fetchingModels,
    remoteModels,
    modelDropdownTarget,
    setModelDropdownTarget,
    remoteModelError,
    remoteModelHint,
    setRemoteModelError,
    setRemoteModelHint,
    modelDropdownRef,
    profileDraft,
    setProfileDraft,
    capsMap,
    probingKey,
    probingAll,
    activatingProfile,
    togglingFastProfile,
    saveToast,
    setSaveToast,
    expandedSections,
    setExpandedSections,
    toggleSection,
    testingKey,
    testResult,
    setTestResult,
    thinkingEffort,
    setThinkingEffort,
    thinkingEffortOptions,
    setThinkingEffortOptions,
    thinkingBudget,
    setThinkingBudget,
    thinkingEffectiveBudget,
    thinkingSaving,
    thinkingSaved,
    sortedProfiles,
    formRef,
    profileCardRefs,
    applyPresetToProfileDraft,
    handleSaveThinking,
    handleProbeOne,
    handleProbeAll,
    handleTestConnection,
    handleFetchRemoteModels,
    handleCapToggle,
    fetchConfig,
    handleActivateProfile,
    handleToggleFastMode,
    handleAddProfile,
    handleUpdateProfile,
    handleDeleteProfile,
    resetProfileFormUi,
    setRemoteModels,
    scrollToForm,
  };
}

export type AdminModelCtx = ReturnType<typeof useAdminModelSettings>;
