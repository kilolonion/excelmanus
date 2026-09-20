/** Provider brand colors for visual distinction — single source of truth */
export const PROVIDER_COLORS: Record<string, string> = {
  openai: "#10a37f",
  "openai-codex": "#10a37f",
  workbuddy: "#0052d9",
  "workbuddy-cn": "#0052d9",
  "workbuddy-global": "#0052d9",
  codebuddy: "#0052d9",
  antigravity: "#4285f4",
  anthropic: "#d4a574",
  claude: "#d4a574",
  gemini: "#4285f4",
  google: "#4285f4",
  deepseek: "#4d6bfe",
  qwen: "#ff6a00",
  dashscope: "#ff6a00",
  aliyuncs: "#ff6a00",
  aliyun: "#ff6a00",
  alibaba: "#ff6a00",
  alibabacloud: "#ff6a00",
  zhipu: "#2563eb",
  glm: "#2563eb",
  openrouter: "#7c3aed",
  kimi: "#7c3aed",
  moonshot: "#7c3aed",
  x: "#f28834",
  xai: "#f28834",
  grok: "#f28834",
  mistral: "#fa520f",
  mistralai: "#fa520f",
  tencent: "#1ebafc",
  hunyuan: "#1ebafc",
  qq: "#1ebafc",
  bytedance: "#3c8cff",
  doubao: "#3c8cff",
  volcengine: "#3c8cff",
  ark: "#3c8cff",
  meta: "#0467df",
  llama: "#0467df",
  perplexity: "#1fb8cd",
  baidu: "#2932e1",
  groq: "#f55036",
  together: "#6366f1",
  cohere: "#39594d",
  huawei: "#ff0000",
  nvidia: "#76b900",
  huggingface: "#ffd21e",
  hf: "#ffd21e",
  siliconflow: "#06b6d4",
  siliconcloud: "#06b6d4",
  minimax: "#ec5f27",
};

/** Friendly display names for known providers */
export const PROVIDER_DISPLAY: Record<string, string> = {
  openai: "OpenAI",
  "openai-codex": "OpenAI Codex",
  workbuddy: "WorkBuddy",
  "workbuddy-cn": "WorkBuddy 国内版",
  "workbuddy-global": "WorkBuddy Global",
  codebuddy: "CodeBuddy",
  antigravity: "Antigravity",
  anthropic: "Anthropic",
  claude: "Anthropic",
  gemini: "Google Gemini",
  google: "Google",
  deepseek: "DeepSeek",
  qwen: "阿里云百炼",
  dashscope: "DashScope",
  aliyuncs: "阿里云",
  aliyun: "阿里云",
  alibaba: "阿里云",
  alibabacloud: "阿里云",
  zhipu: "智谱",
  glm: "智谱",
  openrouter: "OpenRouter",
  kimi: "Kimi",
  moonshot: "Moonshot",
  x: "xAI",
  xai: "xAI",
  grok: "xAI",
  mistral: "Mistral",
  mistralai: "Mistral",
  tencent: "腾讯",
  hunyuan: "混元",
  qq: "腾讯",
  bytedance: "字节跳动",
  doubao: "豆包",
  volcengine: "火山引擎",
  ark: "火山方舟",
  meta: "Meta",
  llama: "Meta",
  perplexity: "Perplexity",
  baidu: "百度",
  groq: "Groq",
  together: "Together",
  cohere: "Cohere",
  huawei: "华为",
  nvidia: "NVIDIA",
  huggingface: "Hugging Face",
  hf: "Hugging Face",
  siliconflow: "SiliconFlow",
  siliconcloud: "SiliconCloud",
  minimax: "MiniMax",
};

/**
 * 从 base_url 提取二级域名作为 provider 名称。
 * 例如 https://api.deepseek.com/v1 → deepseek
 *      https://dashscope.aliyuncs.com/compatible-mode/v1 → aliyuncs
 *      https://api.openai.com/v1 → openai
 */
export function extractProvider(baseUrl: string | undefined): string {
  if (!baseUrl) return "unknown";
  try {
    const hostname = new URL(baseUrl).hostname;
    const parts = hostname.split(".");
    if (parts.length >= 2) return parts[parts.length - 2];
    return parts[0] || "unknown";
  } catch {
    return "unknown";
  }
}

interface ModelBrandSource {
  name?: string;
  model?: string;
  display_name?: string;
  resolved_model?: string;
  description?: string;
  base_url?: string;
  provider?: string;
}

