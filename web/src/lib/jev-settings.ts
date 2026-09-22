export type JevGate = "off" | "enforce";
export type JevProtocol = "typesafe" | "gateway";

export const JEV_GATE_OPTIONS: { value: JevGate; label: string }[] = [
  { value: "off", label: "关闭" },
  { value: "enforce", label: "生效" },
];

export type JevCatalogModel = {
  id: string;
  label: string;
  hint: string;
};

export const JEV_DEFAULT_MODEL = "jev-1.13.0";
export const JEV_GATEWAY_MODEL = "typesafe-ai/jev";

export const JEV_MODELS: JevCatalogModel[] = [
  { id: "jev-1.13.0", label: "Jev 1.13.0", hint: "钉版本（推荐）" },
  { id: "jev-latest", label: "Jev Latest", hint: "跟随最新稳定" },
  { id: "jev-preview", label: "Jev Preview", hint: "预览构建" },
];

export const JEV_GATEWAY_MODELS: JevCatalogModel[] = [
  { id: JEV_GATEWAY_MODEL, label: "Jev", hint: "Vercel AI Gateway" },
];

export type JevProviderPreset = {
  id: string;
  label: string;
  protocol: JevProtocol;
  base_url: string;
  model: string;
  purchaseUrl: string;
  models: JevCatalogModel[];
};

export const JEV_PROVIDER_PRESETS: JevProviderPreset[] = [
  {
    id: "typesafe",
    label: "TypeSafe",
    protocol: "typesafe",
    base_url: "https://api.typesafe.ai",
    model: JEV_DEFAULT_MODEL,
    purchaseUrl: "https://console.typesafe.ai/settings/keys",
    models: JEV_MODELS,
  },
  {
    id: "vercel",
    label: "Vercel",
    protocol: "gateway",
    base_url: "https://ai-gateway.vercel.sh/v4/ai/evaluation-model",
    model: JEV_GATEWAY_MODEL,
    purchaseUrl: "https://vercel.com/docs/ai-gateway",
    models: JEV_GATEWAY_MODELS,
  },
];

export type JevProviderPublic = {
  id: string;
  name: string;
  protocol: JevProtocol;
  base_url: string;
  model: string;
  configured: boolean;
  last4: string;
};

export type JevProviderDraft = {
  id: string;
  name: string;
  protocol: JevProtocol;
  base_url: string;
  model: string;
  api_key: string;
};

export const EMPTY_JEV_PROVIDER_DRAFT: JevProviderDraft = {
  id: "",
  name: "",
  protocol: "typesafe",
  base_url: "",
  model: "",
  api_key: "",
};

export function validateJevProvider(draft: JevProviderDraft): string | null {
  if (!draft.name.trim()) return "请填写供应商名称";
  if (!draft.model.trim()) return "请填写模型 ID";
  try {
    const url = new URL(draft.base_url.trim());
    if (!["https:", "http:"].includes(url.protocol) || !url.hostname || url.username || url.password || url.search || url.hash) return "请填写有效的 HTTP 或 HTTPS 服务地址，不包含密钥、查询参数或片段";
  } catch {
    return "请填写完整的服务地址，例如 https://api.typesafe.ai";
  }
  return null;
}

export function jevPresetById(id: string): JevProviderPreset | undefined {
  return JEV_PROVIDER_PRESETS.find((item) => item.id === id);
}

export function draftFromJevPreset(preset: JevProviderPreset): JevProviderDraft {
  return {
    id: preset.id,
    name: preset.label,
    protocol: preset.protocol,
    base_url: preset.base_url,
    model: preset.model,
    api_key: "",
  };
}

export function draftFromJevProvider(provider: JevProviderPublic): JevProviderDraft {
  return {
    id: provider.id,
    name: provider.name,
    protocol: provider.protocol,
    base_url: provider.base_url,
    model: provider.model,
    api_key: "",
  };
}

