"use client";

import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import {
  Activity, Brain, Check, ChevronRight, Image as ImageIcon, KeyRound, Link2, Loader2, Lock,
  Plus, RotateCcw, Save, Search, Settings2, ShieldAlert, Trash2, Wrench, X, CheckCircle2, Zap,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";
import { useAdminModel } from "./admin-model-context";
import {
  isSubscriptionProfile,
  profileInputModalities,
  profileVisionMode,
  supportsFastMode,
  uniqueSiblingProfileName,
} from "./helpers";
import type { ModelModality } from "@/lib/model-config";
import { THINKING_EFFORT_LEVELS, type ThinkingEffort } from "@/lib/thinking";
import { CapabilityToggleRow } from "./capability-widgets";
import { Field, FIELD_CLASS, GlassSelect, ScopeTag, StatusCallout } from "./form-widgets";
import { modelDocumentation, APP_INPUT_MODALITIES } from "@/lib/model-catalog";
import { ModelIdCombobox } from "./ModelIdCombobox";
import { requestModelSubTab } from "./model-subtab";

const THINKING_OPTIONS = [
  { value: "auto", label: "自动匹配（推荐）" },
  { value: "disabled", label: "关闭思考" },
  { value: "claude", label: "Claude 原生思考" },
  { value: "claude_compat", label: "Claude 经 OpenAI 兼容代理" },
  { value: "gemini", label: "Gemini 思考用量" },
  { value: "gemini_level", label: "Gemini 思考等级" },
  { value: "enable_thinking", label: "通义 / 硅基流动思考开关" },
  { value: "chat_template", label: "本地部署（vLLM / SGLang）" },
  { value: "glm_thinking", label: "DeepSeek / GLM 思考开关" },
  { value: "openai_reasoning", label: "OpenAI / WorkBuddy 推理深度" },
  { value: "openrouter", label: "OpenRouter 推理参数" },
  { value: "deepseek", label: "DeepSeek 自动推理" },
  { value: "reasoning_content_auto", label: "兼容接口自动推理" },
];

const SERVICE_TIER_OPTIONS = [
  { value: "", label: "标准（默认）" },
  { value: "fast", label: "快速（Fast）" },
];

const FAMILY_OPTIONS = [
  { value: "", label: "按模型名称自动判断" },
  { value: "claude", label: "Claude" },
  { value: "gpt", label: "GPT" },
  { value: "gemini", label: "Gemini" },
  { value: "deepseek", label: "DeepSeek" },
  { value: "qwen", label: "Qwen（通义千问）" },
  { value: "glm", label: "GLM（智谱）" },
  { value: "grok", label: "Grok" },
];

/** 输入模态的稳定排序；图片由「图片输入」能力行负责，这里只列其余输入。 */
const MODALITY_ORDER: ModelModality[] = ["text", "image", "video", "audio"];

type CapOverrideField = "supports_tool_calling" | "supports_thinking";

function FormStep({
  number,
  title,
  hint,
  action,
  children,
}: {
  number: number;
  title: string;
  hint?: string;
  action?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="px-3.5 py-3">
      <div className="flex items-start gap-2">
        <span
          className="mt-0.5 inline-flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-[10px] font-semibold"
          style={{ backgroundColor: "var(--em-primary-alpha-12)", color: "var(--em-primary)" }}
          aria-hidden
        >
          {number}
        </span>
        <div className="min-w-0 flex-1">
          <h4 className="text-xs font-semibold leading-none">{title}</h4>
          {hint ? <p className="mt-1 text-[11px] leading-relaxed text-muted-foreground">{hint}</p> : null}
        </div>
        {action ? <div className="shrink-0">{action}</div> : null}
      </div>
      <div className="mt-2.5 pl-7">{children}</div>
    </section>
  );
}

/**
 * 模型的统一配置表单：身份 → 能力与输入 → 思考 → 长度与请求。
 * 模型参数、能力探测与思考等级在同一份表单里一次保存，不再分卡配置。
 */
export function ModelConfigForm() {
  const {
    config,
    editingProfile,
    siblingSourceName,
    profileDraft,
    setProfileDraft,
    addingProfile,
    profileBusy,
    profileError,
    setProfileError,
    modelDropdownRef,
    fetchingModels,
    remoteModels,
    modelDropdownTarget,
    setModelDropdownTarget,
    remoteModelError,
    remoteModelHint,
    setRemoteModelError,
    setRemoteModelHint,
    handleFetchRemoteModels,
    handleAddProfile,
    handleUpdateProfile,
    handleDeleteProfile,
    deletingProfile,
    handleSaveThinking,
    thinkingEffort,
    thinkingEffortOptions,
    setThinkingEffortOptions,
    thinkingBudget,
    setThinkingBudget,
    thinkingEffectiveBudget,
    thinkingSaving,
    capsMap,
    probingKey,
    handleProbeOne,
    handleCapToggle,
    beginEditProfile,
    resetProfileFormUi,
  } = useAdminModel();

  // 手动能力声明先记在表单里，随「保存全部配置」一次写入，避免表单中途产生隐性提交。
  const [capDraft, setCapDraft] = useState<Partial<Record<CapOverrideField, boolean>>>({});
  // 思考等级 / 预算是全局配置：仅当用户在本表单里改动过才写入。
  const thinkingDirtyRef = useRef(false);

  const isEdit = Boolean(editingProfile);
  const addingSibling = Boolean(siblingSourceName && !editingProfile);
  const savedProfile = useMemo(
    () => (config?.profiles || []).find((profile) => profile.name === editingProfile),
    [config?.profiles, editingProfile],
  );
  const subscription = Boolean(savedProfile && isSubscriptionProfile(savedProfile));
  const siblingSource = useMemo(
    () => (config?.profiles || []).find((profile) => profile.name === siblingSourceName),
    [config?.profiles, siblingSourceName],
  );
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

  const listOpen = modelDropdownTarget === "_profile" && remoteModels.length > 0;
  const documentation = modelDocumentation(profileDraft.model);
  const contextTokens = profileDraft.max_context_tokens ?? 0;
  const validContext = Number.isInteger(contextTokens) && contextTokens >= 0 && contextTokens <= 2147483647;
  const outputTokens = profileDraft.max_output_tokens ?? 0;
  const validOutput = Number.isInteger(outputTokens) && outputTokens >= 0 && outputTokens <= 2147483647;
  const modalities = profileInputModalities(profileDraft);
  const visionMode = profileVisionMode(profileDraft);
  const activeCaps = editingProfile ? capsMap[editingProfile] : undefined;
  const capabilityTargetReady = Boolean(editingProfile && profileDraft.model.trim() && profileDraft.base_url.trim());
  const fastSupported = !subscription && supportsFastMode({
    ...profileDraft,
    // 切换 Model ID 后，已保存的规范名不再代表当前草稿。
    canonical_model: savedProfile && savedProfile.model !== profileDraft.model
      ? ""
      : profileDraft.canonical_model,
  });
  const currentEffortLabel = THINKING_EFFORT_LEVELS.find(({ key }) => key === thinkingEffort)?.label ?? "中";
  const canSubmit = Boolean(profileDraft.model.trim())
    && (Boolean(profileDraft.name.trim()) || addingSibling)
    && validContext
    && validOutput
    && !addingProfile;

  // 切换到另一个模型（或重新载入）时，丢弃上一个模型未保存的能力声明。
  useEffect(() => {
    setCapDraft({});
    thinkingDirtyRef.current = false;
  }, [editingProfile, siblingSourceName]);

  const capValue = (field: CapOverrideField): boolean | null =>
    field in capDraft ? capDraft[field]! : activeCaps?.[field] ?? null;
  const capEvidence = (field: CapOverrideField): string | undefined =>
    field in capDraft ? "user_override" : activeCaps?.evidence?.[field];
  const capError = (field: CapOverrideField): string | undefined =>
    field in capDraft ? undefined : activeCaps?.probe_errors?.[field === "supports_tool_calling" ? "tool_calling" : "thinking"];

  const toggleCapability = (field: CapOverrideField, value: boolean) => {
    setCapDraft((prev) => ({ ...prev, [field]: value }));
  };

  const setModality = (value: ModelModality, selected: boolean) => {
    setProfileDraft((draft) => {
      const current = profileInputModalities(draft);
      return {
        ...draft,
        input_modalities: MODALITY_ORDER.filter((item) => (item === value ? selected : current.includes(item))),
      };
    });
  };

  const toggleEffortOption = (level: ThinkingEffort) => {
    thinkingDirtyRef.current = true;
    if (thinkingEffortOptions.includes(level)) {
      if (thinkingEffortOptions.length === 1) return;
      setThinkingEffortOptions(thinkingEffortOptions.filter((item) => item !== level));
      return;
    }
    const selected = new Set([...thinkingEffortOptions, level]);
    setThinkingEffortOptions(THINKING_EFFORT_LEVELS.map(({ key }) => key).filter((key) => selected.has(key)));
  };

  const resetForm = () => {
    setProfileError(null);
    if (isEdit && savedProfile) {
      beginEditProfile(savedProfile);
    } else {
      // 取消添加：回到来源模型的配置表单。
      resetProfileFormUi();
    }
  };

  const handleSubmit = async () => {
    if (!canSubmit) return;
    if (thinkingDirtyRef.current) {
      const thinkingOk = await handleSaveThinking(thinkingEffortOptions, thinkingBudget);
      if (!thinkingOk) return;
      thinkingDirtyRef.current = false;
    }
    const pendingCaps = Object.entries(capDraft) as [CapOverrideField, boolean][];
    const savedName = editingProfile
      ? await handleUpdateProfile(editingProfile, { keepEditing: true })
      : await handleAddProfile({ keepEditing: true });
    if (!savedName) return;
    for (const [field, value] of pendingCaps) {
      await handleCapToggle(savedName, profileDraft.model.trim(), profileDraft.base_url, field, value);
    }
    if (pendingCaps.length) setCapDraft({});
  };

  return (
    <form
      className="min-w-0 bg-background"
      onSubmit={(event) => { event.preventDefault(); void handleSubmit(); }}
    >
      <fieldset disabled={addingProfile || profileBusy} className="min-w-0">
        <div className="divide-y divide-border/50">
          {/* ① 模型身份：先确认这是哪个模型，其余配置都挂在它下面。 */}
          <FormStep number={1} title="模型身份" hint="先确定发往上游的模型和它的称呼；连接凭证与接口地址在「模型连接」中维护。">
            <div className="space-y-3">
              <div className="relative" ref={modelDropdownRef}>
                {isEdit ? (
                  <Field
                    label="Model ID"
                    hint="Model ID 决定发往上游的路由；更换模型请在右上角「添加模型」新建配置。"
                    extra={subscription ? (
                      <span className="inline-flex items-center gap-1 rounded-full px-1.5 py-0.5 text-[10px] font-medium bg-[var(--em-primary-alpha-12)] text-[var(--em-primary)]">
                        <Lock className="h-2.5 w-2.5" /> OAuth 订阅
                      </span>
                    ) : null}
                  >
                    <Input aria-label="Model ID" value={profileDraft.model} className={`${FIELD_CLASS} font-mono`} disabled />
                  </Field>
                ) : (
                  <Field
                    label="Model ID"
                    required
                    hint={codexSibling ? "从此订阅账号的可用列表中选择要加入的模型。" : "沿用来源连接的地址与凭证，选择或填写要加入的模型 ID。"}
                    extra={remoteModels.length > 0 ? (
                      <span className="text-[10px] font-medium text-[var(--em-primary)]">已检测到 {remoteModels.length} 个</span>
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
                          name: draft.name.trim()
                            || (codexSibling && !existingNames.includes(model.id)
                              ? model.id
                              : uniqueSiblingProfileName(model.id, existingNames)),
                          description: codexSibling && !draft.description.trim() && model.owned_by
                            ? `${model.owned_by} — OAuth 登录（无需 API Key）`
                            : draft.description,
                        }))}
                      />
                      <Button
                        type="button"
                        size="sm"
                        variant="outline"
                        className="h-9 px-2.5 shrink-0 gap-1 text-[11px]"
                        style={{ color: "var(--em-primary)", borderColor: "color-mix(in srgb, var(--em-primary) 35%, transparent)" }}
                        title="从 API 自动检测可用模型"
                        disabled={fetchingModels || codexSibling}
                        onClick={() => handleFetchRemoteModels(
                          "_profile",
                          profileDraft.base_url || undefined,
                          undefined,
                          profileDraft.protocol || undefined,
                          siblingSourceName || undefined,
                        )}
                      >
                        {fetchingModels ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Search className="h-3.5 w-3.5" />}
                        <span className="hidden min-[380px]:inline">{fetchingModels ? "检测中" : "检测模型"}</span>
                      </Button>
                    </div>
                  </Field>
                )}
                {profileDraft.canonical_model ? (
                  <div className="mt-1.5 flex items-center gap-1.5 flex-wrap">
                    <span
                      className="inline-flex items-center gap-1 rounded-full px-1.5 py-0.5 text-[10px] font-medium font-mono bg-[var(--em-primary-alpha-12)] text-[var(--em-primary)]"
                      title="已绑定到内置已知模型，仅用作名称提示；实际能力按端点探测与有来源的精确型号记录判断；不会改写发给上游的 Model ID"
                    >
                      <CheckCircle2 className="h-2.5 w-2.5" />
                      已匹配 {profileDraft.canonical_model}
                    </span>
                    <button
                      type="button"
                      className="text-[10px] text-muted-foreground hover:text-foreground underline underline-offset-2"
                      onClick={() => setProfileDraft((d) => ({ ...d, canonical_model: "" }))}
                    >
                      解除绑定
                    </button>
                  </div>
                ) : config?.canonical_match_enabled !== false ? (
                  <p className="mt-1.5 text-[10px] text-muted-foreground leading-relaxed">
                    智能匹配已开启：保存后自动把 Model ID 匹配到已知模型名；未知别名不会自动继承模型窗口或已探测能力。
                  </p>
                ) : null}
                {remoteModelError ? (
                  <div className="mt-2">
                    <StatusCallout
                      tone="error"
                      icon={<ShieldAlert className="h-3.5 w-3.5" />}
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

              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                <Field label="模型名称" required={!addingSibling}>
                  <Input
                    aria-label="模型名称"
                    value={profileDraft.name}
                    onChange={(e) => setProfileDraft((d) => ({ ...d, name: e.target.value }))}
                    className={FIELD_CLASS}
                    placeholder={addingSibling ? "选择模型后自动填写" : "例如：Claude 官方"}
                  />
                </Field>
                <Field label="备注">
                  <Input
                    aria-label="备注"
                    value={profileDraft.description}
                    onChange={(e) => setProfileDraft((d) => ({ ...d, description: e.target.value }))}
                    className={FIELD_CLASS}
                    placeholder="例如：公司专用账号"
                  />
                </Field>
              </div>

              <Field label="模型族" hint="仅在代理使用自定义模型名、自动识别不准确时指定。">
                <GlassSelect
                  ariaLabel="模型族"
                  value={profileDraft.model_family || ""}
                  options={FAMILY_OPTIONS}
                  onChange={(next) => setProfileDraft((d) => ({ ...d, model_family: next }))}
                />
              </Field>

              <div className="flex flex-wrap items-center gap-x-2 gap-y-1 rounded-lg border border-border/60 bg-muted/25 px-3 py-2 text-[11px] text-muted-foreground">
                <Link2 className="h-3 w-3 shrink-0" />
                <span className="truncate">
                  连接：{profileDraft.base_url || "订阅账号授权"}
                  {!subscription && profileDraft.protocol && profileDraft.protocol !== "auto" ? ` · ${profileDraft.protocol}` : ""}
                </span>
                <button
                  type="button"
                  className="shrink-0 underline underline-offset-2 hover:text-foreground"
                  onClick={() => requestModelSubTab(subscription ? "subscription" : "providers")}
                >
                  {subscription ? "管理订阅账号" : "在模型连接中修改"}
                </button>
              </div>
            </div>
          </FormStep>

          {/* ② 能力与输入：一次探测，实测结果、手动声明和输入模态在同处确认。 */}
          <FormStep
            number={2}
            title="能力与输入"
            hint="探测一次得到实测结果；手动声明与输入设置在同一处调整，随保存一起写入。"
            action={
              <Button
                type="button"
                size="sm"
                variant="outline"
                className="h-7 shrink-0 gap-1 text-[11px]"
                disabled={!capabilityTargetReady || probingKey === editingProfile}
                onClick={() => editingProfile && void handleProbeOne(editingProfile)}
              >
                {probingKey === editingProfile ? <Loader2 className="h-3 w-3 animate-spin" /> : <Activity className="h-3 w-3" />}
                {probingKey === editingProfile ? "探测中..." : "探测当前模型"}
              </Button>
            }
          >
            <div className="space-y-1">
              <CapabilityToggleRow
                icon={<ImageIcon className="h-3.5 w-3.5" />}
                label="图片输入（Vision）"
                desc="模型是否接收图片输入；切换会写入输入模态，与实测结果共用一处记录"
                value={visionMode === "true" ? true : visionMode === "false" ? false : activeCaps?.supports_vision ?? null}
                evidence={visionMode === "auto" ? activeCaps?.evidence?.supports_vision : "user_override"}
                error={visionMode === "auto" ? activeCaps?.probe_errors?.vision : undefined}
                hint={visionMode === "auto" ? "当前按探测/自动识别" : "手动设置"}
                onToggle={(value) => setModality("image", value)}
              />
              <CapabilityToggleRow
                icon={<Wrench className="h-3.5 w-3.5" />}
                label="工具调用闭环"
                desc="验证生成合法调用并使用工具返回值完成第二轮；不代表所有工具模式均支持"
                value={capValue("supports_tool_calling")}
                evidence={capEvidence("supports_tool_calling")}
                error={capError("supports_tool_calling")}
                onToggle={(value) => toggleCapability("supports_tool_calling", value)}
              />
              <CapabilityToggleRow
                icon={<Brain className="h-3.5 w-3.5" />}
                label="推理摘要输出"
                desc={activeCaps?.thinking_type ? `类型: ${activeCaps.thinking_type}` : "仅记录是否观察到推理摘要；未观察到不代表没有内部推理"}
                value={capValue("supports_thinking")}
                evidence={capEvidence("supports_thinking")}
                error={capError("supports_thinking")}
                onToggle={(value) => toggleCapability("supports_thinking", value)}
              />
            </div>

            <div className="mt-3 space-y-1.5">
              <div className="flex flex-wrap items-center gap-1.5">
                <span className="text-[11px] font-medium text-foreground/80">其他输入</span>
                {(["text", "video", "audio"] as const).map((modality) => {
                  const label = modality === "text" ? "文本" : modality === "video" ? "视频" : "音频";
                  const checked = modalities.includes(modality);
                  return (
                    <label
                      key={modality}
                      className={cn(
                        "inline-flex cursor-pointer items-center gap-1.5 rounded-full border px-2.5 py-1 text-[11px]",
                        modality === "text"
                          ? "cursor-not-allowed border-border/60 bg-muted/40 text-muted-foreground"
                          : "border-input hover:bg-muted/40",
                      )}
                    >
                      <Checkbox
                        checked={checked}
                        disabled={modality === "text"}
                        aria-label={label}
                        onCheckedChange={(next) => setModality(modality, next === true)}
                      />
                      {label}
                      {modality !== "text" && !APP_INPUT_MODALITIES.includes(modality) ? "（尚未接通）" : ""}
                    </label>
                  );
                })}
                <span className="text-[10px] text-muted-foreground">
                  {profileDraft.input_modalities == null ? "当前使用自动识别" : "已手动设置"}
                </span>
                {profileDraft.input_modalities != null && (
                  <button
                    type="button"
                    className="text-[10px] text-muted-foreground hover:text-foreground underline underline-offset-2"
                    onClick={() => setProfileDraft((draft) => ({ ...draft, input_modalities: null, vision_mode: "auto" }))}
                  >
                    恢复自动
                  </button>
                )}
              </div>
              <p className="text-[10px] text-muted-foreground leading-relaxed">
                声明不代表实测。当前客户端只接通文本和图片，音视频不会作为消息输入。
              </p>
            </div>

            {activeCaps?.detected_at ? (
              <p className="mt-2 text-[10px] text-muted-foreground">
                最近探测：{new Date(activeCaps.detected_at).toLocaleString()}
                {activeCaps.manual_override ? " · 含手动声明" : ""}
              </p>
            ) : (
              <p className="mt-2 text-[10px] text-muted-foreground">
                {isEdit ? "尚未探测当前模型；可点击上方「探测当前模型」获取实测结果。" : "保存后即可探测此模型的实际能力。"}
              </p>
            )}

            <details className="group mt-2 text-xs" aria-label="模型能力来源">
              <summary className="flex items-center gap-1.5 cursor-pointer list-none text-muted-foreground hover:text-foreground [&::-webkit-details-marker]:hidden">
                <ChevronRight className="h-3 w-3 shrink-0 transition-transform group-open:rotate-90" />
                <span className="flex-1 truncate">
                  {documentation ? (documentation.match_kind === "alias" ? "已匹配官方型号资料" : "官方文档能力") : "尚无已核实的型号资料"}
                </span>
                <span className="text-[10px] truncate max-w-[55%]">
                  {documentation
                    ? `${documentation.id} · ${documentation.context_window?.toLocaleString() ?? "未知"} tokens · ${documentation.verified_at}`
                    : "点击查看配置提示"}
                </span>
              </summary>
              <div className="mt-1.5 space-y-1 rounded-lg border border-border/60 bg-muted/20 px-3 py-2">
                {documentation ? <>
                  {documentation.match_kind === "alias" && <p className="text-amber-700">当前 ID 是平台/本地变体，资料对应 `{documentation.id}`。</p>}
                  <p className="text-muted-foreground">{documentation.context_window?.toLocaleString() ?? "未知"} tokens · {documentation.input_modalities.join(" / ")} · 工具调用 {documentation.tool_calling === true ? "官方支持" : documentation.tool_calling === false ? "官方不支持" : "未知"}</p>
                  <p className="text-muted-foreground">思考：{documentation.reasoning.supported === true ? (documentation.reasoning.can_disable === true ? "可开启/关闭" : documentation.reasoning.can_disable === false ? "不支持关闭" : "关闭方式未核实") : documentation.reasoning.supported === false ? "无专用推理控制" : "未知"}{documentation.reasoning.efforts.length ? ` · 档位 ${documentation.reasoning.efforts.join(" / ")}` : ""}</p>
                  <p>核验：{documentation.verified_at} · 账户与端点仍需实测</p>
                  {documentation.notes && <p className="text-muted-foreground">{documentation.notes}</p>}
                  {documentation.source_urls.map((url) => <a className="block truncate underline" key={url} href={url} target="_blank" rel="noreferrer">{url}</a>)}
                </> : <p className="text-muted-foreground">可手动配置并探测；默认预算不是模型官方上限。</p>}
              </div>
            </details>
          </FormStep>

          {/* ③ 思考配置：思考如何传给接口，以及聊天区可选的思考档位。 */}
          <FormStep number={3} title="思考配置" hint="先决定此模型如何思考，再选择聊天区可用的思考档位与预算。">
            <div className="space-y-3">
              <Field
                label="思考模式"
                extra={<ScopeTag>此模型</ScopeTag>}
                hint="控制接口如何传递思考参数；通常保持自动。实际思考深度在聊天输入框中选择。"
              >
                <GlassSelect
                  ariaLabel="思考模式"
                  value={profileDraft.thinking_mode || "auto"}
                  options={THINKING_OPTIONS}
                  onChange={(next) => setProfileDraft((d) => ({ ...d, thinking_mode: next }))}
                />
              </Field>

              <div>
                <div className="mb-1.5 flex items-center justify-between gap-2">
                  <span className="inline-flex items-center gap-1.5 text-[11px] font-medium text-foreground/80">
                    可用思考等级（多选）
                    <ScopeTag>全局</ScopeTag>
                  </span>
                  <span className="text-[11px] text-muted-foreground">当前：{currentEffortLabel}</span>
                </div>
                <div className="em-thinking-options" aria-label="可调推理等级">
                  {THINKING_EFFORT_LEVELS.map(({ key, label }) => {
                    const isActive = thinkingEffortOptions.includes(key);
                    return (
                      <button
                        key={key}
                        type="button"
                        aria-pressed={isActive}
                        className={`inline-flex items-center justify-center gap-1 whitespace-nowrap rounded-md border px-2.5 py-2 text-xs font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-60 ${isActive ? "border-transparent text-white" : "border-border text-muted-foreground hover:bg-muted/60"}`}
                        style={isActive ? { backgroundColor: "var(--em-primary)" } : undefined}
                        onClick={() => toggleEffortOption(key)}
                        disabled={thinkingSaving || (isActive && thinkingEffortOptions.length === 1)}
                      >
                        {isActive && <Check className="h-3 w-3 shrink-0" aria-hidden />}
                        {label}
                      </button>
                    );
                  })}
                </div>
              </div>

              <Field
                label="固定思考用量（token）"
                extra={<ScopeTag>全局</ScopeTag>}
                hint="留空按等级自动选择。"
              >
                <Input
                  aria-label="固定思考用量"
                  value={thinkingBudget}
                  onChange={(event) => {
                    thinkingDirtyRef.current = true;
                    setThinkingBudget(event.target.value.replace(/\D/g, ""));
                  }}
                  className={`${FIELD_CLASS} font-mono sm:w-40`}
                  placeholder="自动"
                  inputMode="numeric"
                />
                {thinkingEffectiveBudget > 0 && (
                  <p className="mt-1 text-[10px] text-muted-foreground">当前生效预算: {thinkingEffectiveBudget.toLocaleString()} tokens</p>
                )}
              </Field>
            </div>
          </FormStep>

          {/* ④ 长度与请求：最常保留默认值的细节，放在最后按需覆盖。 */}
          <FormStep number={4} title="长度与请求" hint="按需覆盖上下文、输出长度和请求细节；留空即使用默认值。">
            <div className="space-y-3">
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                <Field label="上下文上限（tokens）" hint="留空或填 0 使用自动 / 全局设置。">
                  <Input
                    aria-label="上下文上限（tokens）"
                    type="number"
                    min={0}
                    max={2147483647}
                    step={1}
                    value={contextTokens || ""}
                    placeholder="自动"
                    aria-invalid={!validContext || undefined}
                    className={FIELD_CLASS}
                    onChange={(event) => setProfileDraft((draft) => ({ ...draft, max_context_tokens: Number(event.target.value) }))}
                  />
                  {!validContext && <p role="alert" className="text-[11px] text-destructive">请输入 0 到 2147483647 之间的整数。</p>}
                </Field>
                <Field
                  label="最大输出长度（tokens）"
                  hint={profileDraft.model.startsWith("antigravity/") && !profileDraft.model.includes("claude")
                    ? "此值会保存；Antigravity 的非 Claude 模型由服务端决定输出长度。"
                    : "留空或填 0 使用默认值；正整数限制单次模型输出，包含推理 token。"}
                >
                  <Input
                    aria-label="最大输出长度（tokens）"
                    type="number"
                    min={0}
                    max={2147483647}
                    step={1}
                    value={outputTokens || ""}
                    placeholder="默认"
                    aria-invalid={!validOutput || undefined}
                    className={FIELD_CLASS}
                    onChange={(event) => setProfileDraft((draft) => ({ ...draft, max_output_tokens: Number(event.target.value) }))}
                  />
                  {!validOutput && <p role="alert" className="text-[11px] text-destructive">请输入 0 到 2147483647 之间的整数。</p>}
                </Field>
              </div>

              {fastSupported && (
                <Field
                  label="响应速度"
                  extra={<ScopeTag>此模型</ScopeTag>}
                  hint="支持 Fast 的 GPT 模型会使用快速处理；可能产生更高费用。"
                >
                  <GlassSelect
                    ariaLabel="响应速度"
                    value={profileDraft.service_tier || ""}
                    options={SERVICE_TIER_OPTIONS}
                    onChange={(next) => setProfileDraft((d) => ({ ...d, service_tier: next as "" | "fast" }))}
                  />
                </Field>
              )}

              <Field label="自定义请求体 (JSON)">
                <Textarea
                  aria-label="自定义请求体"
                  value={profileDraft.custom_extra_body || ""}
                  onChange={(e) => setProfileDraft((d) => ({ ...d, custom_extra_body: e.target.value }))}
                  placeholder='{"temperature": 0.7}'
                  rows={2}
                  className="min-h-[2.5rem] resize-y rounded-lg px-2.5 py-2 text-xs font-mono"
                />
              </Field>
              <Field label="自定义请求头 (JSON)">
                <Textarea
                  aria-label="自定义请求头"
                  value={profileDraft.custom_extra_headers || ""}
                  onChange={(e) => setProfileDraft((d) => ({ ...d, custom_extra_headers: e.target.value }))}
                  placeholder='{"x-custom-auth": "xxx"}'
                  rows={2}
                  className="min-h-[2.5rem] resize-y rounded-lg px-2.5 py-2 text-xs font-mono"
                />
              </Field>
            </div>
          </FormStep>
        </div>

        {profileError ? (
          <div className="px-3.5 pb-3">
            <StatusCallout
              tone="error"
              icon={<ShieldAlert className="h-3.5 w-3.5" />}
              title={profileError}
              onDismiss={() => setProfileError(null)}
            />
          </div>
        ) : null}
      </fieldset>

      <div className="sticky bottom-0 z-10 flex items-center gap-2 border-t border-border/50 bg-muted/25 px-3.5 py-2.5 backdrop-blur-lg pb-[max(0.625rem,env(safe-area-inset-bottom))] sm:relative sm:backdrop-blur-none sm:pb-2.5">
        {isEdit ? (
          <Button
            type="button"
            size="sm"
            variant="ghost"
            className="h-9 gap-1 text-xs text-muted-foreground hover:text-destructive"
            disabled={profileBusy || deletingProfile === editingProfile}
            onClick={() => editingProfile && void handleDeleteProfile(editingProfile)}
          >
            {deletingProfile === editingProfile ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Trash2 className="h-3.5 w-3.5" />}
            删除
          </Button>
        ) : null}
        <Button
          type="button"
          size="sm"
          variant="ghost"
          className="h-9 text-xs gap-1"
          onClick={resetForm}
          disabled={addingProfile}
        >
          {isEdit ? <><RotateCcw className="h-3.5 w-3.5" /> 还原</> : <><X className="h-3.5 w-3.5" /> 取消</>}
        </Button>
        <Button
          type="submit"
          size="sm"
          className="ml-auto h-9 gap-1 text-xs text-white"
          style={{ backgroundColor: "var(--em-primary)" }}
          disabled={!canSubmit}
        >
          {addingProfile ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Save className="h-3.5 w-3.5" />}
          {addingProfile ? "保存中..." : isEdit ? "保存全部配置" : "添加并配置"}
        </Button>
      </div>
    </form>
  );
}
