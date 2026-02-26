"use client";

import { useEffect, useState, useCallback, useRef } from "react";
import {
  Plus,
  Trash2,
  Pencil,
  Save,
  X,
  Eye,
  EyeOff,
  Loader2,
  CheckCircle2,
  Server,
  Bot,
  ScanEye,
  Zap,
  Wrench,
  ImageIcon,
  Brain,
  Check,
  AlertTriangle,
  Download,
  Upload,
  Copy,
  Lock,
  Unlock,
  ClipboardPaste,
  Dices,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Separator } from "@/components/ui/separator";
import { Switch } from "@/components/ui/switch";
import { apiGet, apiPut, apiPost, apiDelete } from "@/lib/api";
import { MiniCheckbox } from "@/components/ui/MiniCheckbox";
import { useAuthStore } from "@/stores/auth-store";
import { useAuthConfigStore } from "@/stores/auth-config-store";

interface ModelSection {
  api_key?: string;
  base_url?: string;
  model?: string;
}

interface ProfileEntry {
  name: string;
  model: string;
  api_key: string;
  base_url: string;
  description: string;
}

interface ModelCapabilities {
  model: string;
  base_url: string;
  healthy: boolean | null;
  health_error: string;
  supports_tool_calling: boolean | null;
  supports_vision: boolean | null;
  supports_thinking: boolean | null;
  thinking_type: string;
  detected_at: string;
  probe_errors: Record<string, string>;
  manual_override: boolean;
}

interface ModelConfig {
  main: ModelSection;
  aux: ModelSection;
  vlm: ModelSection;
  profiles: ProfileEntry[];
}

const SECTION_META: {
  key: string;
  label: string;
  icon: React.ReactNode;
  fields: ("api_key" | "base_url" | "model")[];
  desc: string;
}[] = [
  {
    key: "main",
    label: "主模型",
    icon: <Server className="h-4 w-4" />,
    fields: ["model", "base_url", "api_key"],
    desc: "核心对话模型",
  },
  {
    key: "aux",
    label: "辅助模型 (Aux)",
    icon: <Bot className="h-4 w-4" />,
    fields: ["model", "base_url", "api_key"],
    desc: "路由 + 子代理默认模型 + 窗口感知顾问",
  },
  {
    key: "vlm",
    label: "VLM 视觉模型",
    icon: <ScanEye className="h-4 w-4" />,
    fields: ["model", "base_url", "api_key"],
    desc: "图片表格提取",
  },
];

const FIELD_LABELS: Record<string, string> = {
  api_key: "API Key",
  base_url: "Base URL",
  model: "Model ID",
};

function isMaskedApiKey(value: string): boolean {
  if (!value) return false;
  if (value === "****") return true;
  if (value.length <= 12) return false;
  const middle = value.slice(4, -4);
  return middle.length > 0 && /^\*+$/.test(middle);
}

function isModelUnhealthy(caps: ModelCapabilities | null | undefined): boolean {
  if (!caps) return false;
  if (caps.healthy === false) return true;
  if (caps.probe_errors?.health) return true;
  return false;
}

function getHealthError(caps: ModelCapabilities | null | undefined): string {
  if (!caps) return "";
  return caps.health_error || caps.probe_errors?.health || "";
}

