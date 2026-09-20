import { formatModelIdForDisplay } from "@/lib/model-display";
import { getProviderDisplayName, PROVIDER_COLORS as PROVIDER_BRAND_COLOR } from "@/lib/provider-brand";
import type { ModelCapabilities, ProfileEntry } from "./types";

export const EMPTY_PROFILE_DRAFT: ProfileEntry = {
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
};

export interface ProviderGroup {
  id: string;
  label: string;
  color: string;
  profiles: ProfileEntry[];
}

export function withAlpha(hex: string, alphaHex: string): string {
  if (/^#[0-9a-fA-F]{6}$/.test(hex)) return `${hex}${alphaHex}`;
  return hex;
}

export function inferProfileProvider(profile: Pick<ProfileEntry, "model" | "base_url" | "model_family" | "protocol">): string | null {
  const model = (profile.model || "").toLowerCase();
  const baseUrl = (profile.base_url || "").toLowerCase();
  const family = (profile.model_family || "").toLowerCase();
  const protocol = (profile.protocol || "").toLowerCase();
  const modelPrefix = model.split("/")[0];

  if (model.startsWith("openai-codex/")) return "openai-codex";
  if (model.startsWith("workbuddy-global/") || baseUrl.includes("workbuddy.ai")) return "workbuddy-global";
  if (model.startsWith("workbuddy-cn/") || model.startsWith("workbuddy/") || baseUrl.includes("copilot.tencent.com") || baseUrl.includes("codebuddy.cn")) return "workbuddy-cn";
  // antigravity 模型 ID 内含 claude-/gemini-/gpt-oss，必须先于品牌推断命中
  if (model.startsWith("antigravity/") || protocol === "antigravity" || baseUrl.includes("cloudcode-pa.googleapis.com")) return "antigravity";
  if (baseUrl.includes("openrouter.ai") || protocol === "openrouter") return "openrouter";
  if (model.includes("grok") || baseUrl.includes("x.ai") || baseUrl.includes("xai") || family === "xai") return "xai";
  if (model.includes("moonshot") || model.includes("kimi") || baseUrl.includes("moonshot") || family === "moonshot") return "moonshot";
  if (model.includes("deepseek") || baseUrl.includes("deepseek") || family === "deepseek") return "deepseek";
  if (model.includes("mistral") || model.includes("codestral") || model.includes("pixtral") || baseUrl.includes("mistral") || family === "mistral") return "mistral";
  if (model.includes("claude") || baseUrl.includes("anthropic") || protocol === "anthropic" || family === "claude") return "anthropic";
  if (model.includes("gemini") || baseUrl.includes("generativelanguage") || baseUrl.includes("googleapis") || protocol === "gemini" || family === "gemini") return "gemini";
  if (model.includes("llama") || model.includes("meta-llama") || family === "llama" || family === "meta") return "meta";
  if (model.includes("qwen") || baseUrl.includes("dashscope") || baseUrl.includes("aliyuncs") || family === "qwen") return "qwen";
  if (baseUrl.includes("alibabacloud") || baseUrl.includes("aliyun") || family === "alibaba" || family === "aliyun") return "alibabacloud";
  if (model.includes("glm") || baseUrl.includes("bigmodel") || family === "glm" || family === "zhipu") return "zhipu";
  if (model.includes("hunyuan") || baseUrl.includes("tencent") || family === "hunyuan" || family === "tencent") return "tencent";
  if (model.includes("doubao") || baseUrl.includes("volces") || baseUrl.includes("volcengine") || baseUrl.includes("ark.cn-beijing") || family === "doubao") return "bytedance";
  if (model.includes("ernie") || baseUrl.includes("qianfan") || baseUrl.includes("baidu") || family === "baidu") return "baidu";
  if (model.includes("pangu") || baseUrl.includes("huawei") || family === "huawei") return "huawei";
  if (model.includes("pplx") || baseUrl.includes("perplexity") || family === "perplexity") return "perplexity";
  if (model.includes("nvidia") || baseUrl.includes("nvidia") || family === "nvidia") return "nvidia";
  if (model.startsWith("hf/") || model.includes("huggingface") || baseUrl.includes("huggingface") || family === "huggingface") return "huggingface";
  if (baseUrl.includes("siliconflow") || baseUrl.includes("siliconcloud") || family === "siliconflow" || family === "siliconcloud") return "siliconflow";
  if (model.includes("minimax") || baseUrl.includes("minimax") || family === "minimax") return "minimax";
  if (baseUrl.includes("openai") || family === "gpt" || model.startsWith("gpt-") || model.startsWith("o1") || model.startsWith("o3") || model.startsWith("o4") || model.includes("chatgpt")) return "openai";

  const prefixMap: Record<string, string> = {
    openai: "openai",
    anthropic: "anthropic",
    claude: "anthropic",
    google: "gemini",
    gemini: "gemini",
    deepseek: "deepseek",
    qwen: "qwen",
    zhipu: "zhipu",
    glm: "zhipu",
    moonshot: "moonshot",
    kimi: "moonshot",
    x: "xai",
    xai: "xai",
    grok: "xai",
    mistral: "mistral",
    mistralai: "mistral",
    perplexity: "perplexity",
    baidu: "baidu",
    huawei: "huawei",
    nvidia: "nvidia",
    meta: "meta",
    llama: "meta",
    tencent: "tencent",
    qq: "tencent",
    bytedance: "bytedance",
    doubao: "bytedance",
    volcengine: "bytedance",
    ark: "bytedance",
    hf: "huggingface",
    huggingface: "huggingface",
    siliconflow: "siliconflow",
    siliconcloud: "siliconflow",
    alibaba: "alibabacloud",
    aliyun: "alibabacloud",
    alibabacloud: "alibabacloud",
    minimax: "minimax",
  };
  return prefixMap[modelPrefix] || null;
}

export function getProviderBrandColor(provider: string | null): string {
  if (!provider) return "#6b7280";
  return PROVIDER_BRAND_COLOR[provider] || "#6b7280";
}

export function isCodexProfile(profile: Pick<ProfileEntry, "model">): boolean {
  return (profile.model || "").startsWith("openai-codex/");
}

export function isAntigravityProfile(profile: Pick<ProfileEntry, "model">): boolean {
  return (profile.model || "").startsWith("antigravity/");
}

/** WorkBuddy 订阅模型前缀（按 realm 拆分；末尾 "workbuddy/" 为旧版兼容）。 */
export const WORKBUDDY_MODEL_PREFIXES = ["workbuddy-cn/", "workbuddy-global/", "workbuddy/"] as const;

/** 返回该档案归属的 WorkBuddy realm 前缀，非 WorkBuddy 档案返回 null。 */
export function workbuddyProfilePrefix(profile: Pick<ProfileEntry, "model">): string | null {
  const model = profile.model || "";
  return WORKBUDDY_MODEL_PREFIXES.find((p) => model.startsWith(p)) ?? null;
}

export function isWorkBuddyProfile(profile: Pick<ProfileEntry, "model">): boolean {
  return workbuddyProfilePrefix(profile) !== null;
}

/** 订阅 OAuth 管理档案（openai-codex/、workbuddy-*、antigravity/ 等前缀模型）。 */
export function isSubscriptionProfile(profile: Pick<ProfileEntry, "model">): boolean {
  return isCodexProfile(profile) || isWorkBuddyProfile(profile) || isAntigravityProfile(profile);
}

/** 返回该档案归属的订阅 provider 模型前缀，非订阅档案返回 null。 */
export function subscriptionModelPrefix(profile: Pick<ProfileEntry, "model">): string | null {
  if (isCodexProfile(profile)) return "openai-codex/";
  if (isAntigravityProfile(profile)) return "antigravity/";
  return workbuddyProfilePrefix(profile);
}

export function isProfileConnected(profile: ProfileEntry): boolean {
  return isSubscriptionProfile(profile) || Boolean(profile.api_key);
}

const OFFICIAL_HOST_MARKERS: Record<string, string[]> = {
  openai: ["api.openai.com"],
  "openai-codex": ["api.openai.com"],
  "workbuddy-cn": ["copilot.tencent.com", "codebuddy.cn", "www.codebuddy.cn"],
  "workbuddy-global": ["workbuddy.ai", "www.workbuddy.ai"],
  antigravity: [
    "cloudcode-pa.googleapis.com",
    "daily-cloudcode-pa.googleapis.com",
    "daily-cloudcode-pa.sandbox.googleapis.com",
  ],
  anthropic: ["api.anthropic.com"],
  gemini: ["generativelanguage.googleapis.com"],
  deepseek: ["api.deepseek.com"],
  qwen: ["dashscope.aliyuncs.com"],
  alibabacloud: ["alibabacloud.com", "aliyuncs.com"],
  zhipu: ["open.bigmodel.cn"],
  openrouter: ["openrouter.ai"],
  moonshot: ["api.moonshot.cn", "api.moonshot.ai"],
  minimax: ["api.minimax.io", "api.minimax.chat"],
  xai: ["api.x.ai"],
  bytedance: ["volces.com", "volcengine.com"],
  mistral: ["api.mistral.ai"],
  meta: ["api.llama.com"],
  perplexity: ["api.perplexity.ai"],
  baidu: ["qianfan.baidubce.com", "baidubce.com"],
  tencent: ["tencentcloudapi.com"],
  siliconflow: ["api.siliconflow.cn", "api.siliconflow.com"],
  huggingface: ["api-inference.huggingface.co", "router.huggingface.co"],
  nvidia: ["integrate.api.nvidia.com"],
  huawei: ["huaweicloud.com"],
};

export function hostnameOf(url: string): string {
  try {
    return new URL(url).hostname.toLowerCase();
  } catch {
    return "";
  }
}

export function isOfficialProviderEndpoint(baseUrl: string, providerId: string): boolean {
  const host = hostnameOf(baseUrl);
  if (!host) return false;
  const markers = OFFICIAL_HOST_MARKERS[providerId];
  if (!markers) return false;
  return markers.some((marker) => host === marker || host.endsWith(`.${marker}`));
}

export function getProfileProviderId(profile: ProfileEntry): string {
  if (isCodexProfile(profile)) return "openai-codex";
  if (isAntigravityProfile(profile)) return "antigravity";
  const wbPrefix = workbuddyProfilePrefix(profile);
  if (wbPrefix) return wbPrefix === "workbuddy-global/" ? "workbuddy-global" : "workbuddy-cn";
  const inferred = inferProfileProvider(profile);
  if (inferred && (!profile.base_url.trim() || isOfficialProviderEndpoint(profile.base_url, inferred))) {
    return inferred;
  }
  const host = hostnameOf(profile.base_url);
  if (host) return `custom:${host}`;
  return `custom:${profile.name || "unnamed"}`;
}

export function getProviderGroupLabel(id: string, fallbackName?: string): string {
  if (id.startsWith("custom:")) {
    return fallbackName || id.slice("custom:".length);
  }
  return getProviderDisplayName(id);
}

export function groupProfilesByProvider(profiles: ProfileEntry[]): ProviderGroup[] {
  const map = new Map<string, ProfileEntry[]>();
  for (const profile of profiles) {
    const id = getProfileProviderId(profile);
    const list = map.get(id);
    if (list) list.push(profile);
    else map.set(id, [profile]);
  }
  return Array.from(map.entries()).map(([id, groupProfiles]) => ({
    id,
    label: getProviderGroupLabel(id, groupProfiles[0]?.name),
    color: getProviderBrandColor(id.startsWith("custom:") ? null : id),
    profiles: groupProfiles,
  }));
}

export function formatProviderModelLabel(profile: ProfileEntry): string {
  const providerId = getProfileProviderId(profile);
  const providerLabel = getProviderGroupLabel(providerId, profile.name);
  const model = formatModelIdForDisplay(profile.model) || profile.name;
  return `${providerLabel} · ${model}`;
}

export function profileToDraft(profile: ProfileEntry): ProfileEntry {
  return {
    name: profile.name,
    model: profile.model,
    api_key: "",
    base_url: profile.base_url,
    description: profile.description,
    protocol: profile.protocol || "auto",
    thinking_mode: profile.thinking_mode || "auto",
    model_family: profile.model_family || "",
    custom_extra_body: profile.custom_extra_body || "",
    custom_extra_headers: profile.custom_extra_headers || "",
  };
}

export function findProfileByModelId(
  profiles: ProfileEntry[],
  modelId: string,
): ProfileEntry | undefined {
  return profiles.find((profile) => profile.model === modelId);
}

export function pickDefaultProfile(group: ProviderGroup, activeName?: string | null): ProfileEntry | undefined {
  return group.profiles.find((profile) => profile.name === activeName) || group.profiles[0];
}

export function uniqueSiblingProfileName(modelId: string, existingNames: string[]): string {
  const taken = new Set(existingNames);
  const short = (modelId.split("/").pop() || modelId).trim();
  const candidates = [short, modelId].filter(Boolean);
  for (const candidate of candidates) {
    if (!taken.has(candidate)) return candidate;
  }
  const base = short || "model";
  let index = 2;
  while (taken.has(`${base} ${index}`)) index += 1;
  return `${base} ${index}`;
}

export function siblingDraftFromProfile(profile: ProfileEntry): ProfileEntry {
  return {
    name: "",
    model: "",
    api_key: "",
    base_url: profile.base_url,
    description: profile.description,
    protocol: profile.protocol || "auto",
    thinking_mode: profile.thinking_mode || "auto",
    model_family: profile.model_family || "",
    custom_extra_body: profile.custom_extra_body || "",
    custom_extra_headers: profile.custom_extra_headers || "",
  };
}

export function filterDetectedModels<T extends { id: string }>(models: T[], query: string): T[] {
  const needle = query.trim().toLowerCase();
  if (!needle) return models;
  return models.filter((model) => model.id.toLowerCase().includes(needle));
}

export function filterModelPickerList<T extends { id: string }>(models: T[], inputValue: string): T[] {
  if (models.some((model) => model.id === inputValue)) return models;
  return filterDetectedModels(models, inputValue);
}

export function isMaskedApiKey(value: string): boolean {
  if (!value) return false;
  if (value === "****") return true;
  if (value.length <= 12) return false;
  const middle = value.slice(4, -4);
  return middle.length > 0 && /^\*+$/.test(middle);
}

export function isModelUnhealthy(caps: ModelCapabilities | null | undefined): boolean {
  if (!caps) return false;
  if (caps.healthy === false) return true;
  if (caps.probe_errors?.health) return true;
  return false;
}

export function getHealthError(caps: ModelCapabilities | null | undefined): string {
  if (!caps) return "";
  return caps.health_error || caps.probe_errors?.health || "";
}

export function normalizeFetchedCapabilities(caps: ModelCapabilities): ModelCapabilities {
  // 重新进入页面时清除上次探测残留的错误提示，避免红色告警持久存在。
  // 1) health 错误：始终清除（与之前行为一致）
  // 2) 能力 probe 错误：当结果已确定（true/false）时清除（badge 已表达结果），
  //    仅保留 value===null（不确定）时的错误以便诊断。
  const nextProbeErrors = { ...(caps.probe_errors || {}) };
  let changed = false;

  // 清理 health
  if (caps.healthy === false || nextProbeErrors.health) {
    delete nextProbeErrors.health;
    changed = true;
  }

  // 清理已确定结果的能力探测错误
  const capFields: [string, boolean | null][] = [
    ["tool_calling", caps.supports_tool_calling],
    ["vision", caps.supports_vision],
    ["thinking", caps.supports_thinking],
  ];
  for (const [key, value] of capFields) {
    if (value !== null && nextProbeErrors[key]) {
      delete nextProbeErrors[key];
      changed = true;
    }
  }

  if (!changed) return caps;

  return {
    ...caps,
    healthy: caps.healthy === false ? null : caps.healthy,
    health_error: caps.healthy === false ? "" : caps.health_error,
    probe_errors: nextProbeErrors,
  };
}
