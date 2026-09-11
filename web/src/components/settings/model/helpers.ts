import { PROVIDER_COLORS as PROVIDER_BRAND_COLOR } from "@/lib/provider-brand";
import type { ModelCapabilities, ProfileEntry } from "./types";

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
