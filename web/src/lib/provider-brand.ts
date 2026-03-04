/** Provider brand colors for visual distinction — single source of truth */
export const PROVIDER_COLORS: Record<string, string> = {
  openai: "#10a37f",
  "openai-codex": "#10a37f",
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

export function getProviderColor(provider: string): string {
  return PROVIDER_COLORS[provider] || "#888";
}

export function getProviderDisplayName(provider: string): string {
  return (
    PROVIDER_DISPLAY[provider] ||
    provider.charAt(0).toUpperCase() + provider.slice(1)
  );
}