export function newCustomJevDraft(): JevProviderDraft {
  return {
    id: `custom-${Date.now().toString(36)}`,
    name: "",
    protocol: "gateway",
    base_url: "",
    model: JEV_DEFAULT_MODEL,
    api_key: "",
  };
}

export function jevModelsForProvider(
  provider: Pick<JevProviderPublic, "id" | "protocol" | "model"> & { name?: string },
): JevCatalogModel[] {
  const preset = jevPresetById(provider.id);
  if (preset) return preset.models;
  const model = provider.model.trim() || JEV_DEFAULT_MODEL;
  return [{ id: model, label: model, hint: provider.name || "自定义" }];
}

export function resolveJevCatalogModel(model: string, providers: JevProviderPublic[] = []): JevCatalogModel {
  const id = model.trim() || JEV_DEFAULT_MODEL;
  for (const provider of providers) {
    const found = jevModelsForProvider(provider).find((item) => item.id === id);
    if (found) return found;
  }
  const catalog = [...JEV_MODELS, ...JEV_GATEWAY_MODELS].find((item) => item.id === id);
  return catalog ?? { id, label: id, hint: "自定义版本" };
}

export function jevCatalogOptions(current: string, providers: JevProviderPublic[] = []): JevCatalogModel[] {
  const seen = new Set<string>();
  const options: JevCatalogModel[] = [];
  for (const provider of providers) {
    for (const item of jevModelsForProvider(provider)) {
      if (seen.has(item.id)) continue;
      seen.add(item.id);
      options.push(item);
    }
  }
  if (options.length === 0) {
    const resolved = resolveJevCatalogModel(current, providers);
    return current.trim() ? [{ ...resolved, hint: resolved.hint || "当前版本" }] : [];
  }
  const resolved = resolveJevCatalogModel(current, providers);
  if (!seen.has(resolved.id)) options.unshift({ ...resolved, hint: "当前自定义版本" });
  return options;
}

export function jevGateLabel(value: string): string {
  const normalized = value === "shadow" ? "enforce" : value;
  return JEV_GATE_OPTIONS.find((option) => option.value === normalized)?.label ?? "关闭";
}

export function jevChatEnabled(input: {
  configured: boolean;
  enabled: string;
}): boolean {
  const enabled = input.enabled === "shadow" ? "enforce" : input.enabled;
  return Boolean(input.configured) && enabled === "enforce";
}

export function jevConfiguredFromRuntime(data: {
  jev_enabled?: string;
  jev_active_provider?: string;
  ai_gateway?: { configured?: boolean };
  typesafe?: { configured?: boolean };
  jev_providers?: { id?: string; protocol?: string; configured?: boolean }[];
}): boolean {
  // 与后端 pick_active_jev_provider 对齐：显式选中优先，其次第一个已配密钥，
  // 最后兜底列表头；激活提供商无密钥时按旧版密钥分支回落（typesafe 只看直连密钥）。
  const providers = data.jev_providers ?? [];
  const wanted = (data.jev_active_provider ?? "").trim();
  const active =
    (wanted ? providers.find((item) => item.id === wanted) : undefined) ??
    providers.find((item) => item.configured) ??
    providers[0];
  const legacy = active
    ? active.protocol === "gateway" || wanted === "vercel"
      ? Boolean(data.ai_gateway?.configured)
      : Boolean(data.typesafe?.configured)
    : Boolean(data.ai_gateway?.configured) || Boolean(data.typesafe?.configured);
  const configured = Boolean(active?.configured) || legacy;
  return configured;
}

export function jevChatEnabledFromRuntime(data: {
  jev_enabled?: string;
  jev_active_provider?: string;
  ai_gateway?: { configured?: boolean };
  typesafe?: { configured?: boolean };
  jev_providers?: { id?: string; protocol?: string; configured?: boolean }[];
}): boolean {
  return jevChatEnabled({
    configured: jevConfiguredFromRuntime(data),
    enabled: data.jev_enabled || "off",
  });
}

