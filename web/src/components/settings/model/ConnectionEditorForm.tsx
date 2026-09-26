"use client";

import { useMemo } from "react";
import {
  CheckCircle2, ChevronRight, Eye, EyeOff, KeyRound, Link2, Loader2, Lock, Pencil, Plus,
  Save, Search, ShieldAlert, Wifi, X,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import { useAdminModel } from "./admin-model-context";
import { isSubscriptionProfile, uniqueSiblingProfileName } from "./helpers";
import { Field, FIELD_CLASS, GlassSelect, StatusCallout } from "./form-widgets";
import { ModelIdCombobox } from "./ModelIdCombobox";
import { requestModelSubTab } from "./model-subtab";

const PROTOCOL_OPTIONS = [
  { value: "auto", label: "自动匹配（推荐）" },
  { value: "openai", label: "OpenAI 兼容接口" },
  { value: "openai_responses", label: "OpenAI Responses" },
  { value: "anthropic", label: "Anthropic 原生接口" },
  { value: "gemini", label: "Gemini 原生接口" },
];

const PROTOCOL_HINTS: Record<string, string> = {
  auto: "未指定协议时按模型名和地址自动判断。OpenAI 兼容端点通常以 /v1 结尾。",
  openai: "填写兼容 OpenAI Chat Completions 的服务端点，通常以 /v1 结尾。",
  openai_responses: "填写兼容 OpenAI Responses 的服务端点地址。",
  anthropic: "填写 Anthropic 原生 Messages 端点。",
  gemini: "填写 Gemini 原生 Generative Language 端点。",
};

/**
 * 连接表单：只负责供应商凭证、接口地址、协议和连通测试。
 * 模型参数、能力与思考配置统一在「模型配置」的表单里完成。
 */
export function ConnectionEditorForm() {
  const {
    config,
    showKeys,
    setShowKeys,
    editingProfile,
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
    testingKey,
    testResult,
    setTestResult,
    formRef,
    handleTestConnection,
    handleFetchRemoteModels,
    handleAddProfile,
    handleUpdateProfile,
    resetProfileFormUi,
    addingProfile,
  } = useAdminModel();

  const protocol = profileDraft.protocol || "auto";
  const connectionResult = testResult["_profile_form"];
  const authFailed = Boolean(remoteModelError && /401|API Key|拒绝了当前/.test(remoteModelError));
  const savedProfile = config?.profiles.find((profile) => profile.name === editingProfile);
  const editingSubscription = Boolean(savedProfile && isSubscriptionProfile(savedProfile));
  const existingNames = useMemo(
    () => (config?.profiles || []).map((profile) => profile.name),
    [config?.profiles],
  );
  const usedModelIds = useMemo(() => {
    const ids = new Set<string>();
    for (const profile of config?.profiles || []) {
      if (profile.base_url === profileDraft.base_url) ids.add(profile.model);
    }
    return ids;
  }, [config?.profiles, profileDraft.base_url]);
  const listOpen = modelDropdownTarget === "_profile" && remoteModels.length > 0;
  const keySaved = Boolean(editingProfile && !profileDraft.api_key.trim());
  const canSubmit = Boolean(profileDraft.model.trim()) && Boolean(profileDraft.name.trim()) && !addingProfile;

  return (
    <div
      ref={formRef}
      className={cn(
        "rounded-xl border overflow-hidden shadow-[0_8px_24px_rgba(32,40,35,0.04)]",
        editingProfile
          ? "border-border bg-card"
          : "border-[var(--em-primary-alpha-25)] bg-[var(--em-primary-alpha-04)]",
      )}
    >
      <form onSubmit={(event) => { event.preventDefault(); void (editingProfile ? handleUpdateProfile(editingProfile) : handleAddProfile()); }}>
        <fieldset disabled={addingProfile} className="min-w-0">
          <div className="flex items-start gap-2.5 px-3.5 py-3 bg-background/90 border-b border-border/50">
            <div
              className="h-8 w-8 rounded-lg flex items-center justify-center shrink-0"
              style={{ backgroundColor: "var(--em-primary-alpha-12)", color: "var(--em-primary)" }}
            >
              {editingProfile ? <Pencil className="h-3.5 w-3.5" /> : <Plus className="h-3.5 w-3.5" />}
            </div>
            <div className="min-w-0 flex-1">
              <h4 className="text-sm font-semibold leading-none">
                {editingProfile ? "编辑供应商连接" : "添加供应商连接"}
              </h4>
              <p className="text-[11px] text-muted-foreground mt-1 leading-relaxed">
                {editingProfile
                  ? "只修改连接凭证和接口；模型参数、能力与思考配置在「模型配置」的同一张表单里维护。"
                  : "填写服务地址和凭证，然后检测可用模型。"}
              </p>
            </div>
          </div>

          <div className="px-3.5 py-3 space-y-3 bg-background">
            {editingProfile && (
              <div className="flex items-center gap-2 rounded-lg border border-border/70 bg-muted/25 px-3 py-2.5">
                <div className="min-w-0 flex-1">
                  <p className="text-[11px] font-medium text-foreground/80">已连接模型</p>
                  <p className="mt-0.5 truncate font-mono text-xs text-muted-foreground">{profileDraft.model}</p>
                </div>
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  className="h-8 shrink-0 gap-1 text-[11px]"
                  onClick={() => requestModelSubTab("roles", editingProfile)}
                >
                  配置模型参数
                  <ChevronRight className="h-3 w-3" />
                </Button>
              </div>
            )}

            {editingSubscription ? (
              <div className="flex items-center gap-1.5 rounded-lg border border-border/60 bg-muted/30 px-3 py-2 text-[11px] text-muted-foreground">
                <Lock className="h-3 w-3 shrink-0" style={{ color: "var(--em-primary)" }} />
                使用此订阅账号的授权，无需 API Key；凭证在「订阅账号」中管理
              </div>
            ) : (
              <Field
                label="API Key"
                extra={keySaved ? (
                  <span className="inline-flex items-center gap-1 rounded-full px-1.5 py-0.5 text-[10px] font-medium bg-[var(--em-primary-alpha-12)] text-[var(--em-primary)]">
                    <KeyRound className="h-2.5 w-2.5" />
                    已保存
                  </span>
                ) : null}
              >
                <div className="relative">
                  <Input
                    aria-label="API Key"
                    value={profileDraft.api_key}
                    onChange={(e) => setProfileDraft((d) => ({ ...d, api_key: e.target.value }))}
                    className={`${FIELD_CLASS} font-mono pr-9`}
                    type={showKeys["profile_api_key"] ? "text" : "password"}
                    autoComplete="off"
                    spellCheck={false}
                    aria-invalid={authFailed || undefined}
                    placeholder={editingProfile ? "已保存，留空继续使用" : "sk-..."}
                  />
                  <button
                    type="button"
                    className="absolute right-2 top-1/2 -translate-y-1/2 rounded-md text-muted-foreground hover:text-foreground hover:bg-muted/60 !min-h-0 !min-w-0 h-6 w-6 flex items-center justify-center"
                    onClick={() => setShowKeys((prev) => ({ ...prev, profile_api_key: !prev.profile_api_key }))}
                    aria-label={showKeys["profile_api_key"] ? "隐藏 API Key" : "显示 API Key"}
                  >
                    {showKeys["profile_api_key"] ? <EyeOff className="h-3.5 w-3.5" /> : <Eye className="h-3.5 w-3.5" />}
                  </button>
                </div>
              </Field>
            )}

            <div className="grid grid-cols-1 sm:grid-cols-[minmax(0,1fr)_12rem] gap-3">
              <Field label="API 请求地址">
                <Input
                  aria-label="API 请求地址"
                  value={profileDraft.base_url}
                  onChange={(e) => setProfileDraft((d) => ({ ...d, base_url: e.target.value }))}
                  className={`${FIELD_CLASS} font-mono`}
                  placeholder="https://your-api-endpoint.com/v1/"
                  spellCheck={false}
                  disabled={editingSubscription}
                />
              </Field>
              <Field label="协议">
                <GlassSelect
                  ariaLabel="协议"
                  value={protocol}
                  options={PROTOCOL_OPTIONS}
                  onChange={(next) => setProfileDraft((d) => ({ ...d, protocol: next }))}
                  disabled={editingSubscription}
                />
              </Field>
            </div>
            {!editingSubscription && (
              <p className="text-[11px] text-muted-foreground -mt-1.5 leading-relaxed">
                {PROTOCOL_HINTS[protocol] || PROTOCOL_HINTS.auto}
              </p>
            )}

            {!editingProfile && (
              <div className="relative" ref={modelDropdownRef}>
                <Field
                  label="首个模型 ID"
                  required
                  hint="保存后可在「模型配置」中继续添加同连接的其他模型。"
                  extra={remoteModels.length > 0 ? (
                    <span className="text-[10px] font-medium text-[var(--em-primary)]">
                      已检测到 {remoteModels.length} 个
                    </span>
                  ) : null}
                >
                  <div className="flex gap-1.5">
                    <ModelIdCombobox
                      value={profileDraft.model}
                      models={remoteModels}
                      usedModelIds={usedModelIds}
                      open={listOpen}
                      disabled={addingProfile}
                      onOpenChange={(open) => setModelDropdownTarget(open ? "_profile" : null)}
                      onChange={(model) => setProfileDraft((draft) => ({ ...draft, model }))}
                      onSelect={(model) => setProfileDraft((draft) => ({
                        ...draft,
                        model: model.id,
                        name: draft.name.trim() || uniqueSiblingProfileName(model.id, existingNames),
                      }))}
                    />
                    <Button
                      type="button"
                      size="sm"
                      variant="outline"
                      className="h-9 px-2.5 shrink-0 gap-1 text-[11px]"
                      style={{ color: "var(--em-primary)", borderColor: "color-mix(in srgb, var(--em-primary) 35%, transparent)" }}
                      title="从 API 自动检测可用模型"
                      disabled={fetchingModels}
                      onClick={() => handleFetchRemoteModels(
                        "_profile",
                        profileDraft.base_url || undefined,
                        profileDraft.api_key || undefined,
                        profileDraft.protocol || undefined,
                      )}
                    >
                      {fetchingModels ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Search className="h-3.5 w-3.5" />}
                      <span className="hidden min-[380px]:inline">{fetchingModels ? "检测中" : "检测模型"}</span>
                    </Button>
                  </div>
                </Field>
                {remoteModelError ? (
                  <div className="mt-2">
                    <StatusCallout
                      tone="error"
                      icon={authFailed ? <KeyRound className="h-3.5 w-3.5" /> : <ShieldAlert className="h-3.5 w-3.5" />}
                      title={remoteModelError}
                      detail={remoteModelHint}
                      onDismiss={() => {
                        setRemoteModelError(null);
                        setRemoteModelHint(null);
                      }}
                    />
                  </div>
                ) : null}
              </div>
            )}

            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <Field label="供应商名称" required={!editingProfile}>
                <Input
                  aria-label="供应商名称"
                  value={profileDraft.name}
                  onChange={(e) => setProfileDraft((d) => ({ ...d, name: e.target.value }))}
                  className={FIELD_CLASS}
                  placeholder="例如：Claude 官方"
                  disabled={Boolean(editingProfile)}
                />
              </Field>
              <Field label="备注">
                <Input
                  aria-label="备注"
                  value={profileDraft.description}
                  onChange={(e) => setProfileDraft((d) => ({ ...d, description: e.target.value }))}
                  className={FIELD_CLASS}
                  placeholder="例如：公司专用账号"
                  disabled={Boolean(editingProfile)}
                />
              </Field>
            </div>
            {editingProfile && (
              <p className="flex items-center gap-1.5 text-[10px] text-muted-foreground">
                <Link2 className="h-3 w-3" />
                名称和备注属于模型身份，请在「模型配置」的同一张表单里修改。
              </p>
            )}

            {connectionResult ? (
              <StatusCallout
                tone={connectionResult.ok ? "ok" : "error"}
                icon={connectionResult.ok
                  ? <CheckCircle2 className="h-3.5 w-3.5" />
                  : <ShieldAlert className="h-3.5 w-3.5" />}
                title={connectionResult.ok ? "连通测试成功" : (connectionResult.error || "连通测试失败")}
                detail={connectionResult.ok ? undefined : connectionResult.hint}
                onDismiss={() => setTestResult((prev) => ({ ...prev, _profile_form: null }))}
              />
            ) : null}
          </div>

          <div className="sticky bottom-0 px-3.5 py-2.5 sm:relative bg-muted/25 backdrop-blur-lg sm:backdrop-blur-none border-t border-border/50 z-10 flex items-center gap-2 sm:justify-end pb-[max(0.625rem,env(safe-area-inset-bottom))] sm:pb-2.5">
            <Button
              type="button"
              size="sm"
              variant="ghost"
              className="h-9 text-xs gap-1"
              onClick={resetProfileFormUi}
              disabled={addingProfile}
            >
              <X className="h-3.5 w-3.5" /> 取消
            </Button>
            <div className="flex gap-2 ml-auto">
              <Button
                type="button"
                size="sm"
                variant="outline"
                className="h-9 text-xs gap-1"
                disabled={!profileDraft.model || testingKey === "_profile_form"}
                onClick={() => handleTestConnection("_profile_form", {
                  name: editingProfile || undefined,
                  model: profileDraft.model,
                  base_url: profileDraft.base_url || undefined,
                  api_key: profileDraft.api_key || undefined,
                })}
              >
                {testingKey === "_profile_form" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Wifi className="h-3.5 w-3.5" />}
                {testingKey === "_profile_form" ? "测试中..." : "连通测试"}
              </Button>
              <Button
                type="submit"
                size="sm"
                className="h-9 text-xs gap-1 text-white"
                style={{ backgroundColor: "var(--em-primary)" }}
                disabled={!canSubmit}
              >
                {addingProfile ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Save className="h-3.5 w-3.5" />}
                {addingProfile ? "保存中..." : editingProfile ? "更新连接" : "添加连接"}
              </Button>
            </div>
          </div>
        </fieldset>
      </form>
    </div>
  );
}
