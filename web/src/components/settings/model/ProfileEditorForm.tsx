"use client";

import { useMemo, type ReactNode } from "react";
import {
  Check, ChevronDown, ChevronRight, Eye, EyeOff, KeyRound, Loader2, Lock, Pencil,
  Plus, Save, Search, ShieldAlert, Wifi, Wrench, X, CheckCircle2,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { glassMenuItemClass, glassMenuPanelClass } from "@/components/ui/menu-panel";
import { cn } from "@/lib/utils";
import { useAdminModel } from "./admin-model-context";
import { isSubscriptionProfile, uniqueSiblingProfileName, filterModelPickerList } from "./helpers";

const FIELD = "h-9 text-xs rounded-lg";

const PROTOCOL_OPTIONS = [
  { value: "auto", label: "auto（自动检测）" },
  { value: "openai", label: "openai（Chat Completions）" },
  { value: "openai_responses", label: "openai_responses（Responses）" },
  { value: "anthropic", label: "anthropic（Claude 原生）" },
  { value: "gemini", label: "gemini（Gemini 原生）" },
];

const THINKING_OPTIONS = [
  { value: "auto", label: "auto（自动探测）" },
  { value: "disabled", label: "disabled（禁用思考）" },
  { value: "claude", label: "claude（原生 Extended Thinking）" },
  { value: "claude_compat", label: "claude_compat（OAI 代理透传）" },
  { value: "enable_thinking", label: "enable_thinking（DashScope/硅基流动）" },
  { value: "glm_thinking", label: "glm_thinking（智谱 GLM）" },
  { value: "openai_reasoning", label: "openai_reasoning（OpenAI o系列）" },
  { value: "openrouter", label: "openrouter（OpenRouter）" },
  { value: "deepseek", label: "deepseek（自动输出推理）" },
  { value: "reasoning_content_auto", label: "reasoning_content_auto（自动）" },
];

const FAMILY_OPTIONS = [
  { value: "", label: "auto（按模型名推断）" },
  { value: "claude", label: "Claude" },
  { value: "gpt", label: "GPT" },
  { value: "gemini", label: "Gemini" },
  { value: "deepseek", label: "DeepSeek" },
  { value: "qwen", label: "Qwen（通义千问）" },
  { value: "glm", label: "GLM（智谱）" },
  { value: "grok", label: "Grok" },
];

const PROTOCOL_HINTS: Record<string, string> = {
  auto: "未指定协议时按模型名和地址自动判断。OpenAI 兼容端点通常以 /v1 结尾。",
  openai: "填写兼容 OpenAI Chat Completions 的服务端点，通常以 /v1 结尾。",
  openai_responses: "填写兼容 OpenAI Responses 的服务端点地址。",
  anthropic: "填写 Anthropic 原生 Messages 端点。",
  gemini: "填写 Gemini 原生 Generative Language 端点。",
};

function Field({
  label,
  hint,
  required,
  extra,
  children,
}: {
  label: string;
  hint?: string;
  required?: boolean;
  extra?: ReactNode;
  children: ReactNode;
}) {
  return (
    <div className="space-y-1">
      <div className="flex items-center gap-1.5 min-h-[1.125rem]">
        <label className="text-[11px] font-medium text-foreground/80">
          {label}
          {required ? <span className="ml-0.5 text-destructive/80">*</span> : null}
        </label>
        {extra}
      </div>
      {children}
      {hint ? <p className="text-[11px] text-muted-foreground leading-relaxed">{hint}</p> : null}
    </div>
  );
}

function GlassSelect({
  value,
  options,
  onChange,
  ariaLabel,
  disabled,
}: {
  value: string;
  options: { value: string; label: string }[];
  onChange: (value: string) => void;
  ariaLabel: string;
  disabled?: boolean;
}) {
  const current = options.find((option) => option.value === value)?.label ?? value;
  return (
    <DropdownMenu modal={false}>
      <DropdownMenuTrigger asChild>
        <button
          type="button"
          aria-label={ariaLabel}
          disabled={disabled}
          className="inline-flex items-center gap-1.5 w-full h-9 rounded-lg border border-input bg-background px-2.5 text-left text-xs hover:bg-muted/40 disabled:opacity-60 disabled:hover:bg-transparent disabled:cursor-not-allowed"
        >
          <span className="flex-1 truncate">{current}</span>
          <ChevronDown className="h-3.5 w-3.5 text-muted-foreground shrink-0" />
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent
        align="end"
        sideOffset={6}
        className={cn(glassMenuPanelClass, "z-[80] min-w-[16rem]")}
      >
        {options.map((option) => (
          <DropdownMenuItem
            key={option.value || "empty"}
            className={glassMenuItemClass}
            onClick={() => onChange(option.value)}
          >
            <span className="flex-1">{option.label}</span>
            {option.value === value && (
              <Check className="h-3.5 w-3.5" style={{ color: "var(--em-primary)" }} />
            )}
          </DropdownMenuItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

function StatusCallout({
  tone,
  icon,
  title,
  detail,
  onDismiss,
}: {
  tone: "ok" | "error";
  icon: ReactNode;
  title: string;
  detail?: string | null;
  onDismiss?: () => void;
}) {
  return (
    <div
      className={cn(
        "rounded-xl px-3 py-2.5 text-xs border",
        tone === "ok"
          ? "bg-emerald-500/[0.08] text-emerald-700 dark:text-emerald-400 border-emerald-500/20"
          : "bg-destructive/[0.06] text-destructive border-destructive/20",
      )}
    >
      <div className="flex items-start gap-2">
        <span className="mt-0.5 shrink-0">{icon}</span>
        <div className="flex-1 min-w-0 space-y-1">
          <p className="font-medium leading-snug">{title}</p>
          {detail ? (
            <p
              className={cn(
                "leading-relaxed",
                tone === "error"
                  ? "text-amber-700 dark:text-amber-400"
                  : "text-emerald-700/80 dark:text-emerald-400/80",
              )}
            >
              {detail}
            </p>
          ) : null}
        </div>
        {onDismiss ? (
          <button
            type="button"
            className="shrink-0 mt-0.5 text-current/60 hover:text-current"
            onClick={onDismiss}
            aria-label="关闭提示"
          >
            <X className="h-3.5 w-3.5" />
          </button>
        ) : null}
      </div>
    </div>
  );
}

export function ProfileEditorForm() {
  const {
    config,
    showKeys,
    setShowKeys,
    editingProfile,
    siblingSourceName,
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
    expandedSections,
    setExpandedSections,
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
  const authFailed = Boolean(
    remoteModelError && /401|API Key|拒绝了当前/.test(remoteModelError),
  );
  const credentialName = editingProfile || siblingSourceName || undefined;
  const addingSibling = Boolean(siblingSourceName && !editingProfile);
  const siblingSource = useMemo(
    () => (config?.profiles || []).find((p) => p.name === siblingSourceName),
    [config?.profiles, siblingSourceName],
  );
  // 订阅（OAuth）sibling：沿用订阅凭证，无需 API Key，连接参数固定不可改。
  const codexSibling = Boolean(addingSibling && siblingSource && isSubscriptionProfile(siblingSource));
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
  const filteredRemoteModels = filterModelPickerList(remoteModels, profileDraft.model);
  const listOpen = modelDropdownTarget === "_profile" && remoteModels.length > 0;
  const keySaved = Boolean(editingProfile && !profileDraft.api_key.trim());
  const keyInherited = Boolean(addingSibling && !profileDraft.api_key.trim());
  const advancedConfigured = Boolean(
    profileDraft.thinking_mode !== "auto"
      || profileDraft.model_family
      || profileDraft.custom_extra_body
      || profileDraft.custom_extra_headers,
  );
  const formTitle = editingProfile ? "编辑提供商" : addingSibling ? "添加模型" : "添加提供商";
  const formHint = editingProfile
    ? "修改连接信息。API Key 留空则继续使用已保存的凭证。"
    : codexSibling
      ? "沿用 ChatGPT 订阅 OAuth 凭证，从可用列表选择要加入的模型。"
      : addingSibling
        ? "沿用此提供商的地址和凭证，从已检测列表中选择要加入的模型。"
        : "填写服务地址和凭证，然后检测可用模型。";
  const canSubmit = Boolean(profileDraft.model) && (Boolean(profileDraft.name.trim()) || addingSibling) && !addingProfile;

  return (
    <div
      ref={formRef}
      className={cn(
        "rounded-xl border overflow-hidden shadow-[0_8px_24px_rgba(32,40,35,0.04)]",
        editingProfile || addingSibling
          ? "border-border bg-card"
          : "border-[var(--em-primary-alpha-25)] bg-[var(--em-primary-alpha-04)]",
      )}
    >
      <div className="flex items-start gap-2.5 px-3.5 py-3 bg-background/90 border-b border-border/50">
        <div
          className="h-8 w-8 rounded-lg flex items-center justify-center shrink-0"
          style={{ backgroundColor: "var(--em-primary-alpha-12)", color: "var(--em-primary)" }}
        >
          {editingProfile ? <Pencil className="h-3.5 w-3.5" /> : <Plus className="h-3.5 w-3.5" />}
        </div>
        <div className="min-w-0 flex-1">
          <h4 className="text-sm font-semibold leading-none">
            {formTitle}
          </h4>
          <p className="text-[11px] text-muted-foreground mt-1 leading-relaxed">
            {formHint}
          </p>
        </div>
      </div>

      <div className="px-3.5 py-3 space-y-3 bg-background">
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <Field label="供应商名称" required={!addingSibling}>
            <Input
              value={profileDraft.name}
              onChange={(e) => setProfileDraft((d) => ({ ...d, name: e.target.value }))}
              className={FIELD}
              placeholder={addingSibling ? "选择模型后自动填写" : "例如：Claude 官方"}
            />
          </Field>
          <Field label="备注">
            <Input
              value={profileDraft.description}
              onChange={(e) => setProfileDraft((d) => ({ ...d, description: e.target.value }))}
              className={FIELD}
              placeholder="例如：公司专用账号"
            />
          </Field>
        </div>

        {codexSibling ? (
          <div className="flex items-center gap-1.5 rounded-lg border border-border/60 bg-muted/30 px-3 py-2 text-[11px] text-muted-foreground">
            <Lock className="h-3 w-3 shrink-0" style={{ color: "var(--em-primary)" }} />
            使用 ChatGPT 订阅 OAuth 凭证，无需 API Key
          </div>
        ) : (
        <Field
          label="API Key"
          extra={keySaved ? (
            <span className="inline-flex items-center gap-1 rounded-full px-1.5 py-0.5 text-[10px] font-medium bg-[var(--em-primary-alpha-12)] text-[var(--em-primary)]">
              <KeyRound className="h-2.5 w-2.5" />
              已保存
            </span>
          ) : keyInherited ? (
            <span className="inline-flex items-center gap-1 rounded-full px-1.5 py-0.5 text-[10px] font-medium bg-[var(--em-primary-alpha-12)] text-[var(--em-primary)]">
              <KeyRound className="h-2.5 w-2.5" />
              沿用凭证
            </span>
          ) : null}
        >
          <div className="relative">
            <Input
              value={profileDraft.api_key}
              onChange={(e) => setProfileDraft((d) => ({ ...d, api_key: e.target.value }))}
              className={`${FIELD} font-mono pr-9`}
              type={showKeys["profile_api_key"] ? "text" : "password"}
              autoComplete="off"
              spellCheck={false}
              aria-invalid={authFailed || undefined}
              placeholder={editingProfile ? "已保存，留空继续使用" : keyInherited ? "已沿用所属提供商凭证，留空即可" : "sk-..."}
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
              value={profileDraft.base_url}
              onChange={(e) => setProfileDraft((d) => ({ ...d, base_url: e.target.value }))}
              className={`${FIELD} font-mono`}
              placeholder="https://your-api-endpoint.com/v1/"
              spellCheck={false}
              disabled={codexSibling}
            />
          </Field>
          <Field label="协议">
            <GlassSelect
              ariaLabel="协议"
              value={protocol}
              options={PROTOCOL_OPTIONS}
              onChange={(next) => setProfileDraft((d) => ({ ...d, protocol: next }))}
              disabled={codexSibling}
            />
          </Field>
        </div>
        {!codexSibling && (
        <p className="text-[11px] text-muted-foreground -mt-1.5 leading-relaxed">
          {PROTOCOL_HINTS[protocol] || PROTOCOL_HINTS.auto}
        </p>
        )}

        <div className="relative" ref={modelDropdownRef}>
          <Field
            label="Model ID"
            required
            extra={remoteModels.length > 0 ? (
              <span className="text-[10px] font-medium text-[var(--em-primary)]">
                已检测到 {remoteModels.length} 个
              </span>
            ) : null}
          >
            <div className="flex gap-1.5">
              <div className="relative flex-1 min-w-0">
                <Input
                  value={profileDraft.model}
                  onChange={(e) => setProfileDraft((d) => ({ ...d, model: e.target.value }))}
                  onMouseDown={(e) => {
                    if (e.button !== 0 || remoteModels.length === 0) return;
                    if (document.activeElement !== e.currentTarget) return;
                    setModelDropdownTarget((current) => (current === "_profile" ? null : "_profile"));
                  }}
                  onKeyDown={(e) => {
                    if (e.key === "Escape" && listOpen) {
                      e.preventDefault();
                      setModelDropdownTarget(null);
                    }
                  }}
                  aria-expanded={listOpen}
                  aria-haspopup="listbox"
                  className={cn(FIELD, "font-mono", remoteModels.length > 0 && "pr-8")}
                  placeholder={
                    remoteModels.length > 0
                      ? (listOpen ? `在 ${remoteModels.length} 个模型中搜索` : "输入，再点一次展开列表")
                      : addingSibling
                        ? "检测后可从列表选择，或直接填写"
                        : "例如：claude-sonnet-5"
                  }
                  spellCheck={false}
                />
                {remoteModels.length > 0 ? (
                  <ChevronDown
                    className={cn(
                      "absolute right-2.5 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-muted-foreground pointer-events-none transition-transform",
                      listOpen && "rotate-180",
                    )}
                  />
                ) : null}
              </div>
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
                  credentialName,
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
          {listOpen && (
            <div
              className={cn(glassMenuPanelClass, "relative z-20 mt-1.5 overflow-hidden p-1.5")}
              onMouseDown={(e) => e.preventDefault()}
            >
              <p className="text-[11px] font-medium text-muted-foreground px-2 py-1.5">
                {filteredRemoteModels.length === remoteModels.length
                  ? `检测到 ${remoteModels.length} 个模型`
                  : `${filteredRemoteModels.length} / ${remoteModels.length} 个匹配`}
              </p>
              <div className="max-h-56 overflow-y-auto overscroll-contain">
                {filteredRemoteModels.map((m) => {
                  const selected = profileDraft.model === m.id;
                  const alreadyAdded = usedModelIds.has(m.id);
                  return (
                    <button
                      key={m.id}
                      type="button"
                      className={cn(
                        "w-full text-left px-2.5 py-2 rounded-xl text-xs font-mono transition-colors flex items-center justify-between gap-2",
                        selected
                          ? "bg-[var(--em-primary-alpha-10)] text-foreground"
                          : "hover:bg-muted/60",
                      )}
                      onClick={() => {
                        setProfileDraft((d) => ({
                          ...d,
                          model: m.id,
                          name: d.name.trim()
                            || (codexSibling && !existingNames.includes(m.id)
                              ? m.id
                              : uniqueSiblingProfileName(m.id, existingNames)),
                          description: codexSibling && !d.description.trim() && m.owned_by
                            ? `${m.owned_by} — OAuth 登录（无需 API Key）`
                            : d.description,
                        }));
                      }}
                    >
                      <span className="truncate">{m.id}</span>
                      <span className="flex items-center gap-1.5 shrink-0">
                        {alreadyAdded ? (
                          <span className="text-[10px] font-sans text-muted-foreground">已添加</span>
                        ) : null}
                        {m.owned_by ? (
                          <span className="text-[10px] font-sans text-muted-foreground">{m.owned_by}</span>
                        ) : null}
                        {selected ? <Check className="h-3.5 w-3.5" style={{ color: "var(--em-primary)" }} /> : null}
                      </span>
                    </button>
                  );
                })}
                {filteredRemoteModels.length === 0 && (
                  <p className="px-2.5 py-2 text-[11px] text-muted-foreground">无匹配模型</p>
                )}
              </div>
            </div>
          )}
        </div>

        <div className="rounded-xl border border-border/60 overflow-hidden">
          <button
            type="button"
            className="flex items-center gap-1.5 w-full px-3 py-2 text-[11px] font-medium text-muted-foreground hover:bg-muted/40 transition-colors"
            onClick={() => setExpandedSections((prev) => ({ ...prev, _profile_advanced: !prev._profile_advanced }))}
          >
            {expandedSections._profile_advanced ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
            <Wrench className="h-3.5 w-3.5" />
            高级配置
            {advancedConfigured ? (
              <Badge variant="secondary" className="text-[9px] ml-1 px-1.5 py-0">已配置</Badge>
            ) : null}
          </button>
          {expandedSections._profile_advanced && (
            <div className="px-3 pb-3 pt-2 border-t border-border/50 space-y-3 bg-muted/15">
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                <Field label="思考模式">
                  <GlassSelect
                    ariaLabel="思考模式"
                    value={profileDraft.thinking_mode || "auto"}
                    options={THINKING_OPTIONS}
                    onChange={(next) => setProfileDraft((d) => ({ ...d, thinking_mode: next }))}
                  />
                </Field>
                <Field label="模型族">
                  <GlassSelect
                    ariaLabel="模型族"
                    value={profileDraft.model_family || ""}
                    options={FAMILY_OPTIONS}
                    onChange={(next) => setProfileDraft((d) => ({ ...d, model_family: next }))}
                  />
                </Field>
              </div>
              <Field label="自定义请求体 (JSON)">
                <textarea
                  value={profileDraft.custom_extra_body || ""}
                  onChange={(e) => setProfileDraft((d) => ({ ...d, custom_extra_body: e.target.value }))}
                  placeholder='{"temperature": 0.7}'
                  rows={2}
                  className="w-full text-xs rounded-lg border border-input bg-background px-2.5 py-2 font-mono focus:outline-none focus:ring-1 focus:ring-ring resize-y min-h-[2.5rem]"
                />
              </Field>
              <Field label="自定义请求头 (JSON)">
                <textarea
                  value={profileDraft.custom_extra_headers || ""}
                  onChange={(e) => setProfileDraft((d) => ({ ...d, custom_extra_headers: e.target.value }))}
                  placeholder='{"x-custom-auth": "xxx"}'
                  rows={2}
                  className="w-full text-xs rounded-lg border border-input bg-background px-2.5 py-2 font-mono focus:outline-none focus:ring-1 focus:ring-ring resize-y min-h-[2.5rem]"
                />
              </Field>
            </div>
          )}
        </div>

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
          size="sm"
          variant="ghost"
          className="h-9 text-xs gap-1"
          onClick={resetProfileFormUi}
        >
          <X className="h-3.5 w-3.5" /> 取消
        </Button>
        <div className="flex gap-2 ml-auto">
          <Button
            size="sm"
            variant="outline"
            className="h-9 text-xs gap-1"
            disabled={!profileDraft.model || testingKey === "_profile_form"}
            onClick={() => handleTestConnection("_profile_form", {
              name: credentialName,
              model: profileDraft.model,
              base_url: profileDraft.base_url || undefined,
              api_key: profileDraft.api_key || undefined,
            })}
          >
            {testingKey === "_profile_form" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Wifi className="h-3.5 w-3.5" />}
            {testingKey === "_profile_form" ? "测试中..." : "连通测试"}
          </Button>
          <Button
            size="sm"
            className="h-9 text-xs gap-1 text-white"
            style={{ backgroundColor: "var(--em-primary)" }}
            disabled={!canSubmit}
            onClick={() =>
              editingProfile
                ? handleUpdateProfile(editingProfile)
                : handleAddProfile()
            }
          >
            {addingProfile ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Save className="h-3.5 w-3.5" />}
            {addingProfile ? "添加中..." : editingProfile ? "更新" : addingSibling ? "添加模型" : "添加"}
          </Button>
        </div>
      </div>
    </div>
  );
}
