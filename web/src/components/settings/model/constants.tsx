"use client";

import type { ReactNode } from "react";
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
    label: "OpenAI",
    icon: "🟢",
    model: "gpt-4o",
    base_url: "https://api.openai.com/v1",
    protocol: "openai",
    thinking_mode: "auto",
    model_family: "gpt",
    description: "GPT-4o 经典模型",
    purchaseUrl: "https://platform.openai.com/api-keys",
  },
  {
    id: "anthropic",
    label: "Anthropic",
    icon: "🟤",
    model: "claude-sonnet-4",
    base_url: "https://api.anthropic.com",
    protocol: "anthropic",
    thinking_mode: "claude",
    model_family: "claude",
    description: "Claude Sonnet 4",
    purchaseUrl: "https://console.anthropic.com/settings/keys",
  },
  {
    id: "gemini",
    label: "Google Gemini",
    icon: "🔵",
    model: "gemini-2.5-flash",
    base_url: "https://generativelanguage.googleapis.com/v1beta/openai",
    protocol: "openai",
    thinking_mode: "gemini_level",
    model_family: "gemini",
    description: "Gemini 2.5 Flash (API Key)",
    purchaseUrl: "https://aistudio.google.com/apikey",
  },
  {
    id: "deepseek",
    label: "DeepSeek",
    icon: "🐋",
    model: "deepseek-v3",
    base_url: "https://api.deepseek.com/v1",
    protocol: "openai",
    thinking_mode: "auto",
    model_family: "deepseek",
    description: "DeepSeek-V3（经典模型）",
    purchaseUrl: "https://platform.deepseek.com/api_keys",
  },
  {
    id: "qwen",
    label: "阿里云百炼",
    icon: "☁️",
    model: "qwen-plus",
    base_url: "https://dashscope.aliyuncs.com/compatible-mode/v1",
    protocol: "openai",
    thinking_mode: "enable_thinking",
    model_family: "qwen",
    description: "通义千问 Qwen-Plus",
    purchaseUrl: "https://dashscope.console.aliyun.com/apiKey",
  },
  {
    id: "zhipu",
    label: "智谱 AI",
    icon: "🧠",
    model: "glm-4.5",
    base_url: "https://open.bigmodel.cn/api/paas/v4",
    protocol: "openai",
    thinking_mode: "glm_thinking",
    model_family: "glm",
    description: "GLM-4.5",
    purchaseUrl: "https://open.bigmodel.cn/usercenter/apikeys",
  },
  {
    id: "openrouter",
    label: "OpenRouter",
    icon: "🔀",
    model: "anthropic/claude-sonnet-4",
    base_url: "https://openrouter.ai/api/v1",
    protocol: "openai",
    thinking_mode: "openrouter",
    model_family: "",
    description: "全球模型聚合路由",
    purchaseUrl: "https://openrouter.ai/keys",
  },
  {
    id: "kimi",
    label: "Kimi (月之暗面)",
    icon: "🌙",
    model: "kimi-k2.6",
    base_url: "https://api.moonshot.cn/v1",
    protocol: "openai",
    thinking_mode: "auto",
    model_family: "moonshot",
    description: "Kimi K2.6",
    purchaseUrl: "https://platform.moonshot.cn/console/api-keys",
  },
  {
    id: "minimax",
    label: "MiniMax",
    icon: "M",
    model: "MiniMax-M2",
    base_url: "https://api.minimax.io/v1",
    protocol: "openai",
    thinking_mode: "auto",
    model_family: "minimax",
    description: "MiniMax M2",
    purchaseUrl: "https://platform.minimax.io/",
  },
  {
    id: "xai",
    label: "xAI Grok",
    icon: "X",
    model: "grok-4",
    base_url: "https://api.x.ai/v1",
    protocol: "openai",
    thinking_mode: "auto",
    model_family: "grok",
    description: "Grok 4",
    purchaseUrl: "https://console.x.ai/",
  },
  {
    id: "doubao",
    label: "字节豆包",
    icon: "🫘",
    model: "doubao-seed-1.6",
    base_url: "https://ark.cn-beijing.volces.com/api/v3",
    protocol: "openai",
    thinking_mode: "auto",
    model_family: "doubao",
    description: "Doubao Seed 1.6",
    purchaseUrl: "https://console.volcengine.com/ark",
  },
  {
    id: "mimo",
    label: "小米 MiMo",
    icon: "米",
    model: "mimo-v2.6-flash",
    base_url: "https://api.xiaomimimo.com/v1",
    protocol: "openai",
    thinking_mode: "glm_thinking",
    model_family: "mimo",
    description: "MiMo V2.6 Flash（全模态 · 1M 上下文）",
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

export const CODEX_MODELS: CodexModelEntry[] = [
  { modelId: "gpt-5.2-codex", publicId: "openai-codex/gpt-5.2-codex", profileName: "openai-codex/gpt-5.2-codex", displayName: "Codex 5.2 (Legacy)", proOnly: false },
  { modelId: "gpt-5.1-codex", publicId: "openai-codex/gpt-5.1-codex", profileName: "openai-codex/gpt-5.1-codex", displayName: "Codex 5.1 (Legacy)", proOnly: false },
  { modelId: "gpt-5.1-codex-mini", publicId: "openai-codex/gpt-5.1-codex-mini", profileName: "openai-codex/gpt-5.1-codex-mini", displayName: "Codex Mini (Legacy)", proOnly: false },
  { modelId: "gpt-5.1-codex-max", publicId: "openai-codex/gpt-5.1-codex-max", profileName: "openai-codex/gpt-5.1-codex-max", displayName: "Codex Max (Legacy)", proOnly: false },
  { modelId: "gpt-5-codex", publicId: "openai-codex/gpt-5-codex", profileName: "openai-codex/gpt-5-codex", displayName: "Codex 5 (Legacy)", proOnly: false },
  { modelId: "gpt-5-codex-mini", publicId: "openai-codex/gpt-5-codex-mini", profileName: "openai-codex/gpt-5-codex-mini", displayName: "Codex Mini (GPT-5)", proOnly: false },
  { modelId: "gpt-5.2", publicId: "openai-codex/gpt-5.2", profileName: "openai-codex/gpt-5.2", displayName: "GPT-5.2 (Legacy)", proOnly: false },
  { modelId: "gpt-5.1", publicId: "openai-codex/gpt-5.1", profileName: "openai-codex/gpt-5.1", displayName: "GPT-5.1 (Legacy)", proOnly: false },
  { modelId: "gpt-5", publicId: "openai-codex/gpt-5", profileName: "openai-codex/gpt-5", displayName: "GPT-5 (Legacy)", proOnly: false },
  { modelId: "gpt-5.3-codex", publicId: "openai-codex/gpt-5.3-codex", profileName: "openai-codex/gpt-5.3-codex", displayName: "Codex 5.3 (Legacy)", proOnly: false },
  { modelId: "gpt-5.3-codex-spark", publicId: "openai-codex/gpt-5.3-codex-spark", profileName: "openai-codex/gpt-5.3-codex-spark", displayName: "Codex Spark (Legacy)", proOnly: true },
];


export function getAvailableCodexModels(planType?: string): CodexModelEntry[] {
  const isPro = planType === "pro";
  return CODEX_MODELS.filter((m) => !m.proOnly || isPro);
}

export function getDefaultCodexModel(planType?: string): CodexModelEntry {
  const available = getAvailableCodexModels(planType);
  return available[0];
}
