"use client";

import { motion, LayoutGroup } from "framer-motion";
import {
  Plus, Trash2, Pencil, Save, X, Eye, EyeOff, Loader2, CheckCircle2,
  Server, Bot, Zap, Wrench, AlertTriangle, Wifi, XCircle,
  ChevronDown, ChevronRight, ArrowRightLeft, ExternalLink, Crown, Lock, Dices,
  Search,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Switch } from "@/components/ui/switch";
import { formatModelIdForDisplay } from "@/lib/model-display";
import { useAdminModel } from "./admin-model-context";
import { SECTION_META, FIELD_LABELS, PROVIDER_PRESETS, PROVIDER_LOGO_SLUG } from "./constants";
import { ProviderLogo } from "./ProviderLogo";
import { CapabilityBadges } from "./capability-widgets";
import {
  isMaskedApiKey, isModelUnhealthy, getHealthError,
  inferProfileProvider, getProviderBrandColor, withAlpha,
} from "./helpers";
import type { ModelConfig, ModelSection } from "./types";

export function ConnectionConfigPanel() {
  const {
    config,
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
    applyingProfile,
    applyMenuOpen,
    setApplyMenuOpen,
    applyMenuRef,
    applyMenuDropUp,
    setApplyMenuDropUp,
    expandedSections,
    setExpandedSections,
    toggleSection,
    testingKey,
    testResult,
    setTestResult,
    sortedProfiles,
    formRef,
    profileCardRefs,
    applyPresetToProfileDraft,
    handleProbeOne,
    handleTestConnection,
    handleFetchRemoteModels,
    handleApplyProfileToRole,
    handleSaveSection,
    handleToggleEnabled,
    handleAddProfile,
    handleUpdateProfile,
    handleDeleteProfile,
    updateDraft,
    scrollToForm,
  } = useAdminModel();

  return (
    <div className="flex flex-col gap-2">
        <style>{`
          @keyframes main-model-glow {
            0%, 100% { box-shadow: 0 0 15px -3px var(--em-primary); }
            50% { box-shadow: 0 0 25px -3px var(--em-primary); }
          }
        `}</style>

        {/* ── Collapsible model endpoint cards ── */}
        <div className="space-y-2 order-2">
        {SECTION_META.map((section) => {
          const sectionCaps = capsMap[section.key];
          const isExpanded = !!expandedSections[section.key];
          const modelId = editDrafts[section.key]?.model || (config?.[section.key as keyof ModelConfig] as ModelSection)?.model || "";
          const isDisabled = (section.key === "aux" || section.key === "embedding") && enabledDrafts[section.key] === false;
          return (
          <div key={section.key} className={`rounded-lg border transition-colors ${
            isModelUnhealthy(sectionCaps)
              ? "border-destructive/40 bg-destructive/5"
              : "border-border"
          }`}>
            {/* ── Collapsed summary row (always visible) ── */}
            <div
              role="button"
              tabIndex={0}
              className="w-full flex items-center gap-2 px-3 py-2.5 text-left hover:bg-muted/30 transition-colors rounded-lg overflow-hidden cursor-pointer"
              onClick={() => toggleSection(section.key)}
              onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); toggleSection(section.key); } }}
            >
              <span className="text-muted-foreground transition-transform flex-shrink-0" style={{ transform: isExpanded ? "rotate(90deg)" : "rotate(0deg)" }}>
                <ChevronRight className="h-3.5 w-3.5" />
              </span>
              <span className="flex-shrink-0" style={{ color: isModelUnhealthy(sectionCaps) ? "var(--destructive, #ef4444)" : "var(--em-primary)" }}>{section.icon}</span>
              <span className="font-semibold text-sm whitespace-nowrap">{section.label}</span>
              {(section.key === "aux" || section.key === "embedding") && (
                <Switch
                  checked={section.key === "embedding" ? enabledDrafts[section.key] === true : enabledDrafts[section.key] !== false}
                  onCheckedChange={(checked) => { handleToggleEnabled(section.key, checked); }}
                  onClick={(e) => e.stopPropagation()}
                  className="ml-0.5 scale-75 origin-left"
                />
              )}
              {modelId && (
                <Badge variant="secondary" className="text-[10px] font-mono max-w-[30%] sm:max-w-[40%] truncate">
                  {modelId}
                </Badge>
              )}
              {isModelUnhealthy(sectionCaps) ? (
                <span className="inline-flex items-center gap-1 text-destructive text-[10px] shrink-0">
                  <AlertTriangle className="h-2.5 w-2.5" />
                  <span className="hidden sm:inline">连接失败</span>
                </span>
              ) : (
                <span className="hidden sm:inline-flex"><CapabilityBadges caps={sectionCaps ?? null} /></span>
              )}
              {isDisabled && (
                <span className="text-[10px] text-amber-600 dark:text-amber-400 shrink-0">已禁用</span>
              )}
            </div>

            {/* ── Expanded edit form ── */}
            {isExpanded && (
              <div className="px-4 pb-4 pt-1 border-t border-border/50">
                {isModelUnhealthy(sectionCaps) && (
                  <div className="mb-2 flex items-center gap-1.5 text-destructive">
                    <AlertTriangle className="h-3 w-3 flex-shrink-0" />
                    <span className="text-[11px] truncate" title={getHealthError(sectionCaps)}>
                      连接失败: {getHealthError(sectionCaps) || "模型不可达"}
                    </span>
                  </div>
                )}
                {isDisabled && (
                  <p className="text-xs text-amber-600 dark:text-amber-400 mb-2">{section.key === "embedding" ? "已禁用，语义检索功能关闭" : "已禁用，将回退到主模型"}</p>
                )}
                <div className={`space-y-2 transition-opacity ${isDisabled ? "opacity-40 pointer-events-none" : ""}`}>
                  {section.fields.map((field) => (
                    <div key={field} className="flex flex-col sm:flex-row sm:items-center gap-1 sm:gap-2">
                      <label className="text-xs text-muted-foreground sm:w-16 flex-shrink-0">
                        {FIELD_LABELS[field]}
                      </label>
                      <div className="flex-1 relative">
                        {field === "model" ? (
                          <div ref={modelDropdownTarget === section.key ? modelDropdownRef : undefined}>
                            <div className="flex gap-1">
                              <Input
                                value={editDrafts[section.key]?.[field] || ""}
                                onChange={(e) => {
                                  updateDraft(section.key, field, e.target.value);
                                  if (remoteModels.length > 0 && modelDropdownTarget === section.key) setModelDropdownTarget(section.key);
                                }}
                                onFocus={() => { if (remoteModels.length > 0 && modelDropdownTarget === section.key) setModelDropdownTarget(section.key); }}
                                className="h-8 text-xs font-mono flex-1"
                                placeholder={`输入 ${FIELD_LABELS[field]}...`}
                              />
                              <Button
                                type="button"
                                size="sm"
                                variant="outline"
                                className="h-8 px-2 shrink-0"
                                title="从 API 自动检测可用模型"
                                disabled={fetchingModels}
                                onClick={() => handleFetchRemoteModels(
                                  section.key,
                                  editDrafts[section.key]?.base_url || undefined,
                                  isMaskedApiKey(editDrafts[section.key]?.api_key || "") ? undefined : editDrafts[section.key]?.api_key || undefined,
                                  editDrafts[section.key]?.protocol || undefined,
                                )}
                              >
                                {fetchingModels ? (
                                  <Loader2 className="h-3 w-3 animate-spin" />
                                ) : (
                                  <Search className="h-3 w-3" />
                                )}
                              </Button>
                            </div>
                            {modelDropdownTarget === section.key && remoteModelError && (
                              <p className="text-[10px] text-destructive mt-0.5">{remoteModelError}</p>
                            )}
                            {modelDropdownTarget === section.key && remoteModels.length > 0 && (
                              <div className="absolute z-50 left-0 right-0 mt-1 max-h-48 overflow-y-auto rounded-md border border-border bg-popover shadow-md">
                                {remoteModels
                                  .filter((m) => {
                                    const cur = editDrafts[section.key]?.[field] || "";
                                    return !cur || m.id.toLowerCase().includes(cur.toLowerCase());
                                  })
                                  .map((m) => (
                                  <button
                                    key={m.id}
                                    type="button"
                                    className="w-full text-left px-3 py-1.5 text-xs font-mono hover:bg-muted/60 transition-colors flex items-center justify-between gap-2"
                                    onClick={() => {
                                      updateDraft(section.key, field, m.id);
                                      setModelDropdownTarget(null);
                                    }}
                                  >
                                    <span className="truncate">{m.id}</span>
                                    {m.owned_by && (
                                      <span className="text-[10px] text-muted-foreground shrink-0">{m.owned_by}</span>
                                    )}
                                  </button>
                                ))}
                              </div>
                            )}
                          </div>
                        ) : (
                          <>
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
                          </>
                        )}
                      </div>
                    </div>
                  ))}
                  {section.key !== "embedding" && (
                  <div className="flex flex-col sm:flex-row sm:items-center gap-1 sm:gap-2">
                    <label className="text-xs text-muted-foreground sm:w-16 shrink-0">协议</label>
                    <select
                      value={editDrafts[section.key]?.protocol || "auto"}
                      onChange={(e) => updateDraft(section.key, "protocol", e.target.value)}
                      className="w-full h-8 text-xs rounded-md border border-input bg-background px-2 py-1 font-mono focus:outline-none focus:ring-1 focus:ring-ring"
                    >
                      <option value="auto">auto（自动检测）</option>
                      <option value="openai">openai（Chat Completions）</option>
                      <option value="openai_responses">openai_responses（Responses API）</option>
                      <option value="anthropic">anthropic（Claude 原生）</option>
                      <option value="gemini">gemini（Gemini 原生）</option>
                    </select>
                  </div>
                  )}
                </div>
                {testResult[section.key] && (
                  <div className={`mt-2 rounded-md px-3 py-2 text-xs border ${
                    testResult[section.key]!.ok
                      ? "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border-emerald-500/20"
                      : "bg-destructive/10 text-destructive border-destructive/20"
                  }`}>
                    <div className="flex items-center gap-1.5">
                      {testResult[section.key]!.ok ? (
                        <CheckCircle2 className="h-3.5 w-3.5 shrink-0" />
                      ) : (
                        <XCircle className="h-3.5 w-3.5 shrink-0" />
                      )}
                      <span className="truncate">
                        {testResult[section.key]!.ok ? "连通测试成功" : testResult[section.key]!.error || "连通测试失败"}
                      </span>
                      <button
                        className="ml-auto shrink-0 hover:opacity-70"
                        onClick={() => setTestResult((prev) => ({ ...prev, [section.key]: null }))}
                      >
                        <X className="h-3 w-3" />
                      </button>
                    </div>
                    {!testResult[section.key]!.ok && testResult[section.key]!.hint && (
                      <p className="mt-1.5 pt-1.5 border-t border-destructive/15 text-[11px] leading-relaxed text-amber-700 dark:text-amber-400">
                        💡 {testResult[section.key]!.hint}
                      </p>
                    )}
                  </div>
                )}
                <div className="flex flex-col sm:flex-row justify-end gap-2 mt-3">
                  {section.key !== "embedding" && (
                  <Button
                    size="sm"
                    variant="outline"
                    className="h-8 sm:h-7 text-xs gap-1"
                    onClick={() => handleTestConnection(section.key, {
                      name: section.key,
                      model: editDrafts[section.key]?.model,
                      base_url: editDrafts[section.key]?.base_url,
                      api_key: isMaskedApiKey(editDrafts[section.key]?.api_key || "") ? undefined : editDrafts[section.key]?.api_key,
                    })}
                    disabled={testingKey === section.key}
                  >
                    {testingKey === section.key ? (
                      <Loader2 className="h-3 w-3 animate-spin" />
                    ) : (
                      <Wifi className="h-3 w-3" />
                    )}
                    {testingKey === section.key ? "测试中..." : "连通测试"}
                  </Button>
                  )}
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
            )}
          </div>
          );
        })}
        </div>

        {/* ── 模型配置 ── */}
        <div className="rounded-lg border border-border order-1" data-coach-id="coach-settings-profiles">
          <div
            role="button"
            tabIndex={0}
            className="w-full flex items-center gap-2 px-3 py-2.5 text-left hover:bg-muted/30 transition-colors rounded-lg overflow-hidden cursor-pointer"
            onClick={() => toggleSection("profiles")}
            onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); toggleSection("profiles"); } }}
          >
            <span className="text-muted-foreground transition-transform flex-shrink-0" style={{ transform: expandedSections.profiles ? "rotate(90deg)" : "rotate(0deg)" }}>
              <ChevronRight className="h-3.5 w-3.5" />
            </span>
            <Dices className="h-4 w-4 flex-shrink-0" style={{ color: "var(--em-primary)" }} />
            <span className="font-semibold text-sm">模型配置</span>
            {config?.profiles && config.profiles.length > 0 && (
              <Badge variant="secondary" className="text-[10px]">{config.profiles.length} 个档案</Badge>
            )}
            <Button
              size="sm"
              variant="outline"
              className="h-6 text-[10px] gap-0.5 flex-shrink-0 ml-auto"
              onClick={(e) => {
                e.stopPropagation();
                setExpandedSections((prev) => ({ ...prev, profiles: true }));
                setNewProfile(true);
                setEditingProfile(null);
                setProfileDraft({ name: "", model: "", api_key: "", base_url: "", description: "", protocol: "auto", thinking_mode: "auto", model_family: "", custom_extra_body: "", custom_extra_headers: "" });
                scrollToForm();
              }}
            >
              <Plus className="h-3 w-3" />
              新增
            </Button>
          </div>

          {expandedSections.profiles && (
            <div className="px-4 pb-4 pt-1 border-t border-border/50">
              {/* Inline error */}
              {profileError && (
                <div className="rounded-md border border-destructive/40 bg-destructive/5 px-3 py-2 mb-3 flex items-center gap-2">
                  <AlertTriangle className="h-3.5 w-3.5 text-destructive shrink-0" />
                  <p className="text-xs text-destructive flex-1">{profileError}</p>
                  <button onClick={() => setProfileError(null)} className="text-destructive/60 hover:text-destructive shrink-0">
                    <X className="h-3 w-3" />
                  </button>
                </div>
              )}
              {/* Provider presets - quick add */}
              {!editingProfile && (
                <div className="mb-3 space-y-2.5">
                  <p className="text-xs text-muted-foreground mb-2">常见 API Key 提供方（点击预填表单，只需补充 API Key）</p>
                  <div className="grid grid-cols-2 min-[360px]:grid-cols-3 sm:grid-cols-5 gap-1.5">
                    {PROVIDER_PRESETS.map((preset) => (
                      <button
                        key={preset.id}
                        type="button"
                        className={`text-left rounded-lg border px-2 sm:px-2.5 py-1.5 sm:py-2 transition-all ${
                          newProfile && profileDraft.model === preset.model && profileDraft.base_url === preset.base_url
                            ? "border-[var(--em-primary)]/60 bg-[var(--em-primary)]/5"
                            : "border-border hover:border-[var(--em-primary)]/40 hover:bg-[var(--em-primary)]/5"
                        }`}
                        onClick={() => applyPresetToProfileDraft(preset)}
                      >
                        <div className="flex items-center gap-1 sm:gap-1.5">
                          <ProviderLogo id={preset.id} />
                          <span className="text-[11px] sm:text-xs font-medium truncate">{preset.label}</span>
                        </div>
                        <p className="text-[10px] font-mono text-muted-foreground truncate mt-0.5 hidden sm:block">{preset.model}</p>
                        <a
                          href={preset.purchaseUrl}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="items-center gap-0.5 text-[10px] mt-1 hover:underline hidden sm:inline-flex"
                          style={{ color: "var(--em-primary)" }}
                          onClick={(e) => e.stopPropagation()}
                        >
                          获取 Key <ExternalLink className="h-2.5 w-2.5" />
                        </a>
                      </button>
                    ))}
                  </div>
                </div>
              )}

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
                        placeholder="如: gpt5"
                      />
                    </div>
                    <div className="relative" ref={modelDropdownRef}>
                      <label className="text-xs text-muted-foreground">Model ID *</label>
                      <div className="flex gap-1">
                        <Input
                          value={profileDraft.model}
                          onChange={(e) => {
                            setProfileDraft((d) => ({ ...d, model: e.target.value }));
                            // 输入时过滤已加载的模型列表
                            if (remoteModels.length > 0) setModelDropdownTarget("_profile");
                          }}
                          onFocus={() => { if (remoteModels.length > 0) setModelDropdownTarget("_profile"); }}
                          className="h-8 sm:h-7 text-xs font-mono flex-1"
                          placeholder="如: gpt-6-astra"
                        />
                        <Button
                          type="button"
                          size="sm"
                          variant="outline"
                          className="h-8 sm:h-7 px-2 shrink-0"
                          title="从 API 自动检测可用模型"
                          disabled={fetchingModels}
                          onClick={() => handleFetchRemoteModels(
                            "_profile",
                            profileDraft.base_url || undefined,
                            profileDraft.api_key || undefined,
                            profileDraft.protocol || undefined,
                          )}
                        >
                          {fetchingModels ? (
                            <Loader2 className="h-3 w-3 animate-spin" />
                          ) : (
                            <Search className="h-3 w-3" />
                          )}
                        </Button>
                      </div>
                      {remoteModelError && (
                        <p className="text-[10px] text-destructive mt-0.5">{remoteModelError}</p>
                      )}
                      {modelDropdownTarget === "_profile" && remoteModels.length > 0 && (
                        <div className="absolute z-50 top-full left-0 right-0 mt-1 max-h-48 overflow-y-auto rounded-md border border-border bg-popover shadow-md">
                          {remoteModels
                            .filter((m) => !profileDraft.model || m.id.toLowerCase().includes(profileDraft.model.toLowerCase()))
                            .map((m) => (
                            <button
                              key={m.id}
                              type="button"
                              className="w-full text-left px-3 py-1.5 text-xs font-mono hover:bg-muted/60 transition-colors flex items-center justify-between gap-2"
                              onClick={() => {
                                setProfileDraft((d) => ({ ...d, model: m.id }));
                                setModelDropdownTarget(null);
                              }}
                            >
                              <span className="truncate">{m.id}</span>
                              {m.owned_by && (
                                <span className="text-[10px] text-muted-foreground shrink-0">{m.owned_by}</span>
                              )}
                            </button>
                          ))}
                          {remoteModels.filter((m) => !profileDraft.model || m.id.toLowerCase().includes(profileDraft.model.toLowerCase())).length === 0 && (
                            <p className="px-3 py-2 text-[10px] text-muted-foreground">无匹配模型</p>
                          )}
                        </div>
                      )}
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
                  <div>
                    <label className="text-xs text-muted-foreground">协议</label>
                    <select
                      value={profileDraft.protocol || "auto"}
                      onChange={(e) => setProfileDraft((d) => ({ ...d, protocol: e.target.value }))}
                      className="w-full h-8 sm:h-7 text-xs rounded-md border border-input bg-background px-2 py-1 font-mono focus:outline-none focus:ring-1 focus:ring-ring"
                    >
                      <option value="auto">auto（自动检测）</option>
                      <option value="openai">openai（Chat Completions）</option>
                      <option value="openai_responses">openai_responses（Responses API）</option>
                      <option value="anthropic">anthropic（Claude 原生）</option>
                      <option value="gemini">gemini（Gemini 原生）</option>
                    </select>
                  </div>
                  {/* ── 高级配置（折叠区） ── */}
                  <div className="border border-border/50 rounded-md overflow-hidden">
                    <button
                      type="button"
                      className="flex items-center gap-1.5 w-full px-3 py-1.5 text-xs text-muted-foreground hover:bg-muted/50 transition-colors"
                      onClick={() => setExpandedSections((prev) => ({ ...prev, _profile_advanced: !prev._profile_advanced }))}
                    >
                      {expandedSections._profile_advanced ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
                      <Wrench className="h-3 w-3" />
                      高级配置
                      {(profileDraft.thinking_mode !== "auto" || profileDraft.model_family || profileDraft.custom_extra_body || profileDraft.custom_extra_headers) && (
                        <Badge variant="secondary" className="text-[9px] ml-1 px-1 py-0">已配置</Badge>
                      )}
                    </button>
                    {expandedSections._profile_advanced && (
                      <div className="px-3 pb-3 pt-1 border-t border-border/50 space-y-2">
                        <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                          <div>
                            <label className="text-xs text-muted-foreground">思考模式</label>
                            <select
                              value={profileDraft.thinking_mode || "auto"}
                              onChange={(e) => setProfileDraft((d) => ({ ...d, thinking_mode: e.target.value }))}
                              className="w-full h-8 sm:h-7 text-xs rounded-md border border-input bg-background px-2 py-1 font-mono focus:outline-none focus:ring-1 focus:ring-ring"
                            >
                              <option value="auto">auto（自动探测）</option>
                              <option value="disabled">disabled（禁用思考）</option>
                              <option value="claude">claude（原生 Extended Thinking）</option>
                              <option value="claude_compat">claude_compat（OAI 代理透传）</option>
                              <option value="enable_thinking">enable_thinking（DashScope/硅基流动）</option>
                              <option value="glm_thinking">glm_thinking（智谱 GLM）</option>
                              <option value="openai_reasoning">openai_reasoning（OpenAI o系列）</option>
                              <option value="openrouter">openrouter（OpenRouter）</option>
                              <option value="deepseek">deepseek（自动输出推理）</option>
                              <option value="reasoning_content_auto">reasoning_content_auto（自动）</option>
                            </select>
                          </div>
                          <div>
                            <label className="text-xs text-muted-foreground">模型族</label>
                            <select
                              value={profileDraft.model_family || ""}
                              onChange={(e) => setProfileDraft((d) => ({ ...d, model_family: e.target.value }))}
                              className="w-full h-8 sm:h-7 text-xs rounded-md border border-input bg-background px-2 py-1 font-mono focus:outline-none focus:ring-1 focus:ring-ring"
                            >
                              <option value="">auto（按模型名推断）</option>
                              <option value="claude">Claude</option>
                              <option value="gpt">GPT</option>
                              <option value="gemini">Gemini</option>
                              <option value="deepseek">DeepSeek</option>
                              <option value="qwen">Qwen（通义千问）</option>
                              <option value="glm">GLM（智谱）</option>
                              <option value="grok">Grok</option>
                            </select>
                          </div>
                        </div>
                        <div>
                          <label className="text-xs text-muted-foreground">自定义请求体 (JSON)</label>
                          <textarea
                            value={profileDraft.custom_extra_body || ""}
                            onChange={(e) => setProfileDraft((d) => ({ ...d, custom_extra_body: e.target.value }))}
                            placeholder='{"temperature": 0.7}'
                            rows={2}
                            className="w-full text-xs rounded-md border border-input bg-background px-2 py-1.5 font-mono focus:outline-none focus:ring-1 focus:ring-ring resize-y min-h-[2rem]"
                          />
                        </div>
                        <div>
                          <label className="text-xs text-muted-foreground">自定义请求头 (JSON)</label>
                          <textarea
                            value={profileDraft.custom_extra_headers || ""}
                            onChange={(e) => setProfileDraft((d) => ({ ...d, custom_extra_headers: e.target.value }))}
                            placeholder='{"x-custom-auth": "xxx"}'
                            rows={2}
                            className="w-full text-xs rounded-md border border-input bg-background px-2 py-1.5 font-mono focus:outline-none focus:ring-1 focus:ring-ring resize-y min-h-[2rem]"
                          />
                        </div>
                      </div>
                    )}
                  </div>
                  {testResult["_profile_form"] && (
                    <div className={`rounded-md px-3 py-2 text-xs border ${
                      testResult["_profile_form"]!.ok
                        ? "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border-emerald-500/20"
                        : "bg-destructive/10 text-destructive border-destructive/20"
                    }`}>
                      <div className="flex items-center gap-1.5">
                        {testResult["_profile_form"]!.ok ? (
                          <CheckCircle2 className="h-3.5 w-3.5 shrink-0" />
                        ) : (
                          <XCircle className="h-3.5 w-3.5 shrink-0" />
                        )}
                        <span className="truncate">
                          {testResult["_profile_form"]!.ok ? "连通测试成功" : testResult["_profile_form"]!.error || "连通测试失败"}
                        </span>
                        <button
                          className="ml-auto shrink-0 hover:opacity-70"
                          onClick={() => setTestResult((prev) => ({ ...prev, _profile_form: null }))}
                        >
                          <X className="h-3 w-3" />
                        </button>
                      </div>
                      {!testResult["_profile_form"]!.ok && testResult["_profile_form"]!.hint && (
                        <p className="mt-1.5 pt-1.5 border-t border-destructive/15 text-[11px] leading-relaxed text-amber-700 dark:text-amber-400">
                          💡 {testResult["_profile_form"]!.hint}
                        </p>
                      )}
                    </div>
                  )}
                  <div className="sticky bottom-0 -mx-3 -mb-3 px-3 py-2.5 sm:relative sm:mx-0 sm:mb-0 sm:px-0 sm:py-0 sm:pt-1 bg-background/80 backdrop-blur-lg sm:bg-transparent sm:backdrop-blur-none border-t border-border/40 sm:border-t-0 z-10 flex items-center gap-2 sm:justify-end pb-[max(0.625rem,env(safe-area-inset-bottom))] sm:pb-0">
                    <Button
                      size="sm"
                      variant="ghost"
                      className="h-9 sm:h-7 text-xs gap-1"
                      onClick={() => {
                        setNewProfile(false);
                        setEditingProfile(null);
                        setProfileError(null);
                        setTestResult((prev) => ({ ...prev, _profile_form: null }));
                      }}
                    >
                      <X className="h-3 w-3" /> 取消
                    </Button>
                    <div className="flex gap-2 ml-auto">
                      <Button
                        size="sm"
                        variant="outline"
                        className="h-9 sm:h-7 text-xs gap-1"
                        disabled={!profileDraft.model || testingKey === "_profile_form"}
                        onClick={() => handleTestConnection("_profile_form", {
                          model: profileDraft.model,
                          base_url: profileDraft.base_url || undefined,
                          api_key: profileDraft.api_key || undefined,
                        })}
                      >
                        {testingKey === "_profile_form" ? (
                          <Loader2 className="h-3 w-3 animate-spin" />
                        ) : (
                          <Wifi className="h-3 w-3" />
                        )}
                        {testingKey === "_profile_form" ? "测试中..." : "连通测试"}
                      </Button>
                      <Button
                        size="sm"
                        className="h-9 sm:h-7 text-xs gap-1 text-white"
                        style={{ backgroundColor: "var(--em-primary)" }}
                        disabled={!profileDraft.name || !profileDraft.model || addingProfile}
                        onClick={() =>
                          editingProfile
                            ? handleUpdateProfile(editingProfile)
                            : handleAddProfile()
                        }
                      >
                        {addingProfile ? (
                          <Loader2 className="h-3 w-3 animate-spin" />
                        ) : (
                          <Save className="h-3 w-3" />
                        )}
                        {addingProfile ? "添加中..." : editingProfile ? "更新" : "添加"}
                      </Button>
                    </div>
                  </div>
                </div>
              )}

              {/* Profile list */}
              <LayoutGroup>
              <div className="space-y-2">
                {sortedProfiles.map((p) => {
                  const pCaps = capsMap[p.name];
                  const isHighlighted = highlightProfile === p.name;
                  const isCodexProfile = p.model.startsWith("openai-codex/");
                  const isMainProfile = !isCodexProfile && p.model === config?.main?.model;
                  const isUnhealthy = isModelUnhealthy(pCaps);
                  const initials = p.name.slice(0, 2).toUpperCase();
                  const providerId = inferProfileProvider(p);
                  const hasProviderLogo = !!(providerId && PROVIDER_LOGO_SLUG[providerId]);
                  const providerColor = getProviderBrandColor(providerId);
                  return (
                  <motion.div
                    layout
                    layoutId={`profile-${p.name}`}
                    transition={{ layout: { type: "spring", stiffness: 400, damping: 30 } }}
                    key={p.name}
                    ref={(el: HTMLDivElement | null) => { profileCardRefs.current[p.name] = el; }}
                    className={`group rounded-xl border text-sm transition-[border-color,background-color] duration-200 ${
                      isCodexProfile
                        ? "border-[var(--em-primary)]/25 bg-gradient-to-r from-[var(--em-primary)]/5 to-transparent"
                        : isMainProfile
                          ? "border-[var(--em-primary)] bg-gradient-to-br from-[var(--em-primary)]/10 via-[var(--em-primary)]/3 to-transparent ring-1 ring-[var(--em-primary)]/15 cursor-pointer"
                          : isHighlighted
                            ? "border-[var(--em-primary)] bg-[var(--em-primary)]/5 ring-1 ring-[var(--em-primary)]/20 scale-[1.005] cursor-pointer shadow-sm"
                            : isUnhealthy
                              ? "border-destructive/30 bg-destructive/3 cursor-pointer hover:border-destructive/50 hover:shadow-sm"
                              : "border-border/60 bg-card cursor-pointer hover:border-border hover:shadow-sm hover:bg-muted/20"
                    }`}
                    style={isMainProfile ? { animation: "main-model-glow 3s ease-in-out infinite" } : undefined}
                    onClick={() => {
                      if (isCodexProfile) return;
                      setEditingProfile(p.name);
                      setNewProfile(false);
                      setProfileDraft({
                        name: p.name,
                        model: p.model,
                        api_key: "",
                        base_url: p.base_url,
                        description: p.description,
                        protocol: p.protocol || "auto",
                        thinking_mode: p.thinking_mode || "auto",
                        model_family: p.model_family || "",
                        custom_extra_body: p.custom_extra_body || "",
                        custom_extra_headers: p.custom_extra_headers || "",
                      });
                      scrollToForm();
                    }}
                  >
                    {/* ── Main row ── */}
                    <div className="flex items-center gap-3 px-3 py-2.5">
                      {/* Avatar */}
                      <div
                        className={`flex-shrink-0 w-8 h-8 rounded-lg flex items-center justify-center text-[11px] font-bold select-none ${
                        isCodexProfile
                          ? "bg-[var(--em-primary)]/15 text-[var(--em-primary)]"
                          : hasProviderLogo
                            ? (isMainProfile ? "bg-[var(--em-primary)]/10" : "bg-muted/40")
                          : isMainProfile
                            ? "bg-amber-500/15 text-amber-600 dark:text-amber-400"
                          : isUnhealthy
                            ? "bg-destructive/10 text-destructive"
                            : "bg-muted text-muted-foreground group-hover:bg-[var(--em-primary)]/10 group-hover:text-[var(--em-primary)] transition-colors"
                        }`}
                        style={hasProviderLogo
                          ? {
                            backgroundColor: withAlpha(providerColor, "1A"),
                            color: providerColor,
                          }
                          : undefined}
                      >
                        {hasProviderLogo && providerId
                          ? <ProviderLogo id={providerId} color={providerColor} />
                          : isCodexProfile
                            ? <Lock className="h-3.5 w-3.5" />
                            : initials}
                      </div>

                      {/* Name + model */}
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2 min-w-0">
                          <span className="font-semibold text-sm truncate">{p.name}</span>
                          {isCodexProfile && (
                            <span className="flex-shrink-0 inline-flex items-center gap-0.5 rounded-full px-1.5 py-0.5 text-[9px] font-semibold bg-[var(--em-primary)]/15 text-[var(--em-primary)]">
                              <Lock className="h-2 w-2" />
                              OAuth
                            </span>
                          )}
                          {isMainProfile && (
                            <span className="flex-shrink-0 inline-flex items-center gap-0.5 rounded-full px-1.5 py-0.5 text-[9px] font-semibold bg-amber-500/15 text-amber-600 dark:text-amber-400 border border-amber-500/20">
                              <Crown className="h-2.5 w-2.5" />
                              主模型
                            </span>
                          )}
                          {isUnhealthy && (
                            <span className="flex-shrink-0 inline-flex items-center gap-0.5 text-destructive text-[9px] font-medium">
                              <AlertTriangle className="h-2.5 w-2.5" />
                              <span className="hidden sm:inline">不可用</span>
                            </span>
                          )}
                        </div>
                        <div className="flex items-center gap-1.5 mt-0.5 min-w-0">
                          <span className="text-[10px] font-mono text-muted-foreground truncate max-w-[140px] sm:max-w-[200px]">{formatModelIdForDisplay(p.model)}</span>
                          {!isUnhealthy && p.description && (
                            <span className="hidden sm:inline text-[10px] text-muted-foreground/70 truncate">· {p.description}</span>
                          )}
                        </div>
                      </div>

                      {/* Capability badges — hidden on mobile */}
                      <div className="hidden sm:block flex-shrink-0">
                        {isUnhealthy ? null : <CapabilityBadges caps={pCaps ?? null} />}
                      </div>

                      {/* Action buttons */}
                      <div className="flex items-center gap-0.5 flex-shrink-0 opacity-100 sm:opacity-60 sm:group-hover:opacity-100 transition-opacity touch-show">
                        {/* Apply role dropdown */}
                        <div className="relative" ref={applyMenuOpen === p.name ? applyMenuRef : undefined}>
                          <button
                            title="应用到角色"
                            className="h-7 w-7 sm:h-6 sm:w-6 flex items-center justify-center rounded-md text-muted-foreground hover:text-foreground hover:bg-muted/60 transition-colors disabled:opacity-40"
                            onClick={(e) => {
                              e.stopPropagation();
                              if (applyMenuOpen === p.name) {
                                setApplyMenuOpen(null);
                              } else {
                                const rect = (e.currentTarget as HTMLElement).getBoundingClientRect();
                                const dropdownHeight = 160;
                                const spaceBelow = window.innerHeight - rect.bottom;
                                setApplyMenuDropUp(spaceBelow < dropdownHeight && rect.top > dropdownHeight);
                                setApplyMenuOpen(p.name);
                              }
                            }}
                            disabled={applyingProfile === p.name}
                          >
                            {applyingProfile === p.name ? (
                              <Loader2 className="h-3.5 w-3.5 animate-spin" />
                            ) : (
                              <ArrowRightLeft className="h-3.5 w-3.5" />
                            )}
                          </button>
                          {applyMenuOpen === p.name && (
                              <div className={`absolute right-0 z-50 w-40 rounded-xl border border-border/60 bg-popover shadow-lg overflow-hidden ${
                                applyMenuDropUp ? "bottom-full mb-1.5" : "top-full mt-1.5"
                              }`}>
                                <div className="px-2.5 py-1.5 border-b border-border/40">
                                  <p className="text-[10px] font-medium text-muted-foreground uppercase tracking-wide">应用到角色</p>
                                </div>
                                <div className="p-1">
                                  {([
                                    { role: "main" as const, label: "主模型", icon: <Server className="h-3.5 w-3.5" />, color: "text-blue-500" },
                                    { role: "aux" as const, label: "辅助模型", icon: <Bot className="h-3.5 w-3.5" />, color: "text-violet-500" },
                                  ]).map((item) => (
                                    <button
                                      key={item.role}
                                      className="flex items-center gap-2.5 w-full px-2.5 py-2 text-xs rounded-lg hover:bg-muted/60 active:bg-muted transition-colors text-left"
                                      onClick={(e) => { e.stopPropagation(); handleApplyProfileToRole(p, item.role); }}
                                    >
                                      <span className={item.color}>{item.icon}</span>
                                      <span className="font-medium">用作{item.label}</span>
                                    </button>
                                  ))}
                                </div>
                              </div>
                          )}
                        </div>
                        {/* Probe */}
                        <button
                          title="探测能力"
                          className="h-7 w-7 sm:h-6 sm:w-6 flex items-center justify-center rounded-md text-muted-foreground hover:text-foreground hover:bg-muted/60 transition-colors disabled:opacity-40"
                          onClick={(e) => { e.stopPropagation(); handleProbeOne(p.name); }}
                          disabled={probingKey === p.name}
                        >
                          {probingKey === p.name ? (
                            <Loader2 className="h-3.5 w-3.5 animate-spin" />
                          ) : (
                            <Zap className="h-3.5 w-3.5" />
                          )}
                        </button>
                        {/* Edit */}
                        {!isCodexProfile && (
                          <button
                            title="编辑"
                            className="h-7 w-7 sm:h-6 sm:w-6 flex items-center justify-center rounded-md text-muted-foreground hover:text-foreground hover:bg-muted/60 transition-colors"
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
                                protocol: p.protocol || "auto",
                                thinking_mode: p.thinking_mode || "auto",
                                model_family: p.model_family || "",
                                custom_extra_body: p.custom_extra_body || "",
                                custom_extra_headers: p.custom_extra_headers || "",
                              });
                              scrollToForm();
                            }}
                          >
                            <Pencil className="h-3.5 w-3.5" />
                          </button>
                        )}
                        {/* Delete */}
                        <button
                          title="删除"
                          className="h-7 w-7 sm:h-6 sm:w-6 flex items-center justify-center rounded-md text-muted-foreground hover:text-destructive hover:bg-destructive/10 transition-colors"
                          onClick={(e) => { e.stopPropagation(); handleDeleteProfile(p.name); }}
                        >
                          <Trash2 className="h-3.5 w-3.5" />
                        </button>
                      </div>
                    </div>

                    {/* ── Mobile capability + description row ── */}
                    {(!isUnhealthy || isCodexProfile) && (
                      <div className="sm:hidden flex items-center gap-2 px-3 pb-2.5 -mt-1">
                        <CapabilityBadges caps={pCaps ?? null} />
                        {p.description && !isCodexProfile && (
                          <span className="text-[10px] text-muted-foreground/70 truncate">· {p.description}</span>
                        )}
                        {isCodexProfile && (
                          <span className="text-[10px] text-muted-foreground/70">通过 Codex 卡片管理</span>
                        )}
                      </div>
                    )}

                    {/* ── Error row ── */}
                    {isUnhealthy && !isCodexProfile && (
                      <div className="flex items-center gap-1.5 px-3 pb-2.5 -mt-0.5">
                        <AlertTriangle className="h-3 w-3 flex-shrink-0 text-destructive" />
                        <p className="text-[10px] text-destructive truncate" title={getHealthError(pCaps)}>
                          {getHealthError(pCaps) || "连接失败"}
                        </p>
                      </div>
                    )}
                  </motion.div>
                  );
                })}
                {sortedProfiles.length === 0 && !newProfile && (
                  <div className="flex flex-col items-center gap-2 py-8 text-center">
                    <div className="w-10 h-10 rounded-xl bg-muted/50 flex items-center justify-center">
                      <Bot className="h-5 w-5 text-muted-foreground/50" />
                    </div>
                    <p className="text-xs text-muted-foreground">
                      暂无模型配置
                    </p>
                    <p className="text-[10px] text-muted-foreground/60">
                      点击上方提供方预设或「新增」来添加
                    </p>
                  </div>
                )}
              </div>
              </LayoutGroup>
            </div>
          )}
        </div>

    </div>
  );
}