export type JevEntryTone = "idle" | "ready" | "enforce";

export function jevEntryStatus(input: {
  configured: boolean;
  enabled: string;
}): { tone: JevEntryTone; chip: string } {
  if (!input.configured) {
    return { tone: "idle", chip: "未配置" };
  }
  if (input.enabled === "enforce" || input.enabled === "shadow") {
    return { tone: "enforce", chip: "生效" };
  }
  return { tone: "ready", chip: "已连接 · 关闭" };
}

export function jevProviderDetail(input: {
  configured: boolean;
  last4?: string;
}): string {
  if (!input.configured) return "添加密钥后即可使用";
  return input.last4 ? `密钥 ···${input.last4}` : "密钥已保存";
}

export function jevRoleDetail(input: {
  configured: boolean;
  model: string;
  providerName?: string;
}): string {
  const model = resolveJevCatalogModel(input.model);
  const provider = input.providerName || "决策提供商";
  if (!input.configured) return `${model.label} · 先在供应商中配置密钥`;
  return `${model.label} · ${provider}`;
}

export function jevEntryDetail(input: {
  configured: boolean;
  last4?: string;
  model: string;
}): string {
  if (!input.configured) {
    return "添加密钥后即可使用";
  }
  const model = resolveJevCatalogModel(input.model).id;
  const key = input.last4 ? `密钥 ···${input.last4}` : "密钥已保存";
  return `${model} · ${key}`;
}

export type JevDraft = {
  jev_enabled: JevGate;
  jev_exposure: JevGate;
  jev_observation: JevGate;
  jev_verification: JevGate;
  jev_recovery: JevGate;
  jev_mode_hint: boolean;
  jev_ui_hint: boolean;
  jev_model: string;
  jev_timeout_seconds: number;
  jev_active_provider: string;
  ai_gateway_api_key: string;
  model_canonical_match_enabled: boolean;
};

export const EMPTY_JEV_DRAFT: JevDraft = {
  jev_enabled: "enforce",
  jev_exposure: "enforce",
  jev_observation: "enforce",
  jev_verification: "enforce",
  jev_recovery: "enforce",
  jev_mode_hint: true,
  jev_ui_hint: true,
  jev_model: JEV_DEFAULT_MODEL,
  jev_timeout_seconds: 1.5,
  jev_active_provider: "",
  ai_gateway_api_key: "",
  model_canonical_match_enabled: true,
};

export const JEV_PROVIDER_KEYS: (keyof JevDraft)[] = [
  "jev_timeout_seconds",
  "jev_active_provider",
];

export const JEV_ROLE_KEYS: (keyof JevDraft)[] = [
  "jev_model",
  "jev_active_provider",
  "jev_enabled",
  "jev_exposure",
  "jev_observation",
  "jev_verification",
  "jev_recovery",
  "jev_mode_hint",
  "jev_ui_hint",
  "model_canonical_match_enabled",
];

export function parseJevGate(value: string | undefined): JevGate {
  // Upgrade legacy persisted/runtime snapshots without exposing the removed
  // observation-only state in the UI.
  if (value === "shadow" || value === "enforce") return "enforce";
  return "off";
}

export function parseJevProtocol(value: string | undefined): JevProtocol {
  return value === "gateway" ? "gateway" : "typesafe";
}

export function buildJevPayload(
  draft: JevDraft,
  baseline: JevDraft,
  keys: readonly (keyof JevDraft)[] = Object.keys(EMPTY_JEV_DRAFT) as (keyof JevDraft)[],
): Record<string, unknown> {
  const payload: Record<string, unknown> = {};
  keys.forEach((key) => {
    if (key === "ai_gateway_api_key") {
      const next = draft.ai_gateway_api_key.trim();
      if (next) payload[key] = next;
      return;
    }
    if (draft[key] !== baseline[key]) {
      payload[key] = draft[key];
    }
  });
  return payload;
}