export function ModelTab() {
  const [config, setConfig] = useState<ModelConfig | null>(null);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState<string | null>(null);
  const [saved, setSaved] = useState<string | null>(null);
  const [editDrafts, setEditDrafts] = useState<Record<string, Record<string, string>>>({});
  const [showKeys, setShowKeys] = useState<Record<string, boolean>>({});
  const [newProfile, setNewProfile] = useState(false);
  const [editingProfile, setEditingProfile] = useState<string | null>(null);
  const [profileDraft, setProfileDraft] = useState<ProfileEntry>({
    name: "",
    model: "",
    api_key: "",
    base_url: "",
    description: "",
  });
  // 按 "model|base_url" 或 profile 名索引的每模型能力
  const [capsMap, setCapsMap] = useState<Record<string, ModelCapabilities>>({});
  const [probingKey, setProbingKey] = useState<string | null>(null);
  const [probingAll, setProbingAll] = useState(false);

  // Thinking 配置
  const [thinkingEffort, setThinkingEffort] = useState<string>("medium");
  const [thinkingBudget, setThinkingBudget] = useState<string>("");
  const [thinkingEffectiveBudget, setThinkingEffectiveBudget] = useState<number>(0);
  const [thinkingSaving, setThinkingSaving] = useState(false);
  const [thinkingSaved, setThinkingSaved] = useState(false);

  const user = useAuthStore((s) => s.user);
  const authEnabled = useAuthConfigStore((s) => s.authEnabled);
  const isAdmin = !authEnabled || !user || user.role === "admin";

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

  const fetchThinkingConfig = useCallback(async () => {
    try {
      const data = await apiGet<{ effort: string; budget: number; effective_budget: number }>("/thinking");
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
      const data = await apiPut<{ effort: string; budget: number; effective_budget: number }>("/thinking", body);
      setThinkingEffort(data.effort);
      setThinkingBudget(data.budget > 0 ? String(data.budget) : "");
      setThinkingEffectiveBudget(data.effective_budget);
      setThinkingSaved(true);
      setTimeout(() => setThinkingSaved(false), 2000);
    } catch {
      // 忽略
    } finally {
      setThinkingSaving(false);
    }
  }, []);

  const fetchAllCapabilities = useCallback(async () => {
    try {
      const data = await apiGet<{ items: { name: string; model: string; base_url: string; capabilities: ModelCapabilities | null }[] }>("/config/models/capabilities/all");
      const map: Record<string, ModelCapabilities> = {};
      for (const item of data.items) {
        if (item.capabilities) {
          map[item.name] = item.capabilities;
        }
      }
      setCapsMap(map);
    } catch {
      // 后端未就绪
    }
  }, []);

  const handleProbeOne = useCallback(async (profileName: string) => {
    setProbingKey(profileName);
    try {
      const body: Record<string, string> = { name: profileName };
      const data = await apiPost<{ capabilities: ModelCapabilities }>("/config/models/capabilities/probe", body);
      setCapsMap((prev) => ({ ...prev, [profileName]: data.capabilities }));
    } catch {
      // 忽略
    } finally {
      setProbingKey(null);
    }
  }, []);

  const handleProbeAll = useCallback(async () => {
    setProbingAll(true);
    try {
      const data = await apiPost<{ results: { name: string; capabilities?: ModelCapabilities }[] }>("/config/models/capabilities/probe-all", {});
      setCapsMap((prev) => {
        const next = { ...prev };
        for (const r of data.results) {
          if (r.capabilities) next[r.name] = r.capabilities;
        }
        return next;
      });
    } catch {
      // 忽略
    } finally {
      setProbingAll(false);
    }
  }, []);

  const handleCapToggle = useCallback(async (profileName: string, model: string, base_url: string, field: string, value: boolean) => {
    try {
      const data = await apiPut<{ capabilities: ModelCapabilities | null }>("/config/models/capabilities", {
        model,
        base_url,
        overrides: { [field]: value },
      });
      if (data.capabilities) {
        setCapsMap((prev) => ({ ...prev, [profileName]: data.capabilities! }));
      }
    } catch {
      // 忽略
    }
  }, []);

  const fetchConfig = useCallback(async () => {
    setLoading(true);
    try {
      const data = await apiGet<ModelConfig>("/config/models");
      setConfig(data);
      const drafts: Record<string, Record<string, string>> = {};
      for (const section of SECTION_META) {
        const sectionData = data[section.key as keyof ModelConfig] as ModelSection;
        drafts[section.key] = {};
        for (const field of section.fields) {
          drafts[section.key][field] = (sectionData as Record<string, string>)?.[field] || "";
        }
      }
      setEditDrafts(drafts);
    } catch {
      // 后端未就绪
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (isAdmin) {
      fetchConfig();
      fetchAllCapabilities();
      fetchThinkingConfig();
    }
  }, [fetchConfig, fetchAllCapabilities, fetchThinkingConfig, isAdmin]);

  const handleSaveSection = async (sectionKey: string) => {
    setSaving(sectionKey);
    try {
      const draft = editDrafts[sectionKey];
      const body: Record<string, string> = {};
      for (const [field, value] of Object.entries(draft)) {
        if (field === "api_key" && isMaskedApiKey(value)) continue;
        body[field] = value;
      }
      await apiPut(`/config/models/${sectionKey}`, body);
      setSaved(sectionKey);
      setTimeout(() => setSaved(null), 2000);
      fetchConfig();
    } catch {
      // 忽略
    } finally {
      setSaving(null);
    }
  };

  const handleAddProfile = async () => {
    try {
      await apiPost("/config/models/profiles", profileDraft);
      setNewProfile(false);
      setProfileDraft({ name: "", model: "", api_key: "", base_url: "", description: "" });
      fetchConfig();
    } catch {
      // 忽略
    }
  };

  const handleUpdateProfile = async (originalName: string) => {
    try {
      await apiPut(`/config/models/profiles/${originalName}`, profileDraft);
      setEditingProfile(null);
      setProfileDraft({ name: "", model: "", api_key: "", base_url: "", description: "" });
      fetchConfig();
    } catch {
      // 忽略
    }
  };

  const handleDeleteProfile = async (name: string) => {
    try {
      await apiDelete(`/config/models/profiles/${name}`);
      fetchConfig();
    } catch {
      // 忽略
    }
  };

  const updateDraft = (section: string, field: string, value: string) => {
    setEditDrafts((prev) => ({
      ...prev,
      [section]: { ...prev[section], [field]: value },
    }));
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center py-12">
        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
      </div>
    );
  }

  if (!isAdmin) {
    return (
      <div className="space-y-4">
        <div className="rounded-lg border border-border p-4">
          <div className="flex items-center gap-2 mb-2">
            <Lock className="h-4 w-4" style={{ color: "var(--em-primary)" }} />
            <h3 className="font-semibold text-sm">模型配置</h3>
          </div>
          <p className="text-xs text-muted-foreground">
            模型配置由管理员管理。您可以通过顶部模型选择器切换可用模型。
          </p>
          {user?.allowedModels && user.allowedModels.length > 0 && (
            <div className="mt-3">
              <p className="text-xs text-muted-foreground mb-1.5">您可使用的模型：</p>
              <div className="flex flex-wrap gap-1.5">
                <Badge variant="secondary" className="text-[10px]">default</Badge>
                {user.allowedModels.map((m) => (
                  <Badge key={m} variant="secondary" className="text-[10px]">{m}</Badge>
                ))}
              </div>
            </div>
          )}
          {(!user?.allowedModels || user.allowedModels.length === 0) && (
            <p className="text-xs text-muted-foreground mt-2">
              您可以使用所有已配置的模型。
            </p>
          )}
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-4">
        {/* Section cards */}
        {SECTION_META.map((section) => {
          const sectionCaps = capsMap[section.key];
          return (
          <div key={section.key} className={`rounded-lg border p-4 transition-colors ${
            isModelUnhealthy(sectionCaps)
              ? "border-destructive/40 bg-destructive/5"
              : "border-border"
          }`}>
            <div className="flex items-center gap-2 flex-wrap">
              <span style={{ color: isModelUnhealthy(sectionCaps) ? "var(--destructive, #ef4444)" : "var(--em-primary)" }}>{section.icon}</span>
              <h3 className="font-semibold text-sm">{section.label}</h3>
              <span className="text-xs text-muted-foreground ml-auto hidden sm:inline">{section.desc}</span>
            </div>
            <p className="text-xs text-muted-foreground mt-0.5 ml-6 sm:hidden">{section.desc}</p>
            {isModelUnhealthy(sectionCaps) ? (
              <div className="mt-1.5 mb-1 ml-6 flex items-center gap-1.5 text-destructive">
                <AlertTriangle className="h-3 w-3 flex-shrink-0" />
                <span className="text-[11px] truncate" title={getHealthError(sectionCaps)}>
                  连接失败: {getHealthError(sectionCaps) || "模型不可达"}
                </span>
              </div>
            ) : (
              <div className="mt-1 mb-1 ml-6">
                <CapabilityBadges caps={sectionCaps ?? null} />
              </div>
            )}
            <div className="space-y-2 mt-3">
              {section.fields.map((field) => (
                <div key={field} className="flex flex-col sm:flex-row sm:items-center gap-1 sm:gap-2">
                  <label className="text-xs text-muted-foreground sm:w-16 flex-shrink-0">
                    {FIELD_LABELS[field]}
                  </label>
                  <div className="flex-1 relative">
                    <Input
                      value={editDrafts[section.key]?.[field] || ""}
                      onChange={(e) => updateDraft(section.key, field, e.target.value)}
                      type={field === "api_key" && !showKeys[`${section.key}_${field}`] ? "password" : "text"}
                      className={`h-8 text-xs font-mono ${field === "api_key" ? "pr-8" : ""}`}
                      placeholder={`输入 ${FIELD_LABELS[field]}...`}
                    />
                    {field === "api_key" && (
                      <button
                        className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground !min-h-0 !min-w-0 h-5 w-5 flex items-center justify-center"
                        onClick={() =>
                          setShowKeys((prev) => ({
                            ...prev,
                            [`${section.key}_${field}`]: !prev[`${section.key}_${field}`],
                          }))
                        }
                      >
                        {showKeys[`${section.key}_${field}`] ? (
                          <EyeOff className="h-3 w-3" />
                        ) : (
                          <Eye className="h-3 w-3" />
                        )}
                      </button>
                    )}
                  </div>
                </div>
              ))}
            </div>
            <div className="flex flex-col sm:flex-row justify-end gap-2 mt-3">
              {section.key === "main" && (
                <Button
                  size="sm"
                  variant="outline"
                  className="h-8 sm:h-7 text-xs gap-1"
                  onClick={() => handleProbeOne(section.key)}
                  disabled={probingKey === section.key}
                >
                  {probingKey === section.key ? (
                    <Loader2 className="h-3 w-3 animate-spin" />
                  ) : (
                    <Zap className="h-3 w-3" />
                  )}
                  {probingKey === section.key ? "探测中" : "探测能力"}
                </Button>
              )}
              <Button
                size="sm"
                className="h-8 sm:h-7 text-xs gap-1 text-white"
                style={{ backgroundColor: "var(--em-primary)" }}
                onClick={() => handleSaveSection(section.key)}
                disabled={saving === section.key}
              >
                {saving === section.key ? (
                  <Loader2 className="h-3 w-3 animate-spin" />
                ) : saved === section.key ? (
                  <CheckCircle2 className="h-3 w-3" />
                ) : (
                  <Save className="h-3 w-3" />
                )}
                {saved === section.key ? "已保存" : "保存"}
              </Button>
            </div>
          </div>
          );
        })}

        {/* Profiles section */}
        <Separator />
        <div>
          <div className="flex items-center justify-between gap-2 mb-3">
            <div className="min-w-0">
              <h3 className="font-semibold text-sm">多模型配置</h3>
              <p className="text-xs text-muted-foreground">
                通过 /model 命令切换的模型档案
              </p>
            </div>
            <Button
              size="sm"
              variant="outline"
              className="h-7 text-xs gap-1 flex-shrink-0"
              onClick={() => {
                setNewProfile(true);
                setEditingProfile(null);
                setProfileDraft({ name: "", model: "", api_key: "", base_url: "", description: "" });
                scrollToForm();
              }}
            >
              <Plus className="h-3 w-3" />
              新增模型
            </Button>
          </div>

          {/* New/Edit profile form */}
          {(newProfile || editingProfile) && (
            <div ref={formRef} className="rounded-lg border border-dashed border-border p-3 mb-3 space-y-2">
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                <div>
                  <label className="text-xs text-muted-foreground">名称 *</label>
                  <Input
                    value={profileDraft.name}
                    onChange={(e) => setProfileDraft((d) => ({ ...d, name: e.target.value }))}
                    className="h-8 sm:h-7 text-xs"
                    placeholder="如: gpt4"
                  />
                </div>
                <div>
                  <label className="text-xs text-muted-foreground">Model ID *</label>
                  <Input
                    value={profileDraft.model}
                    onChange={(e) => setProfileDraft((d) => ({ ...d, model: e.target.value }))}
                    className="h-8 sm:h-7 text-xs font-mono"
                    placeholder="如: gpt-4o"
                  />
                </div>
              </div>
              <div>
                <label className="text-xs text-muted-foreground">Base URL（空则继承主配置）</label>
                <Input
                  value={profileDraft.base_url}
                  onChange={(e) => setProfileDraft((d) => ({ ...d, base_url: e.target.value }))}
                  className="h-8 sm:h-7 text-xs font-mono"
                  placeholder="https://..."
                />
              </div>
              <div>
                <label className="text-xs text-muted-foreground">API Key（空则继承主配置）</label>
                <div className="relative">
                  <Input
                    value={profileDraft.api_key}
                    onChange={(e) => setProfileDraft((d) => ({ ...d, api_key: e.target.value }))}
                    className="h-8 sm:h-7 text-xs font-mono pr-8"
                    type={showKeys["profile_api_key"] ? "text" : "password"}
                    placeholder="sk-..."
                  />
                  <button
                    type="button"
                    className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground !min-h-0 !min-w-0 h-5 w-5 flex items-center justify-center"
                    onClick={() => setShowKeys((prev) => ({ ...prev, profile_api_key: !prev.profile_api_key }))}
                  >
                    {showKeys["profile_api_key"] ? (
                      <EyeOff className="h-3 w-3" />
                    ) : (
                      <Eye className="h-3 w-3" />
                    )}
                  </button>
                </div>
              </div>
              <div>
                <label className="text-xs text-muted-foreground">描述</label>
                <Input
                  value={profileDraft.description}
                  onChange={(e) => setProfileDraft((d) => ({ ...d, description: e.target.value }))}
                  className="h-8 sm:h-7 text-xs"
                  placeholder="简短说明"
                />
              </div>
              <div className="flex justify-end gap-2 pt-1">
                <Button
                  size="sm"
                  variant="ghost"
                  className="h-7 text-xs gap-1"
                  onClick={() => {
                    setNewProfile(false);
                    setEditingProfile(null);
                  }}
                >
                  <X className="h-3 w-3" /> 取消
                </Button>
                <Button
                  size="sm"
                  className="h-7 text-xs gap-1 text-white"
                  style={{ backgroundColor: "var(--em-primary)" }}
                  disabled={!profileDraft.name || !profileDraft.model}
                  onClick={() =>
                    editingProfile
                      ? handleUpdateProfile(editingProfile)
                      : handleAddProfile()
                  }
                >
                  <Save className="h-3 w-3" />
                  {editingProfile ? "更新" : "添加"}
                </Button>
              </div>
            </div>
          )}

          {/* Profile list */}
          <div className="space-y-1.5">
            {config?.profiles.map((p) => {
              const pCaps = capsMap[p.name];
              return (
              <div
                key={p.name}
                className={`rounded-lg border px-3 py-3 sm:py-2.5 text-sm transition-colors overflow-hidden cursor-pointer hover:bg-muted/40 active:bg-muted/60 ${
                  isModelUnhealthy(pCaps)
                    ? "border-destructive/40 bg-destructive/5 opacity-70"
                    : "border-border"
                }`}
                onClick={() => {
                  setEditingProfile(p.name);
                  setNewProfile(false);
                  setProfileDraft({
                    name: p.name,
                    model: p.model,
                    api_key: "",
                    base_url: p.base_url,
                    description: p.description,
                  });
                  scrollToForm();
                }}
              >
                {/* Row 1: name + model badge + action buttons */}
                <div className="flex items-center gap-2">
                  <span className="font-medium truncate min-w-0">{p.name}</span>
                  <Badge variant="secondary" className="text-[10px] font-mono min-w-0 max-w-[40%] truncate">
                    {p.model}
                  </Badge>
                  <div className="flex gap-0.5 shrink-0 ml-auto">
                    <Button
                      variant="ghost"
                      size="icon"
                      className="h-6 w-6"
                      title="探测能力"
                      onClick={(e) => { e.stopPropagation(); handleProbeOne(p.name); }}
                      disabled={probingKey === p.name}
                    >
                      {probingKey === p.name ? (
                        <Loader2 className="h-3 w-3 animate-spin" />
                      ) : (
                        <Zap className="h-3 w-3" />
                      )}
                    </Button>
                    <Button
                      variant="ghost"
                      size="icon"
                      className="h-6 w-6"
                      onClick={(e) => {
                        e.stopPropagation();
                        setEditingProfile(p.name);
                        setNewProfile(false);
                        setProfileDraft({
                          name: p.name,
                          model: p.model,
                          api_key: "",
                          base_url: p.base_url,
                          description: p.description,
                        });
                        scrollToForm();
                      }}
                    >
                      <Pencil className="h-3 w-3" />
                    </Button>
                    <Button
                      variant="ghost"
                      size="icon"
                      className="h-6 w-6 text-destructive"
                      onClick={(e) => { e.stopPropagation(); handleDeleteProfile(p.name); }}
                    >
                      <Trash2 className="h-3 w-3" />
                    </Button>
                  </div>
                </div>
                {/* Row 2: capabilities / error + description */}
                <div className="flex items-center gap-1.5 mt-1 flex-nowrap">
                  {isModelUnhealthy(pCaps) ? (
                    <span className="inline-flex items-center gap-1 text-destructive text-[10px]">
                      <AlertTriangle className="h-2.5 w-2.5" />
                      不可用
                    </span>
                  ) : (
                    <CapabilityBadges caps={pCaps ?? null} />
                  )}
                  {!isModelUnhealthy(pCaps) && p.description && (
                    <span className="text-[11px] text-muted-foreground truncate">· {p.description}</span>
                  )}
                </div>
                {/* Error detail */}
                {isModelUnhealthy(pCaps) && (
                  <p className="text-[10px] text-destructive truncate mt-0.5" title={getHealthError(pCaps)}>
                    {getHealthError(pCaps) || "连接失败"}
                  </p>
                )}
              </div>
              );
            })}
            {config?.profiles.length === 0 && !newProfile && (
              <p className="text-xs text-muted-foreground text-center py-4">
                暂无多模型配置，点击"新增模型"添加
              </p>
            )}
          </div>
        </div>

        {/* Model Capabilities Detail */}
        <Separator />
        <div>
          <div className="flex items-center justify-between gap-2 mb-3">
            <div className="min-w-0">
              <h3 className="font-semibold text-sm flex items-center gap-1.5">
                <Zap className="h-4 w-4 flex-shrink-0" style={{ color: "var(--em-primary)" }} />
                模型能力详情
              </h3>
              <p className="text-xs text-muted-foreground truncate">
                当前主模型: {config?.main?.model || "未配置"}
              </p>
            </div>
            <Button
              size="sm"
              variant="outline"
              className="h-7 text-xs gap-1 flex-shrink-0"
              onClick={handleProbeAll}
              disabled={probingAll}
            >
              {probingAll ? (
                <Loader2 className="h-3 w-3 animate-spin" />
              ) : (
                <Zap className="h-3 w-3" />
              )}
              {probingAll ? "探测中..." : "一键探测全部"}
            </Button>
          </div>

          {capsMap.main ? (
            <div className="space-y-2">
              <CapabilityRow
                icon={<Wrench className="h-3.5 w-3.5" />}
                label="工具调用 (Tool Calling)"
                desc="模型是否支持 function calling"
                value={capsMap.main.supports_tool_calling}
                error={capsMap.main.probe_errors?.tool_calling}
                onToggle={(v) => handleCapToggle("main", config?.main?.model || "", config?.main?.base_url || "", "supports_tool_calling", v)}
              />
              <CapabilityRow
                icon={<ImageIcon className="h-3.5 w-3.5" />}
                label="图像识别 (Vision)"
                desc="模型是否支持图片输入"
                value={capsMap.main.supports_vision}
                error={capsMap.main.probe_errors?.vision}
                onToggle={(v) => handleCapToggle("main", config?.main?.model || "", config?.main?.base_url || "", "supports_vision", v)}
              />
              <CapabilityRow
                icon={<Brain className="h-3.5 w-3.5" />}
                label="思考输出 (Thinking)"
                desc={capsMap.main.thinking_type ? `类型: ${capsMap.main.thinking_type}` : "模型是否支持输出推理过程"}
                value={capsMap.main.supports_thinking}
                error={capsMap.main.probe_errors?.thinking}
                onToggle={(v) => handleCapToggle("main", config?.main?.model || "", config?.main?.base_url || "", "supports_thinking", v)}
              />
              {capsMap.main.detected_at && (
                <p className="text-[10px] text-muted-foreground mt-2">
                  上次探测: {new Date(capsMap.main.detected_at).toLocaleString()}
                  {capsMap.main.manual_override && (
                    <Badge variant="secondary" className="ml-1.5 text-[9px]">手动覆盖</Badge>
                  )}
                </p>
              )}
            </div>
          ) : (
            <div className="text-center py-6">
              <p className="text-xs text-muted-foreground mb-2">
                尚未探测模型能力
              </p>
              <Button
                size="sm"
                className="h-7 text-xs gap-1 text-white"
                style={{ backgroundColor: "var(--em-primary)" }}
                onClick={handleProbeAll}
                disabled={probingAll}
              >
                {probingAll ? <Loader2 className="h-3 w-3 animate-spin" /> : <Zap className="h-3 w-3" />}
                一键探测全部
              </Button>
            </div>
          )}
        </div>

        {/* Thinking 配置 */}
        <Separator />
        <div>
          <div className="flex items-center justify-between gap-2 mb-3">
            <div className="min-w-0">
              <h3 className="font-semibold text-sm flex items-center gap-1.5">
                <Brain className="h-4 w-4 flex-shrink-0" style={{ color: "var(--em-primary)" }} />
                推理深度 (Thinking)
              </h3>
              <p className="text-xs text-muted-foreground">
                控制模型思考链的深度，影响推理质量和 token 消耗
              </p>
            </div>
          </div>

          <div className="space-y-3">
            {/* Effort 等级选择器 */}
            <div>
              <label className="text-xs text-muted-foreground mb-1.5 block">思考等级</label>
              <div className="grid grid-cols-3 sm:grid-cols-6 gap-1.5 sm:gap-1">
                {(["none", "minimal", "low", "medium", "high", "xhigh"] as const).map((level) => {
                  const labels: Record<string, string> = {
                    none: "关闭", minimal: "极简", low: "低",
                    medium: "中", high: "高", xhigh: "极高",
                  };
                  const isActive = thinkingEffort === level;
                  return (
                    <button
                      key={level}
                      className={`px-2.5 py-2 sm:py-1 rounded-md text-xs font-medium transition-colors border ${
                        isActive
                          ? "text-white border-transparent"
                          : "border-border text-muted-foreground hover:bg-muted/60"
                      }`}
                      style={isActive ? { backgroundColor: "var(--em-primary)" } : undefined}
                      onClick={() => {
                        setThinkingEffort(level);
                        handleSaveThinking(level, thinkingBudget);
                      }}
                      disabled={thinkingSaving}
                    >
                      {labels[level]}
                    </button>
                  );
                })}
              </div>
            </div>

            {/* Budget 精确预算 */}
            <div>
              <label className="text-xs text-muted-foreground mb-1.5 block">
                Token 预算（可选，留空则按等级自动换算）
              </label>
              <div className="flex flex-col sm:flex-row sm:items-center gap-2">
                <Input
                  value={thinkingBudget}
                  onChange={(e) => setThinkingBudget(e.target.value.replace(/\D/g, ""))}
                  className="h-8 text-xs font-mono w-full sm:w-32"
                  placeholder="自动"
                  inputMode="numeric"
                />
                <Button
                  size="sm"
                  className="h-8 sm:h-7 text-xs gap-1 text-white flex-shrink-0"
                  style={{ backgroundColor: "var(--em-primary)" }}
                  onClick={() => handleSaveThinking(thinkingEffort, thinkingBudget)}
                  disabled={thinkingSaving}
                >
                  {thinkingSaving ? (
                    <Loader2 className="h-3 w-3 animate-spin" />
                  ) : thinkingSaved ? (
                    <CheckCircle2 className="h-3 w-3" />
                  ) : (
                    <Save className="h-3 w-3" />
                  )}
                  {thinkingSaved ? "已保存" : "保存"}
                </Button>
              </div>
              {thinkingEffectiveBudget > 0 && (
                <p className="text-[10px] text-muted-foreground mt-1">
                  当前生效预算: {thinkingEffectiveBudget.toLocaleString()} tokens
                </p>
              )}
            </div>
          </div>
        </div>

        {/* Config Export / Import */}
        <Separator />
        <ConfigTransferPanel config={config} />
    </div>
  );
}

function ConfigTransferPanel({ config }: { config: ModelConfig | null }) {
  const user = useAuthStore((s) => s.user);
  const authEnabled = useAuthConfigStore((s) => s.authEnabled);
  const isAdminScope = !authEnabled || !user || user.role === "admin";

  const [mode, setMode] = useState<"idle" | "export" | "import">("idle");
  const [exportMode, setExportMode] = useState<"password" | "simple">("password");
  const [exportSections, setExportSections] = useState<Record<string, boolean>>({
    main: true,
    aux: true,
    vlm: true,
    profiles: true,
    user: true,
  });
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [importToken, setImportToken] = useState("");
  const [importPassword, setImportPassword] = useState("");
  const [resultToken, setResultToken] = useState("");
  const [importing, setImporting] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [copied, setCopied] = useState(false);
  const [importResult, setImportResult] = useState<{ status: string; imported: Record<string, unknown> } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [needsPassword, setNeedsPassword] = useState<boolean | null>(null);

  const resetState = () => {
    setMode("idle");
    setPassword("");
    setConfirmPassword("");
    setImportToken("");
    setImportPassword("");
    setResultToken("");
    setImportResult(null);
    setError(null);
    setNeedsPassword(null);
    setCopied(false);
  };

  const handleExport = async () => {
    setError(null);
    if (exportMode === "password") {
      if (!password) { setError("请输入加密密码"); return; }
      if (password !== confirmPassword) { setError("两次密码不一致"); return; }
    }
    setExporting(true);
    try {
      const sections = isAdminScope
        ? Object.entries(exportSections)
            .filter(([k, v]) => ["main", "aux", "vlm", "profiles"].includes(k) && v)
            .map(([k]) => k)
        : ["user"];
      const data = await apiPost<{ token: string }>("/config/export", {
        sections,
        mode: exportMode,
        password: exportMode === "password" ? password : null,
      });
      setResultToken(data.token);
    } catch (e) {
      setError(e instanceof Error ? e.message : "导出失败");
    } finally {
      setExporting(false);
    }
  };

  const handleDetectToken = async (token: string) => {
    setImportToken(token);
    setNeedsPassword(null);
    setError(null);
    if (!token.trim()) return;
    try {
      const data = await apiPost<{ needs_password: boolean }>("/config/transfer/detect", { token });
      setNeedsPassword(data.needs_password);
    } catch {
      setNeedsPassword(null);
    }
  };

  const handleImport = async () => {
    setError(null);
    if (!importToken.trim()) { setError("请粘贴配置令牌"); return; }
    if (needsPassword && !importPassword) { setError("此令牌需要密码"); return; }
    setImporting(true);
    try {
      const data = await apiPost<{ status: string; imported: Record<string, unknown>; exported_at: string }>("/config/import", {
        token: importToken,
        password: needsPassword ? importPassword : null,
      });
      setImportResult(data);
    } catch (e) {
      setError(e instanceof Error ? e.message : "导入失败");
    } finally {
      setImporting(false);
    }
  };

  const handleCopy = async () => {
    await navigator.clipboard.writeText(resultToken);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const sectionLabels: Record<string, string> = isAdminScope
    ? { main: "主模型", aux: "辅助模型", vlm: "VLM 视觉模型", profiles: "多模型配置" }
    : { user: "个人 LLM 配置" };

  return (
    <div>
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2 mb-3">
        <div className="min-w-0">
          <h3 className="font-semibold text-sm flex items-center gap-1.5">
            <Download className="h-4 w-4 flex-shrink-0" style={{ color: "var(--em-primary)" }} />
            配置导出 / 导入
          </h3>
          <p className="text-xs text-muted-foreground">
            {isAdminScope
              ? "一键导出全局模型配置（含 Key），加密分享给他人"
              : "导出个人 LLM 配置，加密备份或迁移"}
          </p>
        </div>
        <div className="flex gap-1.5 flex-shrink-0">
          {mode !== "export" && (
            <Button size="sm" variant="outline" className="h-7 text-xs gap-1" onClick={() => { resetState(); setMode("export"); }}>
              <Download className="h-3 w-3" /> 导出
            </Button>
          )}
          {mode !== "import" && (
            <Button size="sm" variant="outline" className="h-7 text-xs gap-1" onClick={() => { resetState(); setMode("import"); }}>
              <Upload className="h-3 w-3" /> 导入
            </Button>
          )}
          {mode !== "idle" && (
            <Button size="sm" variant="ghost" className="h-7 text-xs gap-1" onClick={resetState}>
              <X className="h-3 w-3" /> 关闭
            </Button>
          )}
        </div>
      </div>

      {error && (
        <div className="rounded-md border border-destructive/40 bg-destructive/5 px-3 py-2 mb-3">
          <p className="text-xs text-destructive">{error}</p>
        </div>
      )}

      {/* Export Panel */}
      {mode === "export" && !resultToken && (
        <div className="rounded-lg border border-dashed border-border p-3 space-y-3">
          <div>
            <p className="text-xs font-medium mb-2">选择导出区块</p>
            <div className="flex flex-wrap gap-x-3 gap-y-1.5">
              {Object.entries(sectionLabels).map(([key, label]) => (
                <MiniCheckbox
                  key={key}
                  checked={exportSections[key]}
                  onChange={(v) => setExportSections((prev) => ({ ...prev, [key]: v }))}
                  label={label}
                />
              ))}
            </div>
          </div>
          <div>
            <p className="text-xs font-medium mb-2">加密模式</p>
            <div className="flex flex-col sm:flex-row gap-2 sm:gap-3">
              <label className="inline-flex items-center gap-1.5 text-xs cursor-pointer">
                <input type="radio" name="export-mode" checked={exportMode === "password"} onChange={() => setExportMode("password")} />
                <Lock className="h-3 w-3" /> 口令加密（推荐）
              </label>
              <label className="inline-flex items-center gap-1.5 text-xs cursor-pointer">
                <input type="radio" name="export-mode" checked={exportMode === "simple"} onChange={() => setExportMode("simple")} />
                <Unlock className="h-3 w-3" /> 简单分享
              </label>
            </div>
          </div>
          {exportMode === "password" && (
            <div className="space-y-2">
              <div>
                <div className="flex items-center justify-between mb-0.5">
                  <label className="text-xs text-muted-foreground">设置密码</label>
                  <button
                    type="button"
                    className="inline-flex items-center gap-1 text-[11px] hover:underline"
                    style={{ color: "var(--em-primary)" }}
                    onClick={() => {
                      const chars = "ABCDEFGHJKMNPQRSTWXYZabcdefghjkmnpqrstwxyz23456789!@#$&*";
                      const arr = new Uint8Array(16);
                      crypto.getRandomValues(arr);
                      const pw = Array.from(arr, (b) => chars[b % chars.length]).join("");
                      setPassword(pw);
                      setConfirmPassword(pw);
                    }}
                  >
                    <Dices className="h-3 w-3" /> 随机生成
                  </button>
                </div>
                <Input type={password && password === confirmPassword && password.length >= 12 ? "text" : "password"} value={password} onChange={(e) => setPassword(e.target.value)} className="h-7 text-xs font-mono" placeholder="输入加密密码..." />
              </div>
              <div>
                <label className="text-xs text-muted-foreground">确认密码</label>
                <Input type="password" value={confirmPassword} onChange={(e) => setConfirmPassword(e.target.value)} className="h-7 text-xs font-mono" placeholder="再次输入密码..." />
              </div>
              {password && password === confirmPassword && password.length >= 12 && (
                <p className="text-[11px] text-emerald-600 dark:text-emerald-400 flex items-center gap-1">
                  <Check className="h-3 w-3" /> 密码已就绪，请妥善记录后发送给接收方
                </p>
              )}
            </div>
          )}
          {exportMode === "simple" && (
            <p className="text-[11px] text-amber-600 dark:text-amber-400">
              简单分享模式使用内置密钥，不能防止逆向工程。建议仅在信任的环境中使用。
            </p>
          )}
          <div className="flex justify-end">
            <Button
              size="sm"
              className="h-7 text-xs gap-1 text-white"
              style={{ backgroundColor: "var(--em-primary)" }}
              onClick={handleExport}
              disabled={exporting || (isAdminScope && !Object.entries(exportSections).some(([k, v]) => ["main","aux","vlm","profiles"].includes(k) && v))}
            >
              {exporting ? <Loader2 className="h-3 w-3 animate-spin" /> : <Download className="h-3 w-3" />}
              {exporting ? "加密中..." : "生成令牌"}
            </Button>
          </div>
        </div>
      )}

      {/* Export Result */}
      {mode === "export" && resultToken && (
        <div className="rounded-lg border border-border p-3 space-y-3">
          <div className="flex items-center gap-2">
            <CheckCircle2 className="h-4 w-4 text-emerald-500" />
            <span className="text-sm font-medium">配置导出成功</span>
          </div>
          <div className="relative">
            <textarea
              readOnly
              value={resultToken}
              className="w-full h-20 rounded-md border border-border bg-muted/30 px-3 py-2 text-[10px] font-mono resize-none focus:outline-none"
            />
            <Button
              size="sm"
              variant="outline"
              className="absolute top-2 right-2 h-6 text-[10px] gap-1"
              onClick={handleCopy}
            >
              {copied ? <Check className="h-3 w-3" /> : <Copy className="h-3 w-3" />}
              {copied ? "已复制" : "复制"}
            </Button>
          </div>
          {exportMode === "password" && (
            <p className="text-[11px] text-muted-foreground">
              请将此令牌和密码一起发送给接收方。没有密码无法解密。
            </p>
          )}
        </div>
      )}

      {/* Import Panel */}
      {mode === "import" && !importResult && (
        <div className="rounded-lg border border-dashed border-border p-3 space-y-3">
          <div>
            <label className="text-xs text-muted-foreground">粘贴配置令牌</label>
            <textarea
              value={importToken}
              onChange={(e) => handleDetectToken(e.target.value)}
              className="w-full h-20 rounded-md border border-border bg-background px-3 py-2 text-[10px] font-mono resize-none focus:outline-none focus:ring-1 focus:ring-ring mt-1"
              placeholder="粘贴 EMX1:... 令牌"
            />
          </div>
          {needsPassword === true && (
            <div>
              <label className="text-xs text-muted-foreground flex items-center gap-1">
                <Lock className="h-3 w-3" /> 此令牌需要密码
              </label>
              <Input
                type="password"
                value={importPassword}
                onChange={(e) => setImportPassword(e.target.value)}
                className="h-7 text-xs mt-1"
                placeholder="输入解密密码..."
              />
            </div>
          )}
          {needsPassword === false && (
            <p className="text-[11px] text-muted-foreground flex items-center gap-1">
              <Unlock className="h-3 w-3" /> 简单分享模式，无需密码
            </p>
          )}
          <div className="flex justify-end">
            <Button
              size="sm"
              className="h-7 text-xs gap-1 text-white"
              style={{ backgroundColor: "var(--em-primary)" }}
              onClick={handleImport}
              disabled={importing || !importToken.trim()}
            >
              {importing ? <Loader2 className="h-3 w-3 animate-spin" /> : <ClipboardPaste className="h-3 w-3" />}
              {importing ? "导入中..." : "导入配置"}
            </Button>
          </div>
        </div>
      )}

      {/* Import Result */}
      {mode === "import" && importResult && (
        <div className="rounded-lg border border-border p-3 space-y-2">
          <div className="flex items-center gap-2">
            <CheckCircle2 className="h-4 w-4 text-emerald-500" />
            <span className="text-sm font-medium">配置导入成功</span>
          </div>
          <div className="text-xs text-muted-foreground space-y-1">
            {Object.entries(importResult.imported).map(([key, value]) => (
              <p key={key}>
                <span className="font-medium">{sectionLabels[key] || key}</span>：
                {Array.isArray(value) ? value.join(", ") : String(value)}
              </p>
            ))}
          </div>
          <p className="text-[11px] text-amber-600 dark:text-amber-400">
            配置已生效。建议刷新页面以查看最新配置。
          </p>
        </div>
      )}

      {mode === "idle" && (
        <p className="text-xs text-muted-foreground text-center py-3">
          导出配置可加密分享给他人，导入令牌即可一键还原所有模型设置
        </p>
      )}
    </div>
  );
}

function CapabilityBadges({ caps }: { caps: ModelCapabilities | null }) {
  const items: { key: string; label: string; icon: React.ReactNode; value: boolean | null }[] = [
    { key: "tools", label: "工具", icon: <Wrench className="h-2.5 w-2.5" />, value: caps?.supports_tool_calling ?? null },
    { key: "vision", label: "视觉", icon: <ImageIcon className="h-2.5 w-2.5" />, value: caps?.supports_vision ?? null },
    { key: "thinking", label: "思考", icon: <Brain className="h-2.5 w-2.5" />, value: caps?.supports_thinking ?? null },
  ];

  return (
    <span className="inline-flex items-center gap-1 shrink-0 whitespace-nowrap">
      {items.map((item) => {
        let cls: string;
        let tip: string;

        if (item.value === true) {
          cls = "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400";
          tip = `${item.label}: 支持`;
        } else if (item.value === false) {
          cls = "bg-rose-500/10 text-rose-400/80 dark:text-rose-400/70";
          tip = `${item.label}: 不支持`;
        } else {
          cls = "bg-muted/60 text-muted-foreground/50";
          tip = `${item.label}: 未探测`;
        }

        return (
          <span
            key={item.key}
            title={tip}
            className={`inline-flex items-center gap-0.5 rounded-md px-1.5 py-0.5 text-[9px] leading-none font-medium transition-colors ${cls}`}
          >
            {item.icon}
            <span>{item.label}</span>
          </span>
        );
      })}
    </span>
  );
}

function CapabilityRow({
  icon,
  label,
  desc,
  value,
  error,
  onToggle,
}: {
  icon: React.ReactNode;
  label: string;
  desc: string;
  value: boolean | null;
  error?: string;
  onToggle: (v: boolean) => void;
}) {
  return (
    <div className="flex items-center gap-2.5 sm:gap-3 rounded-lg border border-border px-3 py-3 sm:py-2.5">
      <span
        className="flex-shrink-0"
        style={{ color: value === true ? "var(--em-primary)" : value === false ? "var(--destructive, #ef4444)" : "var(--muted-foreground)" }}
      >
        {icon}
      </span>
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-1.5">
          <span className="text-sm font-medium">{label}</span>
          {value === true && (
            <Badge className="text-[9px] h-4 bg-emerald-500/15 text-emerald-600 border-emerald-500/20">
              支持
            </Badge>
          )}
          {value === false && (
            <Badge variant="secondary" className="text-[9px] h-4">
              不支持
            </Badge>
          )}
          {value === null && (
            <Badge variant="outline" className="text-[9px] h-4">
              未知
            </Badge>
          )}
        </div>
        <p className="text-[11px] text-muted-foreground truncate">{desc}</p>
        {error && (
          <p className="text-[10px] text-destructive truncate mt-0.5" title={error}>
            {error}
          </p>
        )}
      </div>
      <Switch
        checked={value === true}
        onCheckedChange={onToggle}
        className="flex-shrink-0"
      />
    </div>
  );
}
