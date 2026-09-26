"use client";

import type { ReactNode } from "react";
import { MODEL_CATALOG, providerDefaults } from "@/lib/model-catalog";
import type { ProviderPreset, CodexModelEntry } from "./types";

export const SECTION_META: {
  key: string;
  label: string;
  icon: ReactNode;
  fields: ("api_key" | "base_url" | "model")[];
  desc: string;
}[] = [];

export const FIELD_LABELS: Record<string, string> = {
  api_key: "API Key",
  base_url: "Base URL",
  model: "Model ID",
};

export const PROVIDER_LOGO_SLUG: Record<string, string> = {
  openai: "openai",
  anthropic: "anthropic",
  claude: "anthropic",
  gemini: "gemini",
  google: "gemini",
  deepseek: "deepseek",
  qwen: "qwen",
  dashscope: "qwen",
  aliyuncs: "qwen",
  aliyun: "alibabacloud",
  alibaba: "alibabacloud",
  alibabacloud: "alibabacloud",
  zhipu: "zhipu",
  glm: "zhipu",
  "openai-codex": "openai",
  openrouter: "openrouter",
  kimi: "moonshot",
  moonshot: "moonshot",
  x: "x",
  xai: "x",
  grok: "x",
  mistral: "mistral",
  mistralai: "mistral",
  tencent: "qq",
  hunyuan: "qq",
  workbuddy: "qq",
  "workbuddy-cn": "qq",
  "workbuddy-global": "qq",
  codebuddy: "qq",
  antigravity: "gemini",
  qq: "qq",
  bytedance: "bytedance",
  doubao: "bytedance",
  volcengine: "bytedance",
  ark: "bytedance",
  meta: "meta",
  llama: "meta",
  perplexity: "perplexity",
  baidu: "baidu",
  huawei: "huawei",
  nvidia: "nvidia",
  huggingface: "huggingface",
  hf: "huggingface",
  siliconflow: "siliconcloud",
  siliconcloud: "siliconcloud",
  minimax: "minimax",
  mimo: "xiaomi",
  xiaomi: "xiaomi",
  xiaomimimo: "xiaomi",
};

export const PROVIDER_PRESETS: ProviderPreset[] = [
  {
    id: "openai",
    ...providerDefaults("openai"),
    label: "OpenAI",
    icon: "🟢",
    purchaseUrl: "https://platform.openai.com/api-keys",
  },
  {
    id: "anthropic",
    ...providerDefaults("anthropic"),
    label: "Anthropic",
    icon: "🟤",
    purchaseUrl: "https://console.anthropic.com/settings/keys",
  },
  {
    id: "gemini",
    ...providerDefaults("gemini"),
    label: "Google Gemini",
    icon: "🔵",
    purchaseUrl: "https://aistudio.google.com/apikey",
  },
  {
    id: "deepseek",
    ...providerDefaults("deepseek"),
    label: "DeepSeek",
    icon: "🐋",
    purchaseUrl: "https://platform.deepseek.com/api_keys",
  },
  {
    id: "qwen",
    ...providerDefaults("qwen"),
    label: "阿里云百炼",
    icon: "☁️",
    purchaseUrl: "https://dashscope.console.aliyun.com/apiKey",
  },
  {
    id: "zhipu",
    ...providerDefaults("zhipu"),
    label: "智谱 AI",
    icon: "🧠",
    purchaseUrl: "https://open.bigmodel.cn/usercenter/apikeys",
  },
  {
    id: "openrouter",
    ...providerDefaults("openrouter"),
    label: "OpenRouter",
    icon: "🔀",
    purchaseUrl: "https://openrouter.ai/keys",
  },
  {
    id: "kimi",
    ...providerDefaults("kimi"),
    label: "Kimi (月之暗面)",
    icon: "🌙",
    purchaseUrl: "https://platform.moonshot.cn/console/api-keys",
  },
  {
    id: "minimax",
    ...providerDefaults("minimax"),
    label: "MiniMax",
    icon: "M",
    purchaseUrl: "https://platform.minimax.io/",
  },
  {
    id: "xai",
    ...providerDefaults("xai"),
    label: "xAI Grok",
    icon: "X",
    purchaseUrl: "https://console.x.ai/",
  },
  {
    id: "doubao",
    ...providerDefaults("doubao"),
    label: "字节豆包",
    icon: "🫘",
    purchaseUrl: "https://console.volcengine.com/ark",
  },
  {
    id: "mimo",
    ...providerDefaults("mimo"),
    label: "小米 MiMo",
    icon: "米",
    purchaseUrl: "https://platform.xiaomimimo.com",
  },
];

export const CODEX_OAUTH_PRESET: ProviderPreset = {
  id: "openai-codex",
  label: "OpenAI Codex",
  icon: "🧩",
  model: "openai-codex/gpt-5.2-codex",
  base_url: "https://api.openai.com/v1",
  protocol: "openai_responses",
  thinking_mode: "openai_reasoning",
  model_family: "gpt",
  description: "订阅 OAuth 登录，无需 API Key",
  purchaseUrl: "https://chatgpt.com",
};

export const WORKBUDDY_CN_OAUTH_PRESET: ProviderPreset = {
  id: "workbuddy-cn",
  label: "WorkBuddy 国内版",
  icon: "🐧",
  model: "workbuddy-cn/auto",
  base_url: "https://copilot.tencent.com/v2",
  protocol: "openai",
  thinking_mode: "auto",
  model_family: "",
  description: "WorkBuddy/CodeBuddy 国内版订阅登录，无需 API Key",
  purchaseUrl: "https://www.codebuddy.cn",
};

export const WORKBUDDY_GLOBAL_OAUTH_PRESET: ProviderPreset = {
  id: "workbuddy-global",
  label: "WorkBuddy Global",
  icon: "🐧",
  model: "workbuddy-global/auto",
  base_url: "https://www.workbuddy.ai/v2",
  protocol: "openai",
  thinking_mode: "auto",
  model_family: "",
  description: "WorkBuddy 国际版订阅登录，无需 API Key",
  purchaseUrl: "https://www.workbuddy.ai",
};

export const ANTIGRAVITY_OAUTH_PRESET: ProviderPreset = {
  id: "antigravity",
  label: "Google Antigravity",
  icon: "🌐",
  model: "antigravity/claude-sonnet-4-6",
  base_url: "https://daily-cloudcode-pa.googleapis.com",
  protocol: "antigravity",
  thinking_mode: "gemini_level",
  model_family: "claude",
  description: "Google Antigravity / Cloud Code Assist 订阅登录，无需 API Key",
  purchaseUrl: "https://antigravity.google",
};

export const CODEX_MODELS: CodexModelEntry[] = MODEL_CATALOG.codex_models.map((entry) => ({
  modelId: entry.model, publicId: `openai-codex/${entry.model}`,
  profileName: `openai-codex/${entry.model}`, displayName: entry.display_name,
  proOnly: entry.pro_only,
}));


export function getAvailableCodexModels(planType?: string): CodexModelEntry[] {
  const isPro = planType === "pro";
  return CODEX_MODELS.filter((m) => !m.proOnly || isPro);
}

export function getDefaultCodexModel(planType?: string): CodexModelEntry {
  const available = getAvailableCodexModels(planType);
  return available[0];
}