const PROVIDER_ALIASES: Record<string, string> = {
  openai: "openai",
  "openai-codex": "openai-codex",
  workbuddy: "workbuddy",
  "workbuddy-cn": "workbuddy",
  "workbuddy-global": "workbuddy",
  codebuddy: "workbuddy",
  copilot: "workbuddy",
  antigravity: "antigravity",
  anthropic: "anthropic",
  claude: "anthropic",
  google: "gemini",
  gemini: "gemini",
  deepseek: "deepseek",
  qwen: "qwen",
  dashscope: "qwen",
  aliyuncs: "qwen",
  zhipu: "zhipu",
  glm: "zhipu",
  moonshot: "moonshot",
  kimi: "moonshot",
  x: "xai",
  xai: "xai",
  grok: "xai",
  mistral: "mistral",
  mistralai: "mistral",
  meta: "meta",
  llama: "meta",
  openrouter: "openrouter",
  tencent: "tencent",
  hunyuan: "tencent",
  qq: "tencent",
  bytedance: "bytedance",
  doubao: "bytedance",
  volcengine: "bytedance",
  baidu: "baidu",
  perplexity: "perplexity",
  nvidia: "nvidia",
  huggingface: "huggingface",
  hf: "huggingface",
  siliconflow: "siliconflow",
  siliconcloud: "siliconflow",
  minimax: "minimax",
  alibaba: "alibabacloud",
  aliyun: "alibabacloud",
  alibabacloud: "alibabacloud",
  huawei: "huawei",
};

const MODEL_BRAND_PATTERNS: Array<[string, RegExp]> = [
  ["openai-codex", /openai[-_\s/]?codex|\bcodex[-_\s/]+gpt/],
  // antigravity 模型 ID 内含 claude-/gemini-/gpt-oss，必须先于品牌规则命中
  ["antigravity", /antigravity|cloudcode[-_]pa/],
  ["workbuddy", /workbuddy|codebuddy|copilot\.tencent/],
  ["deepseek", /deep[-_\s]?seek/],
  ["anthropic", /anthropic|claude/],
  ["gemini", /gemini|generativelanguage\.googleapis/],
  ["qwen", /qwen|tongyi|dashscope|aliyuncs/],
  ["zhipu", /zhipu|\bglm[-_\s\d/]|bigmodel/],
  ["moonshot", /moonshot|kimi/],
  ["xai", /\bxai\b|\bgrok|api\.x\.ai/],
  ["mistral", /mistral|codestral|pixtral/],
  ["meta", /meta[-_\s/]?llama|\bllama/],
  ["openrouter", /openrouter/],
  ["tencent", /tencent|hunyuan|腾讯|混元/],
  ["bytedance", /bytedance|doubao|volcengine|volces|字节|豆包/],
  ["baidu", /baidu|ernie|qianfan|文心/],
  ["perplexity", /perplexity|\bpplx/],
  ["nvidia", /nvidia|nemotron/],
  ["huggingface", /huggingface|hugging[-_\s]?face|\bhf\//],
  ["siliconflow", /siliconflow|siliconcloud/],
  ["minimax", /minimax/],
  ["alibabacloud", /alibabacloud|aliyun|阿里云/],
  ["huawei", /huawei|pangu|华为|盘古/],
  ["openai", /openai|chatgpt|\bgpt(?:[-_\s/]|\d)|\bo[134](?:[-_\s/]|$)/],
];

function brandFromText(value: string | undefined): string | null {
  const normalized = value?.trim().toLowerCase();
  if (!normalized) return null;
  for (const [brand, pattern] of MODEL_BRAND_PATTERNS) {
    if (pattern.test(normalized)) return brand;
  }
  return null;
}

/**
 * Infer the model's brand from its identity, not just its transport endpoint.
 * This keeps branded models recognizable behind proxies and custom gateways.
 */
export function inferModelBrand(source: ModelBrandSource): string {
  const modelIdentity = [
    source.resolved_model,
    source.model,
    source.display_name,
    source.name,
  ].filter(Boolean).join(" ");
  const modelBrand = brandFromText(modelIdentity);
  if (modelBrand) return modelBrand;

  const explicitProvider = source.provider?.trim().toLowerCase();
  if (explicitProvider) {
    const canonical = PROVIDER_ALIASES[explicitProvider];
    if (canonical) return canonical;
    const providerBrand = brandFromText(explicitProvider);
    if (providerBrand) return providerBrand;
  }

  const endpointBrand = brandFromText(source.base_url);
  if (endpointBrand) return endpointBrand;

  const descriptionBrand = brandFromText(source.description);
  if (descriptionBrand) return descriptionBrand;

  const endpointProvider = extractProvider(source.base_url);
  return PROVIDER_ALIASES[endpointProvider] || endpointProvider;
}

export function getProviderColor(provider: string): string {
  return PROVIDER_COLORS[provider] || "#6b7280";
}

export function getProviderDisplayName(provider: string): string {
  return (
    PROVIDER_DISPLAY[provider] ||
    provider.charAt(0).toUpperCase() + provider.slice(1)
  );
}
