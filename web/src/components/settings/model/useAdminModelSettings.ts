"use client";

import { useEffect, useState, useCallback, useRef, useMemo } from "react";
import { apiGet, apiPut, apiPost, apiDelete, testModelConnection, listRemoteModels } from "@/lib/api";
import type { RemoteModelItem } from "@/lib/api";
import { settingsCache } from "@/lib/settings-cache";
import type { TestConnectionResult } from "@/lib/api";
import { useUIStore } from "@/stores/ui-store";
import { SECTION_META } from "./constants";
import { isMaskedApiKey, normalizeFetchedCapabilities } from "./helpers";
import type { ModelConfig, ModelSection, ModelCapabilities, ProfileEntry, ProbeJobSnapshot } from "./types";
import type { ProviderPreset } from "./types";

export function useAdminModelSettings() {
  const [config, setConfig] = useState<ModelConfig | null>(null);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState<string | null>(null);
  const [saved, setSaved] = useState<string | null>(null);
  const [editDrafts, setEditDrafts] = useState<Record<string, Record<string, string>>>({});
  const [showKeys, setShowKeys] = useState<Record<string, boolean>>({});
  const [enabledDrafts, setEnabledDrafts] = useState<Record<string, boolean>>({});
  const [newProfile, setNewProfile] = useState(false);
  const [editingProfile, setEditingProfile] = useState<string | null>(null);
  const [profileError, setProfileError] = useState<string | null>(null);
  const [highlightProfile, setHighlightProfile] = useState<string | null>(null);
  const [addingProfile, setAddingProfile] = useState(false);
  const [fetchingModels, setFetchingModels] = useState(false);
  const [remoteModels, setRemoteModels] = useState<RemoteModelItem[]>([]);
  const [modelDropdownTarget, setModelDropdownTarget] = useState<string | null>(null);
  const [remoteModelError, setRemoteModelError] = useState<string | null>(null);
  const modelDropdownRef = useRef<HTMLDivElement>(null);
  const profileCardRefs = useRef<Record<string, HTMLDivElement | null>>({});
  const [profileDraft, setProfileDraft] = useState<ProfileEntry>({
    name: "",
    model: "",
    api_key: "",
    base_url: "",
    description: "",
    protocol: "auto",
    thinking_mode: "auto",
    model_family: "",
    custom_extra_body: "",
    custom_extra_headers: "",
  });
  // 按 "model|base_url" 或 profile 名索引的每模型能力
  const [capsMap, setCapsMap] = useState<Record<string, ModelCapabilities>>({});
  const [probingKey, setProbingKey] = useState<string | null>(null);
  const [probingAll, setProbingAll] = useState(false);
  const [probeJob, setProbeJob] = useState<ProbeJobSnapshot | null>(null);
  const probeEsRef = useRef<EventSource | null>(null);
  // 快速应用 profile 到角色
  const [applyingProfile, setApplyingProfile] = useState<string | null>(null);
  const [applyMenuOpen, setApplyMenuOpen] = useState<string | null>(null);
  const applyMenuRef = useRef<HTMLDivElement>(null);
  const [applyMenuDropUp, setApplyMenuDropUp] = useState(false);
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
  const [thinkingBudget, setThinkingBudget] = useState<string>("");
  const [thinkingEffectiveBudget, setThinkingEffectiveBudget] = useState<number>(0);
  const [thinkingSaving, setThinkingSaving] = useState(false);
  const [thinkingSaved, setThinkingSaved] = useState(false);

  // 排序 profiles: 主模型排在最前面
  const sortedProfiles = useMemo(() => {
    if (!config?.profiles) return [];
    const mainModel = config.main?.model;
    if (!mainModel) return config.profiles;
    return [...config.profiles].sort((a, b) => {
      const aIsMain = a.model === mainModel;
      const bIsMain = b.model === mainModel;
      if (aIsMain && !bIsMain) return -1;
      if (!aIsMain && bIsMain) return 1;
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

  const applyPresetToProfileDraft = useCallback((preset: ProviderPreset, customName?: string, customDescription?: string) => {
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
      model_family: preset.model_family,
      custom_extra_body: "",
      custom_extra_headers: "",
    });
    scrollToForm();
  }, [scrollToForm]);

  const fetchThinkingConfig = useCallback(async (force = false) => {
    if (!force) {
      const cached = settingsCache.get<{ effort: string; budget: number; effective_budget: number }>("/thinking");
      if (cached) {
        setThinkingEffort(cached.effort);
        setThinkingBudget(cached.budget > 0 ? String(cached.budget) : "");
        setThinkingEffectiveBudget(cached.effective_budget);
        return;
      }
    }
    try {
      const data = await apiGet<{ effort: string; budget: number; effective_budget: number }>("/thinking", { direct: true });
      settingsCache.set("/thinking", data);
      setThinkingEffort(data.effort);
      setThinkingBudget(data.budget > 0 ? String(data.budget) : "");
      setThinkingEffectiveBudget(data.effective_budget);
    } catch {
      // 后端未就绪
    }
  }, []);

  const handleSaveThinking = useCallback(async (effort: string, budgetStr: string) => {
    setThinkingSaving(true);
    try {
      const body: Record<string, unknown> = { effort };
      const budgetNum = parseInt(budgetStr, 10);
      if (!isNaN(budgetNum) && budgetNum >= 0) {
        body.budget = budgetNum;
      } else {
        body.budget = 0;
      }
      const data = await apiPut<{ effort: string; budget: number; effective_budget: number }>("/thinking", body, { direct: true });
      settingsCache.set("/thinking", data);
      setThinkingEffort(data.effort);
      setThinkingBudget(data.budget > 0 ? String(data.budget) : "");
      setThinkingEffectiveBudget(data.effective_budget);
      setThinkingSaved(true);
      setTimeout(() => setThinkingSaved(false), 2000);
      setSaveToast({ msg: "推理深度已更新", type: "success" });
    } catch (e) {
      setSaveToast({ msg: e instanceof Error ? e.message : "推理深度保存失败", type: "error" });
    } finally {
      setThinkingSaving(false);
    }
  }, []);

  const fetchAllCapabilities = useCallback(async (force = false) => {
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
    const origin = typeof window !== "undefined" ? window.location.origin : "";
    const esUrl = origin + "/api/v1/config/models/capabilities/jobs/" + jobId + "/events";
    const es = new EventSource(esUrl);
    probeEsRef.current = es;

    const onMessage = (e: MessageEvent) => {
      try {
        const snapshot: ProbeJobSnapshot = JSON.parse(e.data);
        setProbeJob(snapshot);
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
          setProbeJob(null);
          setProbingKey(null);
          setProbingAll(false);
        }
      } catch { /* ignore parse errors */ }
    };

    es.addEventListener("job_update", onMessage);
    es.onerror = () => {
      es.close();
      setProbeJob(null);
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
    setTestingKey(key);
    setTestResult((prev) => ({ ...prev, [key]: null }));
    try {
      const result = await testModelConnection(opts);
      setTestResult((prev) => ({ ...prev, [key]: result }));
    } catch (e) {
      setTestResult((prev) => ({ ...prev, [key]: { ok: false, error: e instanceof Error ? e.message : "测试失败", model: opts.model || "" } }));
    } finally {
      setTestingKey(null);
    }
  }, []);

  const handleFetchRemoteModels = useCallback(async (target: string, baseUrl?: string, apiKey?: string, protocol?: string) => {
    setFetchingModels(true);
    setRemoteModelError(null);
    setRemoteModels([]);
    setModelDropdownTarget(null);
    try {
      const result = await listRemoteModels({
        base_url: baseUrl || undefined,
        api_key: apiKey || undefined,
        protocol: protocol || undefined,
      });
      if (result.error) {
        setRemoteModelError(result.error);
      } else if (result.models.length === 0) {
        setRemoteModelError("未检测到可用模型");
      } else {
        setRemoteModels(result.models);
        setModelDropdownTarget(target);
      }
    } catch (e) {
      setRemoteModelError(e instanceof Error ? e.message : "检测失败");
    } finally {
      setFetchingModels(false);
    }
  }, []);

  // 点击外部关闭模型下拉
  useEffect(() => {
    if (!modelDropdownTarget) return;
    const handler = (e: MouseEvent) => {
      if (modelDropdownRef.current && !modelDropdownRef.current.contains(e.target as Node)) {
        setModelDropdownTarget(null);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [modelDropdownTarget]);

  // 点击外部关闭"应用到角色"下拉（不使用 fixed overlay，避免阻塞滚动）
  useEffect(() => {
    if (!applyMenuOpen) return;
    const handler = (e: MouseEvent) => {
      if (applyMenuRef.current && !applyMenuRef.current.contains(e.target as Node)) {
        setApplyMenuOpen(null);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [applyMenuOpen]);

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

  const applyConfigData = useCallback((data: ModelConfig) => {
    setConfig(data);
    const drafts: Record<string, Record<string, string>> = {};
    for (const section of SECTION_META) {
      const sectionData = data[section.key as keyof ModelConfig] as ModelSection;
      drafts[section.key] = {};
      for (const field of section.fields) {
        drafts[section.key][field] = (sectionData as Record<string, string>)?.[field] || "";
      }
      drafts[section.key]["protocol"] = sectionData?.protocol || "auto";
    }
    setEditDrafts(drafts);
    setEnabledDrafts({
      aux: data.aux?.enabled !== false,
      embedding: data.embedding?.enabled === true,
    });
  }, []);

  const fetchConfig = useCallback(async (force = false) => {
    if (!force) {
      const cached = settingsCache.get<ModelConfig>("/config/models");
      if (cached) { applyConfigData(cached); return; }
    }
    // 仅首次加载时展示 loading 旋转，force 刷新（如保存后）静默更新避免闪屏
    if (!force) setLoading(true);
    try {
      const data = await apiGet<ModelConfig>("/config/models", { direct: true });
      settingsCache.set("/config/models", data);
      applyConfigData(data);
    } catch {
      // 后端未就绪
    } finally {
      setLoading(false);
    }
  }, [applyConfigData]);

  useEffect(() => {
    fetchConfig();
    // 强制刷新能力探测结果，避免 Tab 切换/重进设置页时沿用旧的失败提示
    fetchAllCapabilities(true);
    fetchThinkingConfig();
  }, [fetchConfig, fetchAllCapabilities, fetchThinkingConfig]);

  // 自动消失 saveToast
  useEffect(() => {
    if (!saveToast) return;
    const t = setTimeout(() => setSaveToast(null), 3000);
    return () => clearTimeout(t);
  }, [saveToast]);

  const handleApplyProfileToRole = useCallback(async (profile: ProfileEntry, role: "main" | "aux") => {
    const roleLabel = role === "main" ? "主模型" : "辅助模型";
    setApplyingProfile(profile.name);
    setApplyMenuOpen(null);

    try {
      const body: Record<string, unknown> = {
        model: profile.model,
        base_url: profile.base_url || undefined,
        api_key: profile.api_key || undefined,
        protocol: profile.protocol || "auto",
      };
      if (role === "aux") {
        body.enabled = true;
      }
      await apiPut(`/config/models/${role}`, body, { direct: true });
      setSaveToast({ msg: `已将 "${profile.name}" 应用为${roleLabel}`, type: "success" });
      fetchConfig(true);
      fetchAllCapabilities(true);
      // 应用为主模型时，模型选择器中的 "default" 条目会变化
      if (role === "main") {
        useUIStore.getState().bumpModelProfiles();
      }
    } catch (e) {
      setSaveToast({ msg: e instanceof Error ? e.message : `应用为${roleLabel}失败`, type: "error" });
    } finally {
      setApplyingProfile(null);
    }
  }, [fetchConfig, fetchAllCapabilities]);

  const handleSaveSection = async (sectionKey: string) => {
    setSaving(sectionKey);
    const sectionLabel = SECTION_META.find((s) => s.key === sectionKey)?.label || sectionKey;
    try {
      const draft = editDrafts[sectionKey];
      const body: Record<string, unknown> = {};
      for (const [field, value] of Object.entries(draft)) {
        if (field === "api_key" && isMaskedApiKey(value)) continue;
        if (field === "protocol" && sectionKey === "embedding") continue;
        body[field] = value;
      }
      // aux/embedding 保存时一并提交 enabled 开关
      if ((sectionKey === "aux" || sectionKey === "embedding") && enabledDrafts[sectionKey] !== undefined) {
        body.enabled = enabledDrafts[sectionKey];
      }
      await apiPut(`/config/models/${sectionKey}`, body, { direct: true });
      setSaved(sectionKey);
      setTimeout(() => setSaved(null), 2000);
      setSaveToast({ msg: `${sectionLabel} 配置已保存`, type: "success" });
      fetchConfig(true);
      // 主模型配置变更会影响模型选择器中的 "default" 条目
      if (sectionKey === "main") {
        useUIStore.getState().bumpModelProfiles();
      }
    } catch (e) {
      setSaveToast({ msg: e instanceof Error ? e.message : `${sectionLabel} 保存失败`, type: "error" });
    } finally {
      setSaving(null);
    }
  };

  const handleToggleEnabled = async (sectionKey: string, checked: boolean) => {
    setEnabledDrafts((prev) => ({ ...prev, [sectionKey]: checked }));
    const sectionLabel = SECTION_META.find((s) => s.key === sectionKey)?.label || sectionKey;
    // 立即保存开关状态
    try {
      await apiPut(`/config/models/${sectionKey}`, { enabled: checked }, { direct: true });
      setSaveToast({ msg: `${sectionLabel} 已${checked ? "启用" : "禁用"}`, type: "success" });
      fetchConfig(true);
    } catch (e) {
      // 回滚
      setEnabledDrafts((prev) => ({ ...prev, [sectionKey]: !checked }));
      setSaveToast({ msg: e instanceof Error ? e.message : `${sectionLabel} 切换失败`, type: "error" });
    }
  };

  const handleAddProfile = async () => {
    setProfileError(null);
    setAddingProfile(true);
    const newName = profileDraft.name;
    const draftSnapshot = { ...profileDraft };

    // 乐观插入：先在前端列表添加，避免等待网络请求。
    const optimisticEntry: ProfileEntry = {
      name: draftSnapshot.name,
      model: draftSnapshot.model,
      api_key: draftSnapshot.api_key,
      base_url: draftSnapshot.base_url,
      description: draftSnapshot.description,
      protocol: draftSnapshot.protocol || "auto",
      thinking_mode: draftSnapshot.thinking_mode || "auto",
      model_family: draftSnapshot.model_family || "",
      custom_extra_body: draftSnapshot.custom_extra_body || "",
      custom_extra_headers: draftSnapshot.custom_extra_headers || "",
    };
    const prevProfiles = config?.profiles || [];
    setConfig((prev) => {
      if (!prev) return prev;
      const next = { ...prev, profiles: [...prev.profiles, optimisticEntry] };
      settingsCache.set("/config/models", next);
      return next;
    });
    setNewProfile(false);
    setEditingProfile(null);
    setProfileDraft({ name: "", model: "", api_key: "", base_url: "", description: "", protocol: "auto", thinking_mode: "auto", model_family: "", custom_extra_body: "", custom_extra_headers: "" });
    setTestResult((prev) => ({ ...prev, _profile_form: null }));
    useUIStore.getState().bumpModelProfiles();
    setSaveToast({ msg: `模型档案「${newName}」已添加`, type: "success" });
    setHighlightProfile(newName);
    setTimeout(() => setHighlightProfile(null), 2000);
    requestAnimationFrame(() => {
      profileCardRefs.current[newName]?.scrollIntoView({ behavior: "smooth", block: "nearest" });
    });

    try {
      await apiPost("/config/models/profiles", draftSnapshot, { direct: true });
      // 成功后从服务端同步最新数据
      fetchConfig(true);
    } catch (e) {
      // 添加失败回滚：恢复到之前的 profiles 列表。
      setConfig((prev) => {
        if (!prev) return prev;
        const next = { ...prev, profiles: prevProfiles };
        settingsCache.set("/config/models", next);
        return next;
      });
      useUIStore.getState().bumpModelProfiles();
      setProfileError(e instanceof Error ? e.message : "添加失败，请检查网络或参数");
      setSaveToast({ msg: e instanceof Error ? e.message : "添加失败", type: "error" });
    } finally {
      setAddingProfile(false);
    }
  };

  const handleUpdateProfile = async (originalName: string) => {
    setProfileError(null);
    const updatedName = profileDraft.name;
    const draftSnapshot = { ...profileDraft };

    // 乐观更新：先在前端列表替换，避免等待网络请求。
    const prevProfiles = config?.profiles || [];
    const optimisticEntry: ProfileEntry = {
      name: draftSnapshot.name,
      model: draftSnapshot.model,
      api_key: draftSnapshot.api_key,
      base_url: draftSnapshot.base_url,
      description: draftSnapshot.description,
      protocol: draftSnapshot.protocol || "auto",
      thinking_mode: draftSnapshot.thinking_mode || "auto",
      model_family: draftSnapshot.model_family || "",
      custom_extra_body: draftSnapshot.custom_extra_body || "",
      custom_extra_headers: draftSnapshot.custom_extra_headers || "",
    };
    setConfig((prev) => {
      if (!prev) return prev;
      const next = { ...prev, profiles: prev.profiles.map((p) => p.name === originalName ? optimisticEntry : p) };
      settingsCache.set("/config/models", next);
      return next;
    });
    setEditingProfile(null);
    setNewProfile(false);
    setProfileDraft({ name: "", model: "", api_key: "", base_url: "", description: "", protocol: "auto", thinking_mode: "auto", model_family: "", custom_extra_body: "", custom_extra_headers: "" });
    setTestResult((prev) => ({ ...prev, _profile_form: null }));
    useUIStore.getState().bumpModelProfiles();
    setSaveToast({ msg: `模型档案「${updatedName}」已更新`, type: "success" });
    setHighlightProfile(updatedName);
    setTimeout(() => setHighlightProfile(null), 2000);
    requestAnimationFrame(() => {
      profileCardRefs.current[updatedName]?.scrollIntoView({ behavior: "smooth", block: "nearest" });
    });

    try {
      await apiPut(`/config/models/profiles/${originalName}`, draftSnapshot, { direct: true });
      // 成功后从服务端同步最新数据
      fetchConfig(true);
    } catch (e) {
      // 更新失败回滚：恢复到之前的 profiles 列表。
      setConfig((prev) => {
        if (!prev) return prev;
        const next = { ...prev, profiles: prevProfiles };
        settingsCache.set("/config/models", next);
        return next;
      });
      useUIStore.getState().bumpModelProfiles();
      setProfileError(e instanceof Error ? e.message : "更新失败，请检查网络或参数");
      setSaveToast({ msg: e instanceof Error ? e.message : "更新失败", type: "error" });
    }
  };

  const handleDeleteProfile = async (name: string) => {
    setProfileError(null);
    const currentProfiles = config?.profiles || [];
    const removedIndex = currentProfiles.findIndex((p) => p.name === name);
    const removedProfile = removedIndex >= 0 ? currentProfiles[removedIndex] : null;

    // 乐观删除：先从前端列表移除，避免等待网络请求。
    if (removedProfile) {
      setConfig((prev) => {
        if (!prev) return prev;
        const next = { ...prev, profiles: prev.profiles.filter((p) => p.name !== name) };
        settingsCache.set("/config/models", next);
        return next;
      });
      setApplyMenuOpen((prev) => (prev === name ? null : prev));
      useUIStore.getState().bumpModelProfiles();
    }

    try {
      await apiDelete(`/config/models/profiles/${name}`, { direct: true });
      setSaveToast({ msg: `模型档案「${name}」已删除`, type: "success" });
      // 如果正在编辑被删除的 profile，关闭表单
      if (editingProfile === name) {
        setEditingProfile(null);
        setNewProfile(false);
        setProfileDraft({ name: "", model: "", api_key: "", base_url: "", description: "", protocol: "auto", thinking_mode: "auto", model_family: "", custom_extra_body: "", custom_extra_headers: "" });
      }
      // 同步清理缓存中的能力结果，避免保留无效项。
      setCapsMap((prev) => {
        if (!prev[name]) return prev;
        const next = { ...prev };
        delete next[name];
        settingsCache.set("_capsMap", next);
        return next;
      });
    } catch (e) {
      // 删除失败回滚：恢复被删条目到原位置。
      if (removedProfile) {
        setConfig((prev) => {
          if (!prev) return prev;
          if (prev.profiles.some((p) => p.name === name)) return prev;
          const nextProfiles = [...prev.profiles];
          const insertAt = Math.min(Math.max(removedIndex, 0), nextProfiles.length);
          nextProfiles.splice(insertAt, 0, removedProfile);
          const next = { ...prev, profiles: nextProfiles };
          settingsCache.set("/config/models", next);
          return next;
        });
        useUIStore.getState().bumpModelProfiles();
      }
      setProfileError(e instanceof Error ? e.message : "删除失败");
    }
  };

  const updateDraft = (section: string, field: string, value: string) => {
    setEditDrafts((prev) => ({
      ...prev,
      [section]: { ...prev[section], [field]: value },
    }));
  };


  return {
    config,
    loading,
    saving,
    saved,
    editDrafts,
    showKeys,
    setShowKeys,
    enabledDrafts,
    newProfile,
    setNewProfile,
    editingProfile,
    setEditingProfile,
    profileError,
    setProfileError,
    highlightProfile,
    addingProfile,
    fetchingModels,
    remoteModels,
    modelDropdownTarget,
    setModelDropdownTarget,
    remoteModelError,
    modelDropdownRef,
    profileDraft,
    setProfileDraft,
    capsMap,
    probingKey,
    probingAll,
    applyingProfile,
    applyMenuOpen,
    setApplyMenuOpen,
    applyMenuRef,
    applyMenuDropUp,
    setApplyMenuDropUp,
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
    handleApplyProfileToRole,
    handleSaveSection,
    handleToggleEnabled,
    handleAddProfile,
    handleUpdateProfile,
    handleDeleteProfile,
    updateDraft,
    scrollToForm,
  };
}

export type AdminModelCtx = ReturnType<typeof useAdminModelSettings>;
