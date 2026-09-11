"use client";

import type { ReactNode } from "react";
import { Bot, Brain } from "lucide-react";
import type { ProviderPreset, CodexModelEntry } from "./types";

export const SECTION_META: {
  key: string;
  label: string;
  icon: ReactNode;
  fields: ("api_key" | "base_url" | "model")[];
  desc: string;
}[] = [
  {
    key: "aux",
    label: "辅助模型 (Aux)",
    icon: <Bot className="h-4 w-4" />,
    fields: ["model", "base_url", "api_key"],
    desc: "路由 + 子代理默认模型",
  },
  {
    key: "embedding",
    label: "Embedding 词嵌入",
    icon: <Brain className="h-4 w-4" />,
    fields: ["model", "base_url", "api_key"],
    desc: "语义检索 / 记忆 / 技能路由",
  },
];

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
};

export const PROVIDER_PRESETS: ProviderPreset[] = [
  {
    id: "openai",
    label: "OpenAI",
    icon: "🟢",
    model: "gpt-6-astra",
    base_url: "https://api.openai.com/v1",
    protocol: "openai",
    thinking_mode: "openai_reasoning",
    model_family: "gpt",
    description: "GPT-6 Astra 旗舰",
    purchaseUrl: "https://platform.openai.com/api-keys",
  },
  {
    id: "anthropic",
    label: "Anthropic",
    icon: "🟤",
    model: "claude-sonnet-5",
    base_url: "https://api.anthropic.com",
    protocol: "anthropic",
    thinking_mode: "claude",
    model_family: "claude",
    description: "Claude Sonnet 5",
    purchaseUrl: "https://console.anthropic.com/settings/keys",
  },
  {
    id: "gemini",
    label: "Google Gemini",
    icon: "🔵",
    model: "gemini-3.8-flash",
    base_url: "https://generativelanguage.googleapis.com/v1beta/openai",
    protocol: "openai",
    thinking_mode: "gemini_level",
    model_family: "gemini",
    description: "Gemini 3.8 Flash (API Key)",
    purchaseUrl: "https://aistudio.google.com/apikey",
  },
  {
    id: "deepseek",
    label: "DeepSeek",
    icon: "🐋",
    model: "deepseek-flash",
    base_url: "https://api.deepseek.com/v1",
    protocol: "openai",
    thinking_mode: "enable_thinking",
    model_family: "deepseek",
    description: "DeepSeek-V4.1 Flash（原生多模态）",
    purchaseUrl: "https://platform.deepseek.com/api_keys",
  },
  {
    id: "qwen",
    label: "阿里云百炼",
    icon: "☁️",
    model: "qwen3.7-plus",
    base_url: "https://dashscope.aliyuncs.com/compatible-mode/v1",
    protocol: "openai",
    thinking_mode: "enable_thinking",
    model_family: "qwen",
    description: "通义千问 Qwen3.7 Plus（原生多模态）",
    purchaseUrl: "https://dashscope.console.aliyun.com/apiKey",
  },
  {
    id: "zhipu",
    label: "智谱 AI",
    icon: "🧠",
    model: "glm-5.3",
    base_url: "https://open.bigmodel.cn/api/paas/v4",
    protocol: "openai",
    thinking_mode: "glm_thinking",
    model_family: "glm",
    description: "GLM-5.3",
    purchaseUrl: "https://open.bigmodel.cn/usercenter/apikeys",
  },
  {
    id: "openrouter",
    label: "OpenRouter",
    icon: "🔀",
    model: "anthropic/claude-sonnet-5",
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
    model: "kimi-k3",
    base_url: "https://api.moonshot.cn/v1",
    protocol: "openai",
    thinking_mode: "openai_reasoning",
    model_family: "moonshot",
    description: "Kimi K3",
    purchaseUrl: "https://platform.moonshot.cn/console/api-keys",
  },
  {
    id: "minimax",
    label: "MiniMax",
    icon: "M",
    model: "MiniMax-M3",
    base_url: "https://api.minimax.io/v1",
    protocol: "openai",
    thinking_mode: "auto",
    model_family: "minimax",
    description: "MiniMax M3",
    purchaseUrl: "https://platform.minimax.io/",
  },
  {
    id: "xai",
    label: "xAI Grok",
    icon: "X",
    model: "grok-4.6",
    base_url: "https://api.x.ai/v1",
    protocol: "openai",
    thinking_mode: "openai_reasoning",
    model_family: "grok",
    description: "Grok 4.6",
    purchaseUrl: "https://console.x.ai/",
  },
  {
    id: "doubao",
    label: "字节豆包",
    icon: "🫘",
    model: "doubao-seed-2.1-pro",
    base_url: "https://ark.cn-beijing.volces.com/api/v3",
    protocol: "openai",
    thinking_mode: "auto",
    model_family: "doubao",
    description: "Doubao Seed 2.1 Pro",
    purchaseUrl: "https://console.volcengine.com/ark",
  },
];

export const CODEX_OAUTH_PRESET: ProviderPreset = {
  id: "openai-codex",
  label: "OpenAI Codex",
  icon: "🧩",
  model: "openai-codex/gpt-6-astra",
  base_url: "https://api.openai.com/v1",
  protocol: "openai",
  thinking_mode: "openai_reasoning",
  model_family: "gpt",
  description: "订阅 OAuth 登录，无需 API Key",
  purchaseUrl: "https://chatgpt.com",
};

export const CODEX_MODELS: CodexModelEntry[] = [
  { modelId: "gpt-6-astra", publicId: "openai-codex/gpt-6-astra", profileName: "openai-codex/gpt-6-astra", displayName: "GPT-6 Astra", proOnly: false },
  { modelId: "gpt-5.6-sol", publicId: "openai-codex/gpt-5.6-sol", profileName: "openai-codex/gpt-5.6-sol", displayName: "GPT-5.6 Sol", proOnly: false },
  { modelId: "gpt-5.6", publicId: "openai-codex/gpt-5.6", profileName: "openai-codex/gpt-5.6", displayName: "GPT-5.6", proOnly: false },
  { modelId: "gpt-5.6-terra", publicId: "openai-codex/gpt-5.6-terra", profileName: "openai-codex/gpt-5.6-terra", displayName: "GPT-5.6 Terra", proOnly: false },
  { modelId: "gpt-5.6-luna", publicId: "openai-codex/gpt-5.6-luna", profileName: "openai-codex/gpt-5.6-luna", displayName: "GPT-5.6 Luna", proOnly: false },
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
