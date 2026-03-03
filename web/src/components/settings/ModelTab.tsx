"use client";

import { useEffect, useState, useCallback, useRef } from "react";
import {
  Plus,
  Trash2,
  Pencil,
  Save,
  X,
  Eye,
  EyeOff,
  Loader2,
  CheckCircle2,
  Server,
  Bot,
  ScanEye,
  Zap,
  Wrench,
  ImageIcon,
  Brain,
  Check,
  AlertTriangle,
  Download,
  Upload,
  Import,
  Copy,
  Lock,
  Unlock,
  ClipboardPaste,
  Dices,
  Wifi,
  XCircle,
  ChevronDown,
  ChevronRight,
  ArrowRightLeft,
  ExternalLink,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Separator } from "@/components/ui/separator";
import { Switch } from "@/components/ui/switch";
import { apiGet, apiPut, apiPost, apiDelete, testModelConnection, listRemoteModels } from "@/lib/api";
import type { RemoteModelItem } from "@/lib/api";
import { settingsCache } from "@/lib/settings-cache";
import type { TestConnectionResult } from "@/lib/api";
import { Search } from "lucide-react";
import { MiniCheckbox } from "@/components/ui/MiniCheckbox";
import {
  fetchCodexStatus,
  codexOAuthStart,
  codexOAuthExchange,
  codexDeviceCodeStart,
  codexDeviceCodePoll,
  connectCodex,
  disconnectCodex,
  refreshCodexToken,
  type CodexStatus,
} from "@/lib/auth-api";
import { useAuthStore } from "@/stores/auth-store";
import { useAuthConfigStore } from "@/stores/auth-config-store";
import { useUIStore } from "@/stores/ui-store";
import { formatModelIdForDisplay } from "@/lib/model-display";

interface ModelSection {
  api_key?: string;
  base_url?: string;
  model?: string;
  enabled?: boolean;
  protocol?: string;
}

interface ProfileEntry {
  name: string;
  model: string;
  api_key: string;
  base_url: string;
  description: string;
  protocol: string;
  thinking_mode: string;
  model_family: string;
  custom_extra_body: string;
  custom_extra_headers: string;
}

interface ModelCapabilities {
  model: string;
  base_url: string;
  healthy: boolean | null;
  health_error: string;
  supports_tool_calling: boolean | null;
  supports_vision: boolean | null;
  supports_thinking: boolean | null;
  thinking_type: string;
  detected_at: string;
  probe_errors: Record<string, string>;
  manual_override: boolean;
}

interface ModelConfig {
  main: ModelSection;
  aux: ModelSection & { enabled?: boolean };
  vlm: ModelSection & { enabled?: boolean };
  embedding: ModelSection & { enabled?: boolean };
  profiles: ProfileEntry[];
}

const SECTION_META: {
  key: string;
  label: string;
  icon: React.ReactNode;
  fields: ("api_key" | "base_url" | "model")[];
  desc: string;
}[] = [
  {
    key: "aux",
    label: "辅助模型 (Aux)",
    icon: <Bot className="h-4 w-4" />,
    fields: ["model", "base_url", "api_key"],
    desc: "路由 + 子代理默认模型 + 窗口感知顾问",
  },
  {
    key: "vlm",
    label: "VLM 视觉模型",
    icon: <ScanEye className="h-4 w-4" />,
    fields: ["model", "base_url", "api_key"],
    desc: "图片表格提取",
  },
  {
    key: "embedding",
    label: "Embedding 词嵌入",
    icon: <Brain className="h-4 w-4" />,
    fields: ["model", "base_url", "api_key"],
    desc: "语义检索 / 记忆 / 技能路由",
  },
];

const FIELD_LABELS: Record<string, string> = {
  api_key: "API Key",
  base_url: "Base URL",
  model: "Model ID",
};

const PROVIDER_LOGO_SLUG: Record<string, string> = {
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

const PROVIDER_BRAND_COLOR: Record<string, string> = {
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
  huawei: "#ff0000",
  nvidia: "#76b900",
  huggingface: "#ffd21e",
  hf: "#ffd21e",
  siliconflow: "#06b6d4",
  siliconcloud: "#06b6d4",
  minimax: "#ec5f27",
};

function ProviderLogo({ id, color }: { id: string; color?: string }) {
  const slug = PROVIDER_LOGO_SLUG[id];
  if (!slug) return null;
  return (
    <span
      className="inline-block h-4 w-4 shrink-0"
      role="img"
      aria-label={id}
      style={{
        color: color || undefined,
        backgroundColor: "currentColor",
        maskImage: `url(/providers/${slug}.svg)`,
        WebkitMaskImage: `url(/providers/${slug}.svg)`,
        maskSize: "contain",
        WebkitMaskSize: "contain",
        maskRepeat: "no-repeat",
        WebkitMaskRepeat: "no-repeat",
        maskPosition: "center",
        WebkitMaskPosition: "center",
      }}
    />
  );
}

function withAlpha(hex: string, alphaHex: string): string {
  if (/^#[0-9a-fA-F]{6}$/.test(hex)) return `${hex}${alphaHex}`;
  return hex;
}

function inferProfileProvider(profile: Pick<ProfileEntry, "model" | "base_url" | "model_family" | "protocol">): string | null {
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

function getProviderBrandColor(provider: string | null): string {
  if (!provider) return "#6b7280";
  return PROVIDER_BRAND_COLOR[provider] || "#6b7280";
}

interface ProviderPreset {
  id: string;
  label: string;
  icon: string;
  model: string;
  base_url: string;
  protocol: string;
  thinking_mode: string;
  model_family: string;
  description: string;
  purchaseUrl: string;
}

const PROVIDER_PRESETS: ProviderPreset[] = [
  {
    id: "openai",
    label: "OpenAI",
    icon: "🟢",
    model: "gpt-4o",
    base_url: "https://api.openai.com/v1",
    protocol: "openai",
    thinking_mode: "auto",
    model_family: "gpt",
    description: "GPT-4o 多模态旗舰",
    purchaseUrl: "https://platform.openai.com/api-keys",
  },
  {
    id: "anthropic",
    label: "Anthropic",
    icon: "🟤",
    model: "claude-sonnet-4-20250514",
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
    thinking_mode: "auto",
    model_family: "gemini",
    description: "Gemini 2.5 Flash",
    purchaseUrl: "https://aistudio.google.com/apikey",
  },
  {
    id: "deepseek",
    label: "DeepSeek",
    icon: "🐋",
    model: "deepseek-chat",
    base_url: "https://api.deepseek.com/v1",
    protocol: "openai",
    thinking_mode: "deepseek",
    model_family: "deepseek",
    description: "DeepSeek-V3",
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
    description: "通义千问 Qwen",
    purchaseUrl: "https://dashscope.console.aliyun.com/apiKey",
  },
  {
    id: "zhipu",
    label: "智谱 AI",
    icon: "🧠",
    model: "glm-4-plus",
    base_url: "https://open.bigmodel.cn/api/paas/v4",
    protocol: "openai",
    thinking_mode: "glm_thinking",
    model_family: "glm",
    description: "GLM-4 Plus",
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
    model: "moonshot-v1-128k",
    base_url: "https://api.moonshot.cn/v1",
    protocol: "openai",
    thinking_mode: "auto",
    model_family: "moonshot",
    description: "Moonshot 长上下文",
    purchaseUrl: "https://platform.moonshot.cn/console/api-keys",
  },
  {
    id: "minimax",
    label: "MiniMax",
    icon: "M",
    model: "MiniMax-M2.5",
    base_url: "https://api.minimax.io/v1",
    protocol: "openai",
    thinking_mode: "auto",
    model_family: "minimax",
    description: "MiniMax 海螺",
    purchaseUrl: "https://platform.minimax.io/",
  },
];

const CODEX_OAUTH_PRESET: ProviderPreset = {
  id: "openai-codex",
  label: "OpenAI Codex",
  icon: "🧩",
  model: "openai-codex/gpt-5.3-codex-spark",
  base_url: "https://api.openai.com/v1",
  protocol: "openai",
  thinking_mode: "openai_reasoning",
  model_family: "gpt",
  description: "订阅 OAuth 登录，无需 API Key",
  purchaseUrl: "https://chatgpt.com",
};

interface CodexModelEntry {
  modelId: string;
  publicId: string;
  profileName: string;
  displayName: string;
  proOnly: boolean;
}

const CODEX_MODELS: CodexModelEntry[] = [
  { modelId: "gpt-5.3-codex-spark", publicId: "openai-codex/gpt-5.3-codex-spark", profileName: "openai-codex/gpt-5.3-codex-spark", displayName: "Codex Spark", proOnly: true },
  { modelId: "gpt-5.3-codex", publicId: "openai-codex/gpt-5.3-codex", profileName: "openai-codex/gpt-5.3-codex", displayName: "Codex 5.3", proOnly: false },
  { modelId: "gpt-5.2-codex", publicId: "openai-codex/gpt-5.2-codex", profileName: "openai-codex/gpt-5.2-codex", displayName: "Codex 5.2", proOnly: false },
  { modelId: "gpt-5.1-codex", publicId: "openai-codex/gpt-5.1-codex", profileName: "openai-codex/gpt-5.1-codex", displayName: "Codex 5.1", proOnly: false },
  { modelId: "gpt-5.1-codex-mini", publicId: "openai-codex/gpt-5.1-codex-mini", profileName: "openai-codex/gpt-5.1-codex-mini", displayName: "Codex Mini", proOnly: false },
  { modelId: "gpt-5.1-codex-max", publicId: "openai-codex/gpt-5.1-codex-max", profileName: "openai-codex/gpt-5.1-codex-max", displayName: "Codex Max", proOnly: false },
  { modelId: "codex-mini-latest", publicId: "openai-codex/codex-mini-latest", profileName: "openai-codex/codex-mini-latest", displayName: "Codex Mini Latest", proOnly: false },
  { modelId: "gpt-5.2", publicId: "openai-codex/gpt-5.2", profileName: "openai-codex/gpt-5.2", displayName: "GPT-5.2 (Codex)", proOnly: false },
  { modelId: "gpt-5.1", publicId: "openai-codex/gpt-5.1", profileName: "openai-codex/gpt-5.1", displayName: "GPT-5.1 (Codex)", proOnly: false },
];

function getAvailableCodexModels(planType?: string): CodexModelEntry[] {
  const isPro = planType === "pro";
  return CODEX_MODELS.filter((m) => !m.proOnly || isPro);
}

function getDefaultCodexModel(planType?: string): CodexModelEntry {
  const available = getAvailableCodexModels(planType);
  return available[0];
}

function isMaskedApiKey(value: string): boolean {
  if (!value) return false;
  if (value === "****") return true;
  if (value.length <= 12) return false;
  const middle = value.slice(4, -4);
  return middle.length > 0 && /^\*+$/.test(middle);
}

function isModelUnhealthy(caps: ModelCapabilities | null | undefined): boolean {
  if (!caps) return false;
  if (caps.healthy === false) return true;
  if (caps.probe_errors?.health) return true;
  return false;
}

function getHealthError(caps: ModelCapabilities | null | undefined): string {
  if (!caps) return "";
  return caps.health_error || caps.probe_errors?.health || "";
}

function normalizeFetchedCapabilities(caps: ModelCapabilities): ModelCapabilities {
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

// ── 普通用户自定义 API 配置面板 ─────────────────────────────

function UserApiConfigPanel({ user }: { user: AuthUser | null }) {
  const [draft, setDraft] = useState({ api_key: "", base_url: "", model: "" });
  const [loaded, setLoaded] = useState(false);
  const [showKey, setShowKey] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [clearing, setClearing] = useState(false);
  const [codexStatus, setCodexStatus] = useState<CodexStatus | null>(null);
  const [codexLoading, setCodexLoading] = useState(true);
  const [codexError, setCodexError] = useState("");

  // OAuth PKCE Browser Flow state
  const [oauthBusy, setOauthBusy] = useState(false);
  const [oauthState, setOauthState] = useState("");
  const [pasteUrl, setPasteUrl] = useState("");
  const [oauthMode, setOauthMode] = useState<"popup" | "paste" | null>(null);
  const popupRef = useRef<Window | null>(null);
  const popupTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Device Code Flow state
  const [deviceState, setDeviceState] = useState<string | null>(null);
  const [userCode, setUserCode] = useState("");
  const [verificationUrl, setVerificationUrl] = useState("");
  const [authorizing, setAuthorizing] = useState(false);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Manual paste / fallback
  const [tokenInput, setTokenInput] = useState("");
  const [connecting, setConnecting] = useState(false);
  const [disconnecting, setDisconnecting] = useState(false);
  const [refreshingToken, setRefreshingToken] = useState(false);
  const [showFallback, setShowFallback] = useState(false);
  const [showManualPaste, setShowManualPaste] = useState(false);
  const [codexSelectedModel, setCodexSelectedModel] = useState("");

  // 加载当前用户的自定义 LLM 配置
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const data = await apiGet<{ api_key: string; base_url: string; model: string }>("/config/models/user");
        if (!cancelled) {
          setDraft({
            api_key: data.api_key || "",
            base_url: data.base_url || "",
            model: data.model || "",
          });
          setLoaded(true);
        }
      } catch {
        if (!cancelled) setLoaded(true);
      }
    })();
    return () => { cancelled = true; };
  }, []);

  // ── Codex 状态加载 ──
  useEffect(() => {
    let cancelled = false;
    fetchCodexStatus()
      .then((data) => { if (!cancelled) setCodexStatus(data); })
      .catch(() => { if (!cancelled) setCodexStatus({ status: "disconnected", provider: "openai-codex" }); })
      .finally(() => { if (!cancelled) setCodexLoading(false); });
    return () => { cancelled = true; };
  }, []);

  // 清理轮询和 popup 监听
  useEffect(() => {
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
      if (popupTimerRef.current) clearInterval(popupTimerRef.current);
    };
  }, []);

  // 监听 popup postMessage 回调 (Path A - popup 自动模式)
  useEffect(() => {
    const handler = (event: MessageEvent) => {
      if (event.origin !== window.location.origin) return;
      if (event.data?.type !== "codex-oauth-callback") return;
      if (popupTimerRef.current) {
        clearInterval(popupTimerRef.current);
        popupTimerRef.current = null;
      }
      if (event.data.error) {
        setOauthBusy(false);
        setOauthMode(null);
        setCodexError(event.data.error);
        return;
      }
      const { code, state: cbState } = event.data;
      if (code && cbState) {
        codexOAuthExchange(code, cbState)
          .then((result) => {
            setCodexStatus({
              status: "connected",
              provider: "openai-codex",
              account_id: result.account_id,
              plan_type: result.plan_type,
              expires_at: result.expires_at,
              is_active: true,
              has_refresh_token: true,
            });
          })
          .catch((e: unknown) => {
            setCodexError(e instanceof Error ? e.message : "OAuth 交换失败");
          })
          .finally(() => {
            setOauthBusy(false);
            setOauthMode(null);
            setOauthState("");
          });
      }
    };
    window.addEventListener("message", handler);
    return () => window.removeEventListener("message", handler);
  }, []);

  // 当连接状态变化时，设置默认选中的 Codex 模型
  useEffect(() => {
    if (codexStatus?.status === "connected" && !codexSelectedModel) {
      const def = getDefaultCodexModel(codexStatus.plan_type);
      setCodexSelectedModel(def.publicId);
    }
  }, [codexStatus, codexSelectedModel]);

  const applyCodexPreset = useCallback(() => {
    const model = codexSelectedModel || getDefaultCodexModel(codexStatus?.plan_type).publicId;
    setDraft((d) => ({
      ...d,
      model,
      base_url: "https://api.openai.com/v1",
    }));
  }, [codexSelectedModel, codexStatus?.plan_type]);

  // ── OAuth PKCE 浏览器流程 ──
  const handleOAuthLogin = useCallback(async () => {
    if (oauthBusy) return;
    setOauthBusy(true);
    setPasteUrl("");
    setCodexError("");
    try {
      const isCliLocalCallback =
        ["localhost", "127.0.0.1"].includes(window.location.hostname)
        && window.location.port === "1455";
      const redirectUri = isCliLocalCallback
        ? `${window.location.origin}/auth/callback`
        : undefined;
      const data = await codexOAuthStart(redirectUri);
      setOauthState(data.state);
      setOauthMode(data.mode);
      const w = 600, h = 700;
      const left = window.screenX + (window.outerWidth - w) / 2;
      const top = window.screenY + (window.outerHeight - h) / 2;
      const popup = window.open(
        data.authorize_url,
        "codex-oauth",
        `width=${w},height=${h},left=${left},top=${top},toolbar=no,menubar=no`,
      );
      popupRef.current = popup;
      if (data.mode === "popup" && popup) {
        popupTimerRef.current = setInterval(() => {
          if (popup.closed) {
            if (popupTimerRef.current) {
              clearInterval(popupTimerRef.current);
              popupTimerRef.current = null;
            }
            setTimeout(() => {
              setOauthBusy((busy) => {
                if (busy) { setOauthMode(null); setOauthState(""); return false; }
                return busy;
              });
            }, 2000);
          }
        }, 500);
      }
      // paste 模式: popup 重定向到 localhost 后失败，用户手动粘贴 URL
    } catch (e) {
      setOauthBusy(false);
      setOauthMode(null);
      setCodexError(e instanceof Error ? e.message : "无法发起 OAuth 登录");
    }
  }, [oauthBusy]);

  const handlePasteUrlSubmit = useCallback(async () => {
    if (!pasteUrl.trim() || !oauthState) return;
    setCodexError("");
    try {
      const url = new URL(pasteUrl.trim());
      const code = url.searchParams.get("code");
      const state = url.searchParams.get("state");
      if (!code || !state) {
        setCodexError("URL 中缺少 code 或 state 参数");
        return;
      }
      const result = await codexOAuthExchange(code, state);
      setCodexStatus({
        status: "connected",
        provider: "openai-codex",
        account_id: result.account_id,
        plan_type: result.plan_type,
        expires_at: result.expires_at,
        is_active: true,
        has_refresh_token: true,
      });
    } catch (e) {
      setCodexError(e instanceof Error ? e.message : "连接失败");
    } finally {
      setOauthBusy(false);
      setOauthMode(null);
      setOauthState("");
      setPasteUrl("");
    }
  }, [pasteUrl, oauthState]);

  const cancelOAuth = useCallback(() => {
    if (popupRef.current && !popupRef.current.closed) popupRef.current.close();
    if (popupTimerRef.current) { clearInterval(popupTimerRef.current); popupTimerRef.current = null; }
    setOauthBusy(false);
    setOauthMode(null);
    setOauthState("");
    setPasteUrl("");
    setCodexError("");
  }, []);

  // ── Device Code Flow ──
  const stopPolling = useCallback(() => {
    if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
    setAuthorizing(false);
    setDeviceState(null);
    setUserCode("");
    setVerificationUrl("");
  }, []);

  const handleDeviceCodeAuthorize = useCallback(async () => {
    if (authorizing) return;
    setAuthorizing(true);
    setCodexError("");
    try {
      const data = await codexDeviceCodeStart();
      setUserCode(data.user_code);
      setVerificationUrl(data.verification_url);
      setDeviceState(data.state);
      const interval = Math.max(data.interval, 3) * 1000;
      pollRef.current = setInterval(async () => {
        try {
          const result = await codexDeviceCodePoll(data.state);
          if (result.status === "connected") {
            stopPolling();
            // 重新获取状态
            fetchCodexStatus()
              .then(setCodexStatus)
              .catch(() => {});
          }
        } catch {
          // 轮询错误不中断
        }
      }, interval);
      setTimeout(() => {
        if (pollRef.current) {
          stopPolling();
          setCodexError("设备码已过期，请重试");
        }
      }, 15 * 60 * 1000);
    } catch (e) {
      setAuthorizing(false);
      setCodexError(e instanceof Error ? e.message : "无法发起设备码登录");
    }
  }, [authorizing, stopPolling]);

  // ── Manual Token Paste ──
  const handleCodexTokenConnect = useCallback(async () => {
    const raw = tokenInput.trim();
    if (!raw || connecting) return;
    setConnecting(true);
    setCodexError("");
    try {
      const parsed = JSON.parse(raw) as Record<string, unknown>;
      const result = await connectCodex(parsed);
      setCodexStatus({
        status: "connected",
        provider: "openai-codex",
        account_id: result.account_id,
        plan_type: result.plan_type,
        expires_at: result.expires_at,
        is_active: true,
        has_refresh_token: true,
      });
      setTokenInput("");
      setShowManualPaste(false);
    } catch (e) {
      if (e instanceof SyntaxError) {
        setCodexError("JSON 格式无效，请粘贴完整 auth.json 内容");
      } else {
        setCodexError(e instanceof Error ? e.message : "连接 Codex 失败");
      }
    } finally {
      setConnecting(false);
    }
  }, [tokenInput, connecting]);

  const handleCodexDisconnect = useCallback(async () => {
    if (disconnecting) return;
    setDisconnecting(true);
    setCodexError("");
    try {
      await disconnectCodex();
      setCodexStatus({ status: "disconnected", provider: "openai-codex" });
    } catch (e) {
      setCodexError(e instanceof Error ? e.message : "断开 Codex 失败");
    } finally {
      setDisconnecting(false);
    }
  }, [disconnecting]);

  const handleCodexRefresh = useCallback(async () => {
    if (refreshingToken) return;
    setRefreshingToken(true);
    setCodexError("");
    try {
      const result = await refreshCodexToken();
      setCodexStatus((prev) => {
        if (!prev) return prev;
        return { ...prev, status: "connected", expires_at: result.expires_at };
      });
    } catch (e) {
      setCodexError(e instanceof Error ? e.message : "刷新 Codex token 失败");
    } finally {
      setRefreshingToken(false);
    }
  }, [refreshingToken]);

  const handleSave = async () => {
    setSaving(true);
    try {
      const { updateProfile } = await import("@/lib/auth-api");
      const body: Record<string, string> = {};
      if (draft.api_key && !isMaskedApiKey(draft.api_key)) body.llm_api_key = draft.api_key;
      if (draft.base_url) body.llm_base_url = draft.base_url;
      if (draft.model) body.llm_model = draft.model;
      await updateProfile(body);
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } catch {
      // 忽略
    } finally {
      setSaving(false);
    }
  };

  const handleClear = async () => {
    setClearing(true);
    try {
      const { updateProfile } = await import("@/lib/auth-api");
      await updateProfile({ llm_api_key: "", llm_base_url: "", llm_model: "" });
      setDraft({ api_key: "", base_url: "", model: "" });
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } catch {
      // 忽略
    } finally {
      setClearing(false);
    }
  };

  const hasCustomConfig = !!(draft.api_key || draft.base_url || draft.model);

  if (!loaded) {
    return (
      <div className="flex items-center justify-center py-12">
        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {/* 用户自定义 API 配置 */}
      <div className="rounded-lg border border-border p-4">
        <div className="flex items-center gap-2 mb-1">
          <Unlock className="h-4 w-4" style={{ color: "var(--em-primary)" }} />
          <h3 className="font-semibold text-sm">我的 API 配置</h3>
          {hasCustomConfig && (
            <Badge variant="secondary" className="text-[10px] ml-auto">已配置</Badge>
          )}
        </div>
        <p className="text-xs text-muted-foreground mb-3">
          配置您自己的 API Key 后，对话将使用您的 API 额度。留空则使用系统默认配置。
        </p>

        <div className="rounded-md border border-border/70 bg-muted/20 p-3 mb-3 space-y-2.5">
          {/* Header */}
          <div className="flex items-center gap-2">
            <ProviderLogo id="openai-codex" />
            <p className="text-xs font-semibold">OpenAI Codex 订阅登录</p>
            <Badge variant="secondary" className="text-[10px] ml-auto">
              {codexLoading
                ? "检测中"
                : codexStatus?.status === "connected"
                  ? "已连接"
                  : codexStatus?.status === "expired"
                    ? "已过期"
                    : "未连接"}
            </Badge>
          </div>

          {codexLoading && (
            <div className="flex items-center justify-center py-3">
              <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
            </div>
          )}

          {/* ── Connected / Expired state ── */}
          {!codexLoading && (codexStatus?.status === "connected" || codexStatus?.status === "expired") && (
            <>
              <div className="flex items-center gap-2 mb-1">
                <div className={`h-2 w-2 rounded-full ${codexStatus.status === "expired" ? "bg-amber-500" : "bg-green-500"}`} />
                <span className="text-xs font-medium">
                  {codexStatus.status === "expired" ? "Token 已过期" : "已连接"}
                </span>
              </div>
              <div className="grid grid-cols-2 gap-y-1 text-[11px]">
                {codexStatus.account_id && (
                  <>
                    <span className="text-muted-foreground">账户</span>
                    <span className="font-mono truncate">{codexStatus.account_id}</span>
                  </>
                )}
                {codexStatus.plan_type && (
                  <>
                    <span className="text-muted-foreground">订阅</span>
                    <span className="capitalize">{codexStatus.plan_type}</span>
                  </>
                )}
                {codexStatus.expires_at && (
                  <>
                    <span className="text-muted-foreground">有效期至</span>
                    <span className="text-[11px]">{new Date(codexStatus.expires_at).toLocaleString("zh-CN")}</span>
                  </>
                )}
              </div>

              {/* 模型选择器 + 应用 */}
              <div className="space-y-1.5">
                <label className="text-[10px] text-muted-foreground">选择 Codex 模型</label>
                <select
                  value={codexSelectedModel}
                  onChange={(e) => setCodexSelectedModel(e.target.value)}
                  className="w-full h-7 rounded-md border border-input bg-background px-2 text-[11px] font-mono outline-none focus:ring-1 focus:ring-[var(--em-primary)]"
                >
                  {getAvailableCodexModels(codexStatus.plan_type).map((m) => (
                    <option key={m.publicId} value={m.publicId}>
                      {m.displayName} ({m.modelId}){m.proOnly ? " ⭐ Pro" : ""}
                    </option>
                  ))}
                </select>
                {codexStatus.plan_type === "pro" && (
                  <p className="text-[10px] text-muted-foreground">
                    <Zap className="inline h-3 w-3 text-amber-500 mr-0.5" />
                    Pro 订阅：Spark 模型可用
                  </p>
                )}
                <Button
                  size="sm"
                  variant="outline"
                  className="h-7 text-xs w-full gap-1"
                  onClick={applyCodexPreset}
                >
                  应用所选模型
                </Button>
              </div>

              <div className="flex gap-1.5 flex-wrap">
                {codexStatus.has_refresh_token && (
                  <Button size="sm" variant="outline" className="h-7 text-xs" onClick={handleCodexRefresh} disabled={refreshingToken}>
                    {refreshingToken ? <Loader2 className="h-3 w-3 animate-spin" /> : "刷新 Token"}
                  </Button>
                )}
                <Button size="sm" variant="outline" className="h-7 text-xs text-red-600 hover:text-red-700 hover:bg-red-50 dark:hover:bg-red-950" onClick={handleCodexDisconnect} disabled={disconnecting}>
                  {disconnecting ? <Loader2 className="h-3 w-3 animate-spin" /> : "断开连接"}
                </Button>
              </div>
            </>
          )}

          {/* ── Not connected state ── */}
          {!codexLoading && codexStatus?.status !== "connected" && codexStatus?.status !== "expired" && (
            <>
              <p className="text-[11px] text-muted-foreground leading-relaxed">
                使用 ChatGPT Plus/Pro 订阅访问 Codex 模型，无需 API Key。
              </p>

              {/* OAuth PKCE 浏览器登录（主要方式） */}
              {!oauthBusy ? (
                <Button
                  size="sm"
                  className="w-full h-8 text-xs text-white gap-1.5 font-medium"
                  style={{ backgroundColor: "var(--em-primary)" }}
                  onClick={handleOAuthLogin}
                  disabled={authorizing}
                >
                  <ExternalLink className="h-3 w-3" />
                  使用 ChatGPT 账号登录
                </Button>
              ) : (
                <div className="space-y-2.5 rounded-md border border-border bg-muted/30 p-3">
                  {oauthMode === "popup" ? (
                    <div className="text-center space-y-1.5">
                      <Loader2 className="h-5 w-5 mx-auto animate-spin text-muted-foreground" />
                      <p className="text-[11px] text-muted-foreground">请在弹出窗口中完成 OpenAI 登录...</p>
                      <p className="text-[10px] text-muted-foreground">授权完成后此页面会自动更新</p>
                    </div>
                  ) : (
                    <div className="space-y-2">
                      <p className="text-[11px] text-muted-foreground leading-relaxed">
                        在弹出窗口中完成 OpenAI 登录后，请复制地址栏中的完整 URL 粘贴到下方：
                      </p>
                      <p className="text-[10px] text-amber-600 dark:text-amber-400">
                        提示：登录完成后页面可能显示无法访问，这是正常的，请直接复制地址栏 URL
                      </p>
                      <Input
                        value={pasteUrl}
                        onChange={(e) => setPasteUrl(e.target.value)}
                        className="h-8 text-xs font-mono"
                        placeholder="http://localhost:1455/auth/callback?code=...&state=..."
                        autoFocus
                      />
                      <Button
                        size="sm"
                        className="w-full h-7 text-xs text-white"
                        style={{ backgroundColor: "var(--em-primary)" }}
                        onClick={handlePasteUrlSubmit}
                        disabled={!pasteUrl.trim()}
                      >
                        确认连接
                      </Button>
                    </div>
                  )}
                  <div className="flex justify-center">
                    <button
                      type="button"
                      className="text-[11px] text-muted-foreground hover:text-foreground transition-colors"
                      onClick={cancelOAuth}
                    >
                      取消
                    </button>
                  </div>
                </div>
              )}

              {/* 备选连接方式（折叠） */}
              <div className="pt-0.5">
                <button
                  type="button"
                  onClick={() => setShowFallback(!showFallback)}
                  className="text-[11px] text-muted-foreground hover:text-foreground transition-colors"
                >
                  {showFallback ? "▾ 收起备选方式" : "▸ 其他连接方式"}
                </button>
                {showFallback && (
                  <div className="mt-2 space-y-3">
                    {/* Device Code 流程 */}
                    <div className="space-y-1.5">
                      <p className="text-[11px] font-medium text-muted-foreground">设备码登录</p>
                      {!authorizing ? (
                        <Button
                          size="sm"
                          variant="outline"
                          className="w-full h-7 text-xs"
                          onClick={handleDeviceCodeAuthorize}
                          disabled={oauthBusy}
                        >
                          使用设备码登录
                        </Button>
                      ) : (
                        <div className="space-y-2 rounded-md border border-border bg-muted/30 p-2.5">
                          <div className="text-center space-y-1.5">
                            <p className="text-[11px] text-muted-foreground">请在浏览器中打开以下链接并输入验证码：</p>
                            <a
                              href={verificationUrl}
                              target="_blank"
                              rel="noopener noreferrer"
                              className="text-[11px] font-medium underline"
                              style={{ color: "var(--em-primary)" }}
                            >
                              {verificationUrl}
                            </a>
                            <div className="flex items-center justify-center gap-2 pt-1">
                              <span
                                className="font-mono text-lg font-bold tracking-widest select-all px-2.5 py-1 rounded-md border border-border bg-background cursor-pointer"
                                title="点击复制"
                                onClick={() => navigator.clipboard.writeText(userCode)}
                              >
                                {userCode}
                              </span>
                            </div>
                            <p className="text-[10px] text-muted-foreground">点击验证码可复制</p>
                          </div>
                          <div className="flex items-center justify-center gap-2">
                            <Loader2 className="h-3 w-3 animate-spin text-muted-foreground" />
                            <span className="text-[11px] text-muted-foreground">等待授权中...</span>
                            <button
                              type="button"
                              className="text-[11px] text-muted-foreground hover:text-foreground transition-colors"
                              onClick={stopPolling}
                            >
                              取消
                            </button>
                          </div>
                        </div>
                      )}
                    </div>

                    {/* 手动粘贴 Token */}
                    <div className="space-y-1.5">
                      <button
                        type="button"
                        onClick={() => setShowManualPaste(!showManualPaste)}
                        className="text-[11px] text-muted-foreground hover:text-foreground transition-colors"
                      >
                        {showManualPaste ? "▾ 收起手动粘贴" : "▸ 手动粘贴 Token"}
                      </button>
                      {showManualPaste && (
                        <div className="mt-1.5 space-y-1.5">
                          <div className="text-[11px] text-muted-foreground space-y-0.5">
                            <p>1. 在终端运行 <code className="px-1 py-0.5 rounded bg-background font-mono">codex login</code></p>
                            <p>2. 复制 <code className="px-1 py-0.5 rounded bg-background font-mono">~/.codex/auth.json</code> 内容</p>
                            <p>3. 粘贴到下方输入框</p>
                          </div>
                          <textarea
                            value={tokenInput}
                            onChange={(e) => setTokenInput(e.target.value)}
                            className="w-full h-16 rounded-md border border-input bg-background px-2 py-1.5 text-[11px] font-mono resize-none"
                            placeholder='{"token": "eyJ...", "refresh_token": "rt_...", ...}'
                          />
                          <Button
                            size="sm"
                            variant="outline"
                            className="h-7 text-xs"
                            onClick={handleCodexTokenConnect}
                            disabled={connecting || !tokenInput.trim()}
                          >
                            {connecting ? <Loader2 className="h-3 w-3 animate-spin mr-1" /> : null}
                            粘贴连接
                          </Button>
                        </div>
                      )}
                    </div>
                  </div>
                )}
              </div>
            </>
          )}

          {codexError ? <p className="text-[11px] text-destructive">{codexError}</p> : null}
        </div>

        {/* Provider presets for quick fill */}
        <div className="mb-3">
          <p className="text-[11px] text-muted-foreground mb-1.5">常见 API Key 提供方（点击预填）</p>
          <div className="flex flex-wrap gap-1.5">
            {PROVIDER_PRESETS.map((preset) => {
              const isActive = draft.model === preset.model && draft.base_url === preset.base_url;
              return (
                <button
                  key={preset.id}
                  type="button"
                  className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] transition-colors ${
                    isActive
                      ? "border-[var(--em-primary)]/60 bg-[var(--em-primary)]/10 text-foreground"
                      : "border-border hover:border-[var(--em-primary)]/40 text-muted-foreground hover:text-foreground hover:bg-muted/40"
                  }`}
                  onClick={() => setDraft((d) => ({ ...d, model: preset.model, base_url: preset.base_url }))}
                >
                  <ProviderLogo id={preset.id} />
                  <span className="font-medium">{preset.label}</span>
                </button>
              );
            })}
          </div>
          {(() => {
            const match = PROVIDER_PRESETS.find((p) => draft.model === p.model && draft.base_url === p.base_url);
            if (!match) return null;
            return (
              <a
                href={match.purchaseUrl}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-1 text-[11px] mt-1.5 hover:underline"
                style={{ color: "var(--em-primary)" }}
              >
                前往 {match.label} 获取 API Key <ExternalLink className="h-3 w-3" />
              </a>
            );
          })()}
        </div>

        <div className="space-y-2">
          <div className="flex flex-col sm:flex-row sm:items-center gap-1 sm:gap-2">
            <label className="text-xs text-muted-foreground sm:w-16 flex-shrink-0">Model ID</label>
            <Input
              value={draft.model}
              onChange={(e) => setDraft((d) => ({ ...d, model: e.target.value }))}
              className="h-8 text-xs font-mono"
              placeholder="如: gpt-4o, qwen-plus, claude-sonnet-4 ..."
            />
          </div>
          <div className="flex flex-col sm:flex-row sm:items-center gap-1 sm:gap-2">
            <label className="text-xs text-muted-foreground sm:w-16 flex-shrink-0">Base URL</label>
            <Input
              value={draft.base_url}
              onChange={(e) => setDraft((d) => ({ ...d, base_url: e.target.value }))}
              className="h-8 text-xs font-mono"
              placeholder="https://api.openai.com/v1 ..."
            />
          </div>
          <div className="flex flex-col sm:flex-row sm:items-center gap-1 sm:gap-2">
            <label className="text-xs text-muted-foreground sm:w-16 flex-shrink-0">API Key</label>
            <div className="flex-1 relative">
              <Input
                value={draft.api_key}
                onChange={(e) => setDraft((d) => ({ ...d, api_key: e.target.value }))}
                type={showKey ? "text" : "password"}
                className="h-8 text-xs font-mono pr-8"
                placeholder="sk-..."
              />
              <button
                className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground !min-h-0 !min-w-0 h-5 w-5 flex items-center justify-center"
                onClick={() => setShowKey((v) => !v)}
              >
                {showKey ? <EyeOff className="h-3 w-3" /> : <Eye className="h-3 w-3" />}
              </button>
            </div>
          </div>
        </div>

        <div className="flex flex-col sm:flex-row justify-end gap-2 mt-3">
          {hasCustomConfig && (
            <Button
              size="sm"
              variant="outline"
              className="h-8 sm:h-7 text-xs gap-1"
              onClick={handleClear}
              disabled={clearing}
            >
              {clearing ? <Loader2 className="h-3 w-3 animate-spin" /> : <Trash2 className="h-3 w-3" />}
              清除配置
            </Button>
          )}
          <Button
            size="sm"
            className="h-8 sm:h-7 text-xs gap-1 text-white"
            style={{ backgroundColor: "var(--em-primary)" }}
            onClick={handleSave}
            disabled={saving}
          >
            {saving ? (
              <Loader2 className="h-3 w-3 animate-spin" />
            ) : saved ? (
              <CheckCircle2 className="h-3 w-3" />
            ) : (
              <Save className="h-3 w-3" />
            )}
            {saved ? "已保存" : "保存"}
          </Button>
        </div>
      </div>

      {/* 可用模型列表 */}
      <div className="rounded-lg border border-border p-4">
        <div className="flex items-center gap-2 mb-2">
          <Lock className="h-4 w-4 text-muted-foreground" />
          <h3 className="font-semibold text-sm">系统模型</h3>
        </div>
        <p className="text-xs text-muted-foreground">
          以下模型由管理员配置，您可以通过顶部模型选择器切换。
        </p>
        {user?.allowedModels && user.allowedModels.length > 0 && (
          <div className="mt-3">
            <div className="flex flex-wrap gap-1.5">
              <Badge variant="secondary" className="text-[10px]">default</Badge>
              {user.allowedModels.map((m) => (
                <Badge key={m} variant="secondary" className="text-[10px]">{m}</Badge>
              ))}
            </div>
          </div>
        )}
        {(!user?.allowedModels || user.allowedModels.length === 0) && (
          <p className="text-xs text-muted-foreground mt-2">
            您可以使用所有已配置的模型。
          </p>
        )}
      </div>
    </div>
  );
}

// ── 管理员 Codex OAuth 专用卡片 ─────────────────────────────

function CodexOAuthAdminCard({
  onProfileCreated,
  existingProfileNames,
}: {
  onProfileCreated: () => void;
  existingProfileNames: string[];
}) {
  const [status, setStatus] = useState<CodexStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  // OAuth PKCE
  const [oauthBusy, setOauthBusy] = useState(false);
  const [oauthState, setOauthState] = useState("");
  const [pasteUrl, setPasteUrl] = useState("");
  const [oauthMode, setOauthMode] = useState<"popup" | "paste" | null>(null);
  const popupRef = useRef<Window | null>(null);
  const popupTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Device Code
  const [userCode, setUserCode] = useState("");
  const [verificationUrl, setVerificationUrl] = useState("");
  const [authorizing, setAuthorizing] = useState(false);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Manual paste
  const [tokenInput, setTokenInput] = useState("");
  const [connecting, setConnecting] = useState(false);
  const [showFallback, setShowFallback] = useState(false);
  const [showManual, setShowManual] = useState(false);

  // Disconnect / Refresh
  const [disconnecting, setDisconnecting] = useState(false);
  const [refreshing, setRefreshing] = useState(false);

  // Multi-model profile management
  const [addingModel, setAddingModel] = useState<string | null>(null);
  const [removingModel, setRemovingModel] = useState<string | null>(null);
  const [codexModelsExpanded, setCodexModelsExpanded] = useState(false);

  // Build a Set of existing codex profile names for quick lookup.
  // Map legacy short names (e.g. "codex-5.3", "Codex 5.3") to current full openai-codex/xxx format.
  const _LEGACY_NAME_MAP: Record<string, string> = {
    "Codex 5.3": "openai-codex/gpt-5.3-codex",
    "codex-oauth": "openai-codex/gpt-5.3-codex",
    "codex-spark": "openai-codex/gpt-5.3-codex-spark",
    "codex-5.3": "openai-codex/gpt-5.3-codex",
    "codex-5.2": "openai-codex/gpt-5.2-codex",
    "codex-5.1": "openai-codex/gpt-5.1-codex",
    "codex-mini": "openai-codex/gpt-5.1-codex-mini",
    "codex-max": "openai-codex/gpt-5.1-codex-max",
    "codex-mini-latest": "openai-codex/codex-mini-latest",
    "codex-gpt-5.2": "openai-codex/gpt-5.2",
    "codex-gpt-5.1": "openai-codex/gpt-5.1",
  };
  const existingCodexProfiles = new Set<string>();
  for (const n of existingProfileNames) {
    const cm = CODEX_MODELS.find((m) => m.profileName === n);
    if (cm) existingCodexProfiles.add(cm.profileName);
    const mapped = _LEGACY_NAME_MAP[n];
    if (mapped) existingCodexProfiles.add(mapped);
  }
  // Check legacy short names that should be cleaned up
  const hasLegacyCodexProfile = existingProfileNames.some((n) => n in _LEGACY_NAME_MAP && !n.startsWith("openai-codex/"));

  useEffect(() => {
    let cancelled = false;
    fetchCodexStatus()
      .then((data) => { if (!cancelled) setStatus(data); })
      .catch(() => { if (!cancelled) setStatus({ status: "disconnected", provider: "openai-codex" }); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
      if (popupTimerRef.current) clearInterval(popupTimerRef.current);
    };
  }, []);

  // postMessage listener for popup auto-callback
  useEffect(() => {
    const handler = (event: MessageEvent) => {
      if (event.origin !== window.location.origin) return;
      if (event.data?.type !== "codex-oauth-callback") return;
      if (popupTimerRef.current) { clearInterval(popupTimerRef.current); popupTimerRef.current = null; }
      if (event.data.error) {
        setOauthBusy(false); setOauthMode(null); setError(event.data.error);
        return;
      }
      const { code, state: cbState } = event.data;
      if (code && cbState) {
        codexOAuthExchange(code, cbState)
          .then((result) => {
            setStatus({
              status: "connected", provider: "openai-codex",
              account_id: result.account_id, plan_type: result.plan_type,
              expires_at: result.expires_at, is_active: true, has_refresh_token: true,
            });
          })
          .catch((e: unknown) => { setError(e instanceof Error ? e.message : "OAuth 交换失败"); })
          .finally(() => { setOauthBusy(false); setOauthMode(null); setOauthState(""); });
      }
    };
    window.addEventListener("message", handler);
    return () => window.removeEventListener("message", handler);
  }, []);

  const handleOAuthLogin = useCallback(async () => {
    if (oauthBusy) return;
    setOauthBusy(true); setPasteUrl(""); setError("");
    try {
      const isLocal = ["localhost", "127.0.0.1"].includes(window.location.hostname) && window.location.port === "1455";
      const redirectUri = isLocal ? `${window.location.origin}/auth/callback` : undefined;
      const data = await codexOAuthStart(redirectUri);
      setOauthState(data.state); setOauthMode(data.mode);
      const w = 600, h = 700;
      const left = window.screenX + (window.outerWidth - w) / 2;
      const top = window.screenY + (window.outerHeight - h) / 2;
      const popup = window.open(data.authorize_url, "codex-oauth", `width=${w},height=${h},left=${left},top=${top},toolbar=no,menubar=no`);
      popupRef.current = popup;
      if (data.mode === "popup" && popup) {
        popupTimerRef.current = setInterval(() => {
          if (popup.closed) {
            if (popupTimerRef.current) { clearInterval(popupTimerRef.current); popupTimerRef.current = null; }
            setTimeout(() => { setOauthBusy((b) => { if (b) { setOauthMode(null); setOauthState(""); return false; } return b; }); }, 2000);
          }
        }, 500);
      }
    } catch (e) {
      setOauthBusy(false); setOauthMode(null);
      setError(e instanceof Error ? e.message : "无法发起 OAuth 登录");
    }
  }, [oauthBusy]);

  const handlePasteUrlSubmit = useCallback(async () => {
    if (!pasteUrl.trim() || !oauthState) return;
    setError("");
    try {
      const url = new URL(pasteUrl.trim());
      const code = url.searchParams.get("code");
      const state = url.searchParams.get("state");
      if (!code || !state) { setError("URL 中缺少 code 或 state 参数"); return; }
      const result = await codexOAuthExchange(code, state);
      setStatus({ status: "connected", provider: "openai-codex", account_id: result.account_id, plan_type: result.plan_type, expires_at: result.expires_at, is_active: true, has_refresh_token: true });
    } catch (e) { setError(e instanceof Error ? e.message : "连接失败"); }
    finally { setOauthBusy(false); setOauthMode(null); setOauthState(""); setPasteUrl(""); }
  }, [pasteUrl, oauthState]);

  const cancelOAuth = useCallback(() => {
    if (popupRef.current && !popupRef.current.closed) popupRef.current.close();
    if (popupTimerRef.current) { clearInterval(popupTimerRef.current); popupTimerRef.current = null; }
    setOauthBusy(false); setOauthMode(null); setOauthState(""); setPasteUrl(""); setError("");
  }, []);

  const stopPolling = useCallback(() => {
    if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
    setAuthorizing(false); setUserCode(""); setVerificationUrl("");
  }, []);

  const handleDeviceCode = useCallback(async () => {
    if (authorizing) return;
    setAuthorizing(true); setError("");
    try {
      const data = await codexDeviceCodeStart();
      setUserCode(data.user_code); setVerificationUrl(data.verification_url);
      const interval = Math.max(data.interval, 3) * 1000;
      pollRef.current = setInterval(async () => {
        try {
          const result = await codexDeviceCodePoll(data.state);
          if (result.status === "connected") {
            stopPolling();
            fetchCodexStatus().then(setStatus).catch(() => {});
          }
        } catch { /* 轮询错误不中断 */ }
      }, interval);
      setTimeout(() => { if (pollRef.current) { stopPolling(); setError("设备码已过期，请重试"); } }, 15 * 60 * 1000);
    } catch (e) { setAuthorizing(false); setError(e instanceof Error ? e.message : "无法发起设备码登录"); }
  }, [authorizing, stopPolling]);

  const handleManualConnect = useCallback(async () => {
    if (!tokenInput.trim() || connecting) return;
    setConnecting(true); setError("");
    try {
      const parsed = JSON.parse(tokenInput.trim());
      const result = await connectCodex(parsed);
      setStatus({ status: "connected", provider: "openai-codex", account_id: result.account_id, plan_type: result.plan_type, expires_at: result.expires_at, is_active: true, has_refresh_token: true });
      setTokenInput(""); setShowManual(false);
    } catch (e) {
      setError(e instanceof SyntaxError ? "JSON 格式无效" : (e instanceof Error ? e.message : "连接失败"));
    } finally { setConnecting(false); }
  }, [tokenInput, connecting]);

  const handleDisconnect = useCallback(async () => {
    if (disconnecting) return;
    setDisconnecting(true); setError("");
    try { await disconnectCodex(); setStatus({ status: "disconnected", provider: "openai-codex" }); }
    catch (e) { setError(e instanceof Error ? e.message : "断开失败"); }
    finally { setDisconnecting(false); }
  }, [disconnecting]);

  const handleRefresh = useCallback(async () => {
    if (refreshing) return;
    setRefreshing(true); setError("");
    try {
      const result = await refreshCodexToken();
      setStatus((prev) => prev ? { ...prev, status: "connected", expires_at: result.expires_at } : prev);
    } catch (e) { setError(e instanceof Error ? e.message : "刷新失败"); }
    finally { setRefreshing(false); }
  }, [refreshing]);

  const handleAddCodexModel = useCallback(async (entry: CodexModelEntry) => {
    setAddingModel(entry.profileName); setError("");
    try {
      await apiPost("/config/models/profiles", {
        name: entry.profileName,
        model: entry.publicId,
        api_key: "",
        base_url: CODEX_OAUTH_PRESET.base_url,
        description: `${entry.displayName} — OAuth 登录（无需 API Key）`,
        protocol: CODEX_OAUTH_PRESET.protocol,
        thinking_mode: CODEX_OAUTH_PRESET.thinking_mode,
        model_family: CODEX_OAUTH_PRESET.model_family,
        custom_extra_body: "",
        custom_extra_headers: "",
      }, { direct: true });
      onProfileCreated();
    } catch (e) { setError(e instanceof Error ? e.message : "创建档案失败"); }
    finally { setAddingModel(null); }
  }, [onProfileCreated]);

  const handleRemoveCodexModel = useCallback(async (profileName: string) => {
    setRemovingModel(profileName); setError("");
    try {
      await apiDelete(`/config/models/profiles/${profileName}`, { direct: true });
      onProfileCreated();
    } catch (e) { setError(e instanceof Error ? e.message : "删除档案失败"); }
    finally { setRemovingModel(null); }
  }, [onProfileCreated]);

  const isConnected = status?.status === "connected";
  const isExpired = status?.status === "expired";

  return (
    <div
      className={`rounded-lg border px-2.5 py-2.5 space-y-2 ${
        isConnected
          ? "border-green-500/40 bg-green-500/5"
          : "border-[var(--em-primary)]/30 bg-[var(--em-primary)]/5"
      }`}
    >
      {/* Header */}
      <div className="flex items-center gap-1.5">
        <ProviderLogo id={CODEX_OAUTH_PRESET.id} />
        <p className="text-xs font-semibold">GPT Codex 订阅登录</p>
        <Badge variant="secondary" className="text-[10px] ml-auto">
          {loading ? "检测中" : isConnected ? "已连接" : isExpired ? "已过期" : "未连接"}
        </Badge>
      </div>

      {loading && (
        <div className="flex items-center justify-center py-2">
          <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
        </div>
      )}

      {/* ── Connected / Expired ── */}
      {!loading && (isConnected || isExpired) && (
        <>
          <div className="flex items-center gap-2">
            <div className={`h-2 w-2 rounded-full ${isExpired ? "bg-amber-500" : "bg-green-500"}`} />
            <span className="text-[11px] font-medium">{isExpired ? "Token 已过期" : "已连接"}</span>
          </div>
          <div className="grid grid-cols-2 gap-y-1 text-[11px]">
            {status?.account_id && (<><span className="text-muted-foreground">账户</span><span className="font-mono truncate">{status.account_id}</span></>)}
            {status?.plan_type && (<><span className="text-muted-foreground">订阅</span><span className="capitalize">{status.plan_type}</span></>)}
            {status?.expires_at && (<><span className="text-muted-foreground">有效期至</span><span>{new Date(status.expires_at).toLocaleString("zh-CN")}</span></>)}
          </div>

          {/* 模型列表 — 多模型添加 */}
          <div className="space-y-1">
            <button
              type="button"
              className="w-full flex items-center gap-1 text-left rounded-md border border-border/60 px-2 py-1 hover:bg-muted/40 transition-colors"
              onClick={() => setCodexModelsExpanded((v) => !v)}
            >
              {codexModelsExpanded ? (
                <ChevronDown className="h-3 w-3 text-muted-foreground" />
              ) : (
                <ChevronRight className="h-3 w-3 text-muted-foreground" />
              )}
              <span className="text-[10px] text-muted-foreground">可用 Codex 模型（点击展开）</span>
              <Badge variant="secondary" className="text-[9px] ml-auto">
                {existingCodexProfiles.size}/{CODEX_MODELS.length}
              </Badge>
            </button>
            {codexModelsExpanded && (
              <>
                <p className="text-[10px] text-muted-foreground">点击添加/移除</p>
                <div className="space-y-1">
                  {CODEX_MODELS.map((m) => {
                    const isProLocked = m.proOnly && status?.plan_type !== "pro";
                    const isAdded = existingCodexProfiles.has(m.profileName);
                    const isBusy = addingModel === m.profileName || removingModel === m.profileName;
                    return (
                      <div
                        key={m.profileName}
                        className={`flex items-center gap-2 rounded-md border px-2 py-1 text-[11px] transition-colors ${
                          isProLocked
                            ? "border-border/50 bg-muted/30 opacity-50"
                            : isAdded
                              ? "border-green-500/40 bg-green-500/5"
                              : "border-border hover:border-[var(--em-primary)]/40"
                        }`}
                      >
                        <span className="font-medium truncate min-w-0 flex-1">{m.displayName}</span>
                        <code className="text-[9px] font-mono text-muted-foreground hidden sm:inline truncate max-w-[30%]">{m.modelId}</code>
                        {m.proOnly && <Badge variant="secondary" className="text-[9px] shrink-0">Pro</Badge>}
                        {isProLocked ? (
                          <Lock className="h-3 w-3 text-muted-foreground shrink-0" />
                        ) : isAdded ? (
                          <Button
                            size="sm"
                            variant="ghost"
                            className="h-5 px-1.5 text-[10px] text-red-600 hover:text-red-700 hover:bg-red-50 dark:hover:bg-red-950 shrink-0"
                            onClick={() => handleRemoveCodexModel(m.profileName)}
                            disabled={isBusy}
                          >
                            {isBusy ? <Loader2 className="h-3 w-3 animate-spin" /> : "移除"}
                          </Button>
                        ) : (
                          <Button
                            size="sm"
                            variant="ghost"
                            className="h-5 px-1.5 text-[10px] shrink-0"
                            style={{ color: "var(--em-primary)" }}
                            onClick={() => handleAddCodexModel(m)}
                            disabled={isBusy}
                          >
                            {isBusy ? <Loader2 className="h-3 w-3 animate-spin" /> : "添加"}
                          </Button>
                        )}
                      </div>
                    );
                  })}
                </div>
                {hasLegacyCodexProfile && (
                  <p className="text-[10px] text-amber-600 dark:text-amber-400 mt-1">
                    检测到旧版 codex-oauth 档案，建议删除后使用上方按钮重新添加
                  </p>
                )}
              </>
            )}
          </div>

          <div className="flex gap-1.5 flex-wrap">
            {status?.has_refresh_token && (
              <Button size="sm" variant="outline" className="h-6 text-[10px]" onClick={handleRefresh} disabled={refreshing}>
                {refreshing ? <Loader2 className="h-3 w-3 animate-spin" /> : "刷新 Token"}
              </Button>
            )}
            <Button size="sm" variant="outline" className="h-6 text-[10px] text-red-600 hover:text-red-700 hover:bg-red-50 dark:hover:bg-red-950" onClick={handleDisconnect} disabled={disconnecting}>
              {disconnecting ? <Loader2 className="h-3 w-3 animate-spin" /> : "断开连接"}
            </Button>
          </div>
        </>
      )}

      {/* ── Not connected ── */}
      {!loading && !isConnected && !isExpired && (
        <>
          <p className="text-[11px] text-muted-foreground leading-relaxed">
            使用 ChatGPT Plus/Pro 订阅，无需 API Key。登录后自动创建模型档案。
          </p>

          {!oauthBusy ? (
            <Button
              size="sm"
              className="w-full h-8 text-xs text-white font-medium gap-1.5"
              style={{ backgroundColor: "var(--em-primary)" }}
              onClick={handleOAuthLogin}
              disabled={authorizing}
            >
              <ExternalLink className="h-3 w-3" />
              使用 ChatGPT 账号登录
            </Button>
          ) : (
            <div className="space-y-2 rounded-md border border-border bg-muted/30 p-2.5">
              {oauthMode === "popup" ? (
                <div className="text-center space-y-1">
                  <Loader2 className="h-5 w-5 mx-auto animate-spin text-muted-foreground" />
                  <p className="text-[11px] text-muted-foreground">请在弹出窗口中完成 OpenAI 登录...</p>
                  <p className="text-[10px] text-muted-foreground">授权完成后自动更新</p>
                </div>
              ) : (
                <div className="space-y-1.5">
                  <p className="text-[11px] text-muted-foreground">登录完成后，复制地址栏 URL 粘贴到下方：</p>
                  <p className="text-[10px] text-amber-600 dark:text-amber-400">提示：页面可能显示无法访问，直接复制地址栏 URL 即可</p>
                  <Input value={pasteUrl} onChange={(e) => setPasteUrl(e.target.value)} className="h-7 text-[11px] font-mono" placeholder="http://localhost:1455/auth/callback?code=...&state=..." autoFocus />
                  <Button size="sm" className="w-full h-7 text-[11px] text-white" style={{ backgroundColor: "var(--em-primary)" }} onClick={handlePasteUrlSubmit} disabled={!pasteUrl.trim()}>
                    确认连接
                  </Button>
                </div>
              )}
              <div className="flex justify-center">
                <button type="button" className="text-[10px] text-muted-foreground hover:text-foreground transition-colors" onClick={cancelOAuth}>取消</button>
              </div>
            </div>
          )}

          {/* 备选方式 */}
          <div>
            <button type="button" onClick={() => setShowFallback(!showFallback)} className="text-[10px] text-muted-foreground hover:text-foreground transition-colors">
              {showFallback ? "▾ 收起备选方式" : "▸ 其他连接方式"}
            </button>
            {showFallback && (
              <div className="mt-1.5 space-y-2.5">
                {/* Device Code */}
                <div className="space-y-1">
                  <p className="text-[10px] font-medium text-muted-foreground">设备码登录</p>
                  {!authorizing ? (
                    <Button size="sm" variant="outline" className="w-full h-7 text-[11px]" onClick={handleDeviceCode} disabled={oauthBusy}>使用设备码登录</Button>
                  ) : (
                    <div className="space-y-1.5 rounded-md border border-border bg-muted/30 p-2">
                      <p className="text-[10px] text-muted-foreground text-center">打开链接并输入验证码：</p>
                      <a href={verificationUrl} target="_blank" rel="noopener noreferrer" className="block text-[10px] font-medium underline text-center" style={{ color: "var(--em-primary)" }}>{verificationUrl}</a>
                      <div className="flex justify-center">
                        <span className="font-mono text-base font-bold tracking-widest select-all px-2 py-0.5 rounded border border-border bg-background cursor-pointer" onClick={() => navigator.clipboard.writeText(userCode)}>{userCode}</span>
                      </div>
                      <div className="flex items-center justify-center gap-2">
                        <Loader2 className="h-3 w-3 animate-spin text-muted-foreground" />
                        <span className="text-[10px] text-muted-foreground">等待授权...</span>
                        <button type="button" className="text-[10px] text-muted-foreground hover:text-foreground" onClick={stopPolling}>取消</button>
                      </div>
                    </div>
                  )}
                </div>
                {/* Manual paste */}
                <div>
                  <button type="button" onClick={() => setShowManual(!showManual)} className="text-[10px] text-muted-foreground hover:text-foreground transition-colors">
                    {showManual ? "▾ 收起手动粘贴" : "▸ 手动粘贴 Token"}
                  </button>
                  {showManual && (
                    <div className="mt-1 space-y-1">
                      <div className="text-[10px] text-muted-foreground space-y-0.5">
                        <p>1. 运行 <code className="px-0.5 rounded bg-background font-mono">codex login</code></p>
                        <p>2. 复制 <code className="px-0.5 rounded bg-background font-mono">~/.codex/auth.json</code></p>
                      </div>
                      <textarea value={tokenInput} onChange={(e) => setTokenInput(e.target.value)} className="w-full h-14 rounded-md border border-input bg-background px-2 py-1 text-[10px] font-mono resize-none" placeholder='{"token":"...","refresh_token":"..."}' />
                      <Button size="sm" variant="outline" className="h-6 text-[10px]" onClick={handleManualConnect} disabled={connecting || !tokenInput.trim()}>
                        {connecting ? <Loader2 className="h-3 w-3 animate-spin mr-1" /> : null}粘贴连接
                      </Button>
                    </div>
                  )}
                </div>
              </div>
            )}
          </div>
        </>
      )}

      {error ? <p className="text-[10px] text-destructive">{error}</p> : null}
    </div>
  );
}

// ── 管理员模型配置面板 ─────────────────────────────────────

type AuthUser = NonNullable<ReturnType<typeof useAuthStore.getState>["user"]>;

export function ModelTab() {
  const [config, setConfig] = useState<ModelConfig | null>(null);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState<string | null>(null);
  const [saved, setSaved] = useState<string | null>(null);
  const [editDrafts, setEditDrafts] = useState<Record<string, Record<string, string>>>({});
  const [showKeys, setShowKeys] = useState<Record<string, boolean>>({});
  const [enabledDrafts, setEnabledDrafts] = useState<Record<string, boolean>>({});
  const [newProfile, setNewProfile] = useState(false);
  const [editingProfile, setEditingProfile] = useState<string | null>(null);
  const [profileError, setProfileError] = useState<string | null>(null);
  const [highlightProfile, setHighlightProfile] = useState<string | null>(null);
  const [addingProfile, setAddingProfile] = useState(false);
  const [fetchingModels, setFetchingModels] = useState(false);
  const [remoteModels, setRemoteModels] = useState<RemoteModelItem[]>([]);
  const [modelDropdownTarget, setModelDropdownTarget] = useState<string | null>(null);
  const [remoteModelError, setRemoteModelError] = useState<string | null>(null);
  const modelDropdownRef = useRef<HTMLDivElement>(null);
  const profileCardRefs = useRef<Record<string, HTMLDivElement | null>>({});
  const [profileDraft, setProfileDraft] = useState<ProfileEntry>({
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
  });
  // 按 "model|base_url" 或 profile 名索引的每模型能力
  const [capsMap, setCapsMap] = useState<Record<string, ModelCapabilities>>({});
  const [probingKey, setProbingKey] = useState<string | null>(null);
  const [probingAll, setProbingAll] = useState(false);
  // 快速应用 profile 到角色
  const [applyingProfile, setApplyingProfile] = useState<string | null>(null);
  const [applyMenuOpen, setApplyMenuOpen] = useState<string | null>(null);
  const [applyToast, setApplyToast] = useState<{ msg: string; type: "success" | "error" } | null>(null);

  // 折叠/展开状态
  const [expandedSections, setExpandedSections] = useState<Record<string, boolean>>({});
  const toggleSection = useCallback((key: string) => {
    setExpandedSections((prev) => ({ ...prev, [key]: !prev[key] }));
  }, []);

  // 底部高级区 pills
  const [advancedPill, setAdvancedPill] = useState<"capabilities" | "thinking" | "transfer">("capabilities");

  // 连通测试状态
  const [testingKey, setTestingKey] = useState<string | null>(null);
  const [testResult, setTestResult] = useState<Record<string, TestConnectionResult | null>>({});

  // Thinking 配置
  const [thinkingEffort, setThinkingEffort] = useState<string>("medium");
  const [thinkingBudget, setThinkingBudget] = useState<string>("");
  const [thinkingEffectiveBudget, setThinkingEffectiveBudget] = useState<number>(0);
  const [thinkingSaving, setThinkingSaving] = useState(false);
  const [thinkingSaved, setThinkingSaved] = useState(false);

  const user = useAuthStore((s) => s.user);
  const authEnabled = useAuthConfigStore((s) => s.authEnabled);
  const isAdmin = !authEnabled || !user || user.role === "admin";

  const formRef = useRef<HTMLDivElement>(null);
  const [pendingScrollToForm, setPendingScrollToForm] = useState(0);
  const scrollToForm = useCallback(() => setPendingScrollToForm((n) => n + 1), []);
  useEffect(() => {
    if (!pendingScrollToForm) return;
    // 状态变更后等待表单挂载再滚动
    requestAnimationFrame(() => {
      formRef.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
    });
  }, [pendingScrollToForm]);

  const applyPresetToProfileDraft = useCallback((preset: ProviderPreset, customName?: string, customDescription?: string) => {
    setNewProfile(true);
    setEditingProfile(null);
    setProfileDraft({
      name: customName || preset.label,
      model: preset.model,
      api_key: "",
      base_url: preset.base_url,
      description: customDescription || preset.description,
      protocol: preset.protocol,
      thinking_mode: preset.thinking_mode,
      model_family: preset.model_family,
      custom_extra_body: "",
      custom_extra_headers: "",
    });
    scrollToForm();
  }, [scrollToForm]);

  const fetchThinkingConfig = useCallback(async (force = false) => {
    if (!force) {
      const cached = settingsCache.get<{ effort: string; budget: number; effective_budget: number }>("/thinking");
      if (cached) {
        setThinkingEffort(cached.effort);
        setThinkingBudget(cached.budget > 0 ? String(cached.budget) : "");
        setThinkingEffectiveBudget(cached.effective_budget);
        return;
      }
    }
    try {
      const data = await apiGet<{ effort: string; budget: number; effective_budget: number }>("/thinking", { direct: true });
      settingsCache.set("/thinking", data);
      setThinkingEffort(data.effort);
      setThinkingBudget(data.budget > 0 ? String(data.budget) : "");
      setThinkingEffectiveBudget(data.effective_budget);
    } catch {
      // 后端未就绪
    }
  }, []);

  const handleSaveThinking = useCallback(async (effort: string, budgetStr: string) => {
    setThinkingSaving(true);
    try {
      const body: Record<string, unknown> = { effort };
      const budgetNum = parseInt(budgetStr, 10);
      if (!isNaN(budgetNum) && budgetNum >= 0) {
        body.budget = budgetNum;
      } else {
        body.budget = 0;
      }
      const data = await apiPut<{ effort: string; budget: number; effective_budget: number }>("/thinking", body, { direct: true });
      settingsCache.set("/thinking", data);
      setThinkingEffort(data.effort);
      setThinkingBudget(data.budget > 0 ? String(data.budget) : "");
      setThinkingEffectiveBudget(data.effective_budget);
      setThinkingSaved(true);
      setTimeout(() => setThinkingSaved(false), 2000);
    } catch {
      // 忽略
    } finally {
      setThinkingSaving(false);
    }
  }, []);

  const fetchAllCapabilities = useCallback(async (force = false) => {
    if (!force) {
      const cached = settingsCache.get<Record<string, ModelCapabilities>>("_capsMap");
      if (cached) {
        const normalized: Record<string, ModelCapabilities> = {};
        for (const [name, caps] of Object.entries(cached)) {
          normalized[name] = normalizeFetchedCapabilities(caps);
        }
        setCapsMap(normalized);
        return;
      }
    }
    try {
      const data = await apiGet<{ items: { name: string; model: string; base_url: string; capabilities: ModelCapabilities | null }[] }>("/config/models/capabilities/all", { direct: true });
      const map: Record<string, ModelCapabilities> = {};
      for (const item of data.items) {
        if (item.capabilities) {
          map[item.name] = normalizeFetchedCapabilities(item.capabilities);
        }
      }
      settingsCache.set("_capsMap", map);
      setCapsMap(map);
    } catch {
      // 后端未就绪
    }
  }, []);

  const handleProbeOne = useCallback(async (profileName: string) => {
    setProbingKey(profileName);
    try {
      const body: Record<string, string> = { name: profileName };
      const data = await apiPost<{ capabilities: ModelCapabilities }>("/config/models/capabilities/probe", body, { direct: true });
      setCapsMap((prev) => {
        const next = { ...prev, [profileName]: data.capabilities };
        settingsCache.set("_capsMap", next);
        return next;
      });
    } catch {
      // 忽略
    } finally {
      setProbingKey(null);
    }
  }, []);

  const handleProbeAll = useCallback(async () => {
    setProbingAll(true);
    try {
      const data = await apiPost<{ results: { name: string; capabilities?: ModelCapabilities }[] }>("/config/models/capabilities/probe-all", {}, { direct: true });
      setCapsMap((prev) => {
        const next = { ...prev };
        for (const r of data.results) {
          if (r.capabilities) next[r.name] = r.capabilities;
        }
        settingsCache.set("_capsMap", next);
        return next;
      });
    } catch {
      // 忽略
    } finally {
      setProbingAll(false);
    }
  }, []);

  const handleTestConnection = useCallback(async (key: string, opts: { name?: string; model?: string; base_url?: string; api_key?: string }) => {
    setTestingKey(key);
    setTestResult((prev) => ({ ...prev, [key]: null }));
    try {
      const result = await testModelConnection(opts);
      setTestResult((prev) => ({ ...prev, [key]: result }));
    } catch (e) {
      setTestResult((prev) => ({ ...prev, [key]: { ok: false, error: e instanceof Error ? e.message : "测试失败", model: opts.model || "" } }));
    } finally {
      setTestingKey(null);
    }
  }, []);

  const handleFetchRemoteModels = useCallback(async (target: string, baseUrl?: string, apiKey?: string, protocol?: string) => {
    setFetchingModels(true);
    setRemoteModelError(null);
    setRemoteModels([]);
    setModelDropdownTarget(null);
    try {
      const result = await listRemoteModels({
        base_url: baseUrl || undefined,
        api_key: apiKey || undefined,
        protocol: protocol || undefined,
      });
      if (result.error) {
        setRemoteModelError(result.error);
      } else if (result.models.length === 0) {
        setRemoteModelError("未检测到可用模型");
      } else {
        setRemoteModels(result.models);
        setModelDropdownTarget(target);
      }
    } catch (e) {
      setRemoteModelError(e instanceof Error ? e.message : "检测失败");
    } finally {
      setFetchingModels(false);
    }
  }, []);

  // 点击外部关闭模型下拉
  useEffect(() => {
    if (!modelDropdownTarget) return;
    const handler = (e: MouseEvent) => {
      if (modelDropdownRef.current && !modelDropdownRef.current.contains(e.target as Node)) {
        setModelDropdownTarget(null);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [modelDropdownTarget]);

  const handleCapToggle = useCallback(async (profileName: string, model: string, base_url: string, field: string, value: boolean) => {
    try {
      const data = await apiPut<{ capabilities: ModelCapabilities | null }>("/config/models/capabilities", {
        model,
        base_url,
        overrides: { [field]: value },
      }, { direct: true });
      if (data.capabilities) {
        setCapsMap((prev) => {
          const next = { ...prev, [profileName]: data.capabilities! };
          settingsCache.set("_capsMap", next);
          return next;
        });
      }
    } catch {
      // 忽略
    }
  }, []);

  const applyConfigData = useCallback((data: ModelConfig) => {
    setConfig(data);
    const drafts: Record<string, Record<string, string>> = {};
    for (const section of SECTION_META) {
      const sectionData = data[section.key as keyof ModelConfig] as ModelSection;
      drafts[section.key] = {};
      for (const field of section.fields) {
        drafts[section.key][field] = (sectionData as Record<string, string>)?.[field] || "";
      }
      drafts[section.key]["protocol"] = sectionData?.protocol || "auto";
    }
    setEditDrafts(drafts);
    setEnabledDrafts({
      aux: data.aux?.enabled !== false,
      vlm: data.vlm?.enabled !== false,
      embedding: data.embedding?.enabled === true,
    });
  }, []);

  const fetchConfig = useCallback(async (force = false) => {
    if (!force) {
      const cached = settingsCache.get<ModelConfig>("/config/models");
      if (cached) { applyConfigData(cached); return; }
    }
    // 仅首次加载时展示 loading 旋转，force 刷新（如保存后）静默更新避免闪屏
    if (!force) setLoading(true);
    try {
      const data = await apiGet<ModelConfig>("/config/models", { direct: true });
      settingsCache.set("/config/models", data);
      applyConfigData(data);
    } catch {
      // 后端未就绪
    } finally {
      setLoading(false);
    }
  }, [applyConfigData]);

  useEffect(() => {
    if (isAdmin) {
      fetchConfig();
      // 强制刷新能力探测结果，避免 Tab 切换/重进设置页时沿用旧的失败提示
      fetchAllCapabilities(true);
      fetchThinkingConfig();
    }
  }, [fetchConfig, fetchAllCapabilities, fetchThinkingConfig, isAdmin]);

  // 自动消失 applyToast
  useEffect(() => {
    if (!applyToast) return;
    const t = setTimeout(() => setApplyToast(null), 3000);
    return () => clearTimeout(t);
  }, [applyToast]);

  const handleApplyProfileToRole = useCallback(async (profile: ProfileEntry, role: "main" | "aux" | "vlm") => {
    const roleLabel = role === "main" ? "主模型" : role === "aux" ? "辅助模型" : "视觉模型";
    setApplyingProfile(profile.name);
    setApplyMenuOpen(null);

    try {
      // 视觉模型赋值前检查视觉能力
      if (role === "vlm") {
        let caps = capsMap[profile.name];
        // 尚未探测过则先 probe
        if (!caps || caps.supports_vision === null) {
          try {
            const probeData = await apiPost<{ capabilities: ModelCapabilities }>("/config/models/capabilities/probe", { name: profile.name }, { direct: true });
            caps = probeData.capabilities;
            setCapsMap((prev) => {
              const next = { ...prev, [profile.name]: caps! };
              settingsCache.set("_capsMap", next);
              return next;
            });
          } catch {
            // probe 失败不阻塞，继续应用
          }
        }
        if (caps && caps.supports_vision === false) {
          const ok = window.confirm(
            `模型 "${profile.model}" 不支持视觉能力。\n仍然要将其设为视觉模型吗？`
          );
          if (!ok) {
            setApplyingProfile(null);
            return;
          }
        }
      }

      const body: Record<string, unknown> = {
        model: profile.model,
        base_url: profile.base_url || undefined,
        api_key: profile.api_key || undefined,
        protocol: profile.protocol || "auto",
      };
      // aux/vlm 同时确保 enabled
      if (role === "aux" || role === "vlm") {
        body.enabled = true;
      }
      await apiPut(`/config/models/${role}`, body, { direct: true });
      setApplyToast({ msg: `已将 "${profile.name}" 应用为${roleLabel}`, type: "success" });
      fetchConfig(true);
      fetchAllCapabilities(true);
    } catch (e) {
      setApplyToast({ msg: e instanceof Error ? e.message : `应用为${roleLabel}失败`, type: "error" });
    } finally {
      setApplyingProfile(null);
    }
  }, [capsMap, fetchConfig, fetchAllCapabilities]);

  const handleSaveSection = async (sectionKey: string) => {
    setSaving(sectionKey);
    try {
      const draft = editDrafts[sectionKey];
      const body: Record<string, unknown> = {};
      for (const [field, value] of Object.entries(draft)) {
        if (field === "api_key" && isMaskedApiKey(value)) continue;
        if (field === "protocol" && sectionKey === "embedding") continue;
        body[field] = value;
      }
      // aux/vlm/embedding 保存时一并提交 enabled 开关
      if ((sectionKey === "aux" || sectionKey === "vlm" || sectionKey === "embedding") && enabledDrafts[sectionKey] !== undefined) {
        body.enabled = enabledDrafts[sectionKey];
      }
      await apiPut(`/config/models/${sectionKey}`, body, { direct: true });
      setSaved(sectionKey);
      setTimeout(() => setSaved(null), 2000);
      fetchConfig(true);
    } catch {
      // 忽略
    } finally {
      setSaving(null);
    }
  };

  const handleToggleEnabled = async (sectionKey: string, checked: boolean) => {
    setEnabledDrafts((prev) => ({ ...prev, [sectionKey]: checked }));
    // 立即保存开关状态
    try {
      await apiPut(`/config/models/${sectionKey}`, { enabled: checked }, { direct: true });
      fetchConfig(true);
    } catch {
      // 回滚
      setEnabledDrafts((prev) => ({ ...prev, [sectionKey]: !checked }));
    }
  };

  const handleAddProfile = async () => {
    setProfileError(null);
    setAddingProfile(true);
    const newName = profileDraft.name;
    try {
      await apiPost("/config/models/profiles", profileDraft, { direct: true });
      setNewProfile(false);
      setEditingProfile(null);
      setProfileDraft({ name: "", model: "", api_key: "", base_url: "", description: "", protocol: "auto", thinking_mode: "auto", model_family: "", custom_extra_body: "", custom_extra_headers: "" });
      setTestResult((prev) => ({ ...prev, _profile_form: null }));
      await fetchConfig(true);
      useUIStore.getState().bumpModelProfiles();
      // 滚动到新卡片并高亮
      setHighlightProfile(newName);
      setTimeout(() => setHighlightProfile(null), 2000);
      requestAnimationFrame(() => {
        profileCardRefs.current[newName]?.scrollIntoView({ behavior: "smooth", block: "nearest" });
      });
    } catch (e) {
      setProfileError(e instanceof Error ? e.message : "添加失败，请检查网络或参数");
    } finally {
      setAddingProfile(false);
    }
  };

  const handleUpdateProfile = async (originalName: string) => {
    setProfileError(null);
    const updatedName = profileDraft.name;
    try {
      await apiPut(`/config/models/profiles/${originalName}`, profileDraft, { direct: true });
      setEditingProfile(null);
      setNewProfile(false);
      setProfileDraft({ name: "", model: "", api_key: "", base_url: "", description: "", protocol: "auto", thinking_mode: "auto", model_family: "", custom_extra_body: "", custom_extra_headers: "" });
      setTestResult((prev) => ({ ...prev, _profile_form: null }));
      await fetchConfig(true);
      useUIStore.getState().bumpModelProfiles();
      // 滚动到更新后的卡片并高亮
      setHighlightProfile(updatedName);
      setTimeout(() => setHighlightProfile(null), 2000);
      requestAnimationFrame(() => {
        profileCardRefs.current[updatedName]?.scrollIntoView({ behavior: "smooth", block: "nearest" });
      });
    } catch (e) {
      setProfileError(e instanceof Error ? e.message : "更新失败，请检查网络或参数");
    }
  };

  const handleDeleteProfile = async (name: string) => {
    setProfileError(null);
    const currentProfiles = config?.profiles || [];
    const removedIndex = currentProfiles.findIndex((p) => p.name === name);
    const removedProfile = removedIndex >= 0 ? currentProfiles[removedIndex] : null;

    // 乐观删除：先从前端列表移除，避免等待网络请求。
    if (removedProfile) {
      setConfig((prev) => {
        if (!prev) return prev;
        const next = { ...prev, profiles: prev.profiles.filter((p) => p.name !== name) };
        settingsCache.set("/config/models", next);
        return next;
      });
      setApplyMenuOpen((prev) => (prev === name ? null : prev));
      useUIStore.getState().bumpModelProfiles();
    }

    try {
      await apiDelete(`/config/models/profiles/${name}`, { direct: true });
      // 如果正在编辑被删除的 profile，关闭表单
      if (editingProfile === name) {
        setEditingProfile(null);
        setNewProfile(false);
        setProfileDraft({ name: "", model: "", api_key: "", base_url: "", description: "", protocol: "auto", thinking_mode: "auto", model_family: "", custom_extra_body: "", custom_extra_headers: "" });
      }
      // 同步清理缓存中的能力结果，避免保留无效项。
      setCapsMap((prev) => {
        if (!prev[name]) return prev;
        const next = { ...prev };
        delete next[name];
        settingsCache.set("_capsMap", next);
        return next;
      });
    } catch (e) {
      // 删除失败回滚：恢复被删条目到原位置。
      if (removedProfile) {
        setConfig((prev) => {
          if (!prev) return prev;
          if (prev.profiles.some((p) => p.name === name)) return prev;
          const nextProfiles = [...prev.profiles];
          const insertAt = Math.min(Math.max(removedIndex, 0), nextProfiles.length);
          nextProfiles.splice(insertAt, 0, removedProfile);
          const next = { ...prev, profiles: nextProfiles };
          settingsCache.set("/config/models", next);
          return next;
        });
        useUIStore.getState().bumpModelProfiles();
      }
      setProfileError(e instanceof Error ? e.message : "删除失败");
    }
  };

  const updateDraft = (section: string, field: string, value: string) => {
    setEditDrafts((prev) => ({
      ...prev,
      [section]: { ...prev[section], [field]: value },
    }));
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center py-12">
        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
      </div>
    );
  }

  if (!isAdmin) {
    return <UserApiConfigPanel user={user} />;
  }

  return (
    <div className="flex flex-col gap-2">
        {/* ── Apply toast ── */}
        {applyToast && (
          <div
            className={`flex items-center gap-2 px-3 py-2 rounded-lg text-xs font-medium transition-opacity ${
              applyToast.type === "success"
                ? "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border border-emerald-500/20"
                : "bg-destructive/10 text-destructive border border-destructive/20"
            }`}
          >
            {applyToast.type === "success" ? <CheckCircle2 className="h-3.5 w-3.5 shrink-0" /> : <AlertTriangle className="h-3.5 w-3.5 shrink-0" />}
            <span className="flex-1">{applyToast.msg}</span>
            <button className="shrink-0 hover:opacity-70" onClick={() => setApplyToast(null)}>
              <X className="h-3 w-3" />
            </button>
          </div>
        )}
        {/* ── Collapsible model endpoint cards ── */}
        <div className="space-y-2 order-2">
        {SECTION_META.map((section) => {
          const sectionCaps = capsMap[section.key];
          const isExpanded = !!expandedSections[section.key];
          const modelId = editDrafts[section.key]?.model || (config?.[section.key as keyof ModelConfig] as ModelSection)?.model || "";
          const isDisabled = (section.key === "aux" || section.key === "vlm" || section.key === "embedding") && enabledDrafts[section.key] === false;
          return (
          <div key={section.key} className={`rounded-lg border transition-colors ${
            isModelUnhealthy(sectionCaps)
              ? "border-destructive/40 bg-destructive/5"
              : "border-border"
          }`}>
            {/* ── Collapsed summary row (always visible) ── */}
            <div
              role="button"
              tabIndex={0}
              className="w-full flex items-center gap-2 px-3 py-2.5 text-left hover:bg-muted/30 transition-colors rounded-lg overflow-hidden cursor-pointer"
              onClick={() => toggleSection(section.key)}
              onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); toggleSection(section.key); } }}
            >
              <span className="text-muted-foreground transition-transform flex-shrink-0" style={{ transform: isExpanded ? "rotate(90deg)" : "rotate(0deg)" }}>
                <ChevronRight className="h-3.5 w-3.5" />
              </span>
              <span className="flex-shrink-0" style={{ color: isModelUnhealthy(sectionCaps) ? "var(--destructive, #ef4444)" : "var(--em-primary)" }}>{section.icon}</span>
              <span className="font-semibold text-sm whitespace-nowrap">{section.label}</span>
              {(section.key === "aux" || section.key === "vlm" || section.key === "embedding") && (
                <Switch
                  checked={section.key === "embedding" ? enabledDrafts[section.key] === true : enabledDrafts[section.key] !== false}
                  onCheckedChange={(checked) => { handleToggleEnabled(section.key, checked); }}
                  onClick={(e) => e.stopPropagation()}
                  className="ml-0.5 scale-75 origin-left"
                />
              )}
              {modelId && (
                <Badge variant="secondary" className="text-[10px] font-mono max-w-[30%] sm:max-w-[40%] truncate">
                  {modelId}
                </Badge>
              )}
              {isModelUnhealthy(sectionCaps) ? (
                <span className="inline-flex items-center gap-1 text-destructive text-[10px] shrink-0">
                  <AlertTriangle className="h-2.5 w-2.5" />
                  <span className="hidden sm:inline">连接失败</span>
                </span>
              ) : (
                <span className="hidden sm:inline-flex"><CapabilityBadges caps={sectionCaps ?? null} /></span>
              )}
              {isDisabled && (
                <span className="text-[10px] text-amber-600 dark:text-amber-400 shrink-0">已禁用</span>
              )}
            </div>

            {/* ── Expanded edit form ── */}
            {isExpanded && (
              <div className="px-4 pb-4 pt-1 border-t border-border/50">
                {isModelUnhealthy(sectionCaps) && (
                  <div className="mb-2 flex items-center gap-1.5 text-destructive">
                    <AlertTriangle className="h-3 w-3 flex-shrink-0" />
                    <span className="text-[11px] truncate" title={getHealthError(sectionCaps)}>
                      连接失败: {getHealthError(sectionCaps) || "模型不可达"}
                    </span>
                  </div>
                )}
                {isDisabled && (
                  <p className="text-xs text-amber-600 dark:text-amber-400 mb-2">{section.key === "embedding" ? "已禁用，语义检索功能关闭" : "已禁用，将回退到主模型"}</p>
                )}
                <div className={`space-y-2 transition-opacity ${isDisabled ? "opacity-40 pointer-events-none" : ""}`}>
                  {section.fields.map((field) => (
                    <div key={field} className="flex flex-col sm:flex-row sm:items-center gap-1 sm:gap-2">
                      <label className="text-xs text-muted-foreground sm:w-16 flex-shrink-0">
                        {FIELD_LABELS[field]}
                      </label>
                      <div className="flex-1 relative">
                        {field === "model" ? (
                          <div ref={modelDropdownTarget === section.key ? modelDropdownRef : undefined}>
                            <div className="flex gap-1">
                              <Input
                                value={editDrafts[section.key]?.[field] || ""}
                                onChange={(e) => {
                                  updateDraft(section.key, field, e.target.value);
                                  if (remoteModels.length > 0 && modelDropdownTarget === section.key) setModelDropdownTarget(section.key);
                                }}
                                onFocus={() => { if (remoteModels.length > 0 && modelDropdownTarget === section.key) setModelDropdownTarget(section.key); }}
                                className="h-8 text-xs font-mono flex-1"
                                placeholder={`输入 ${FIELD_LABELS[field]}...`}
                              />
                              <Button
                                type="button"
                                size="sm"
                                variant="outline"
                                className="h-8 px-2 shrink-0"
                                title="从 API 自动检测可用模型"
                                disabled={fetchingModels}
                                onClick={() => handleFetchRemoteModels(
                                  section.key,
                                  editDrafts[section.key]?.base_url || undefined,
                                  isMaskedApiKey(editDrafts[section.key]?.api_key || "") ? undefined : editDrafts[section.key]?.api_key || undefined,
                                  editDrafts[section.key]?.protocol || undefined,
                                )}
                              >
                                {fetchingModels ? (
                                  <Loader2 className="h-3 w-3 animate-spin" />
                                ) : (
                                  <Search className="h-3 w-3" />
                                )}
                              </Button>
                            </div>
                            {modelDropdownTarget === section.key && remoteModelError && (
                              <p className="text-[10px] text-destructive mt-0.5">{remoteModelError}</p>
                            )}
                            {modelDropdownTarget === section.key && remoteModels.length > 0 && (
                              <div className="absolute z-50 left-0 right-0 mt-1 max-h-48 overflow-y-auto rounded-md border border-border bg-popover shadow-md">
                                {remoteModels
                                  .filter((m) => {
                                    const cur = editDrafts[section.key]?.[field] || "";
                                    return !cur || m.id.toLowerCase().includes(cur.toLowerCase());
                                  })
                                  .map((m) => (
                                  <button
                                    key={m.id}
                                    type="button"
                                    className="w-full text-left px-3 py-1.5 text-xs font-mono hover:bg-muted/60 transition-colors flex items-center justify-between gap-2"
                                    onClick={() => {
                                      updateDraft(section.key, field, m.id);
                                      setModelDropdownTarget(null);
                                    }}
                                  >
                                    <span className="truncate">{m.id}</span>
                                    {m.owned_by && (
                                      <span className="text-[10px] text-muted-foreground shrink-0">{m.owned_by}</span>
                                    )}
                                  </button>
                                ))}
                              </div>
                            )}
                          </div>
                        ) : (
                          <>
                            <Input
                              value={editDrafts[section.key]?.[field] || ""}
                              onChange={(e) => updateDraft(section.key, field, e.target.value)}
                              type={field === "api_key" && !showKeys[`${section.key}_${field}`] ? "password" : "text"}
                              className={`h-8 text-xs font-mono ${field === "api_key" ? "pr-8" : ""}`}
                              placeholder={`输入 ${FIELD_LABELS[field]}...`}
                            />
                            {field === "api_key" && (
                              <button
                                className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground !min-h-0 !min-w-0 h-5 w-5 flex items-center justify-center"
                                onClick={() =>
                                  setShowKeys((prev) => ({
                                    ...prev,
                                    [`${section.key}_${field}`]: !prev[`${section.key}_${field}`],
                                  }))
                                }
                              >
                                {showKeys[`${section.key}_${field}`] ? (
                                  <EyeOff className="h-3 w-3" />
                                ) : (
                                  <Eye className="h-3 w-3" />
                                )}
                              </button>
                            )}
                          </>
                        )}
                      </div>
                    </div>
                  ))}
                  {section.key !== "embedding" && (
                  <div className="flex flex-col sm:flex-row sm:items-center gap-1 sm:gap-2">
                    <label className="text-xs text-muted-foreground sm:w-16 shrink-0">协议</label>
                    <select
                      value={editDrafts[section.key]?.protocol || "auto"}
                      onChange={(e) => updateDraft(section.key, "protocol", e.target.value)}
                      className="w-full h-8 text-xs rounded-md border border-input bg-background px-2 py-1 font-mono focus:outline-none focus:ring-1 focus:ring-ring"
                    >
                      <option value="auto">auto（自动检测）</option>
                      <option value="openai">openai（Chat Completions）</option>
                      <option value="openai_responses">openai_responses（Responses API）</option>
                      <option value="anthropic">anthropic（Claude 原生）</option>
                      <option value="gemini">gemini（Gemini 原生）</option>
                    </select>
                  </div>
                  )}
                </div>
                {testResult[section.key] && (
                  <div className={`mt-2 rounded-md px-3 py-2 text-xs border ${
                    testResult[section.key]!.ok
                      ? "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border-emerald-500/20"
                      : "bg-destructive/10 text-destructive border-destructive/20"
                  }`}>
                    <div className="flex items-center gap-1.5">
                      {testResult[section.key]!.ok ? (
                        <CheckCircle2 className="h-3.5 w-3.5 shrink-0" />
                      ) : (
                        <XCircle className="h-3.5 w-3.5 shrink-0" />
                      )}
                      <span className="truncate">
                        {testResult[section.key]!.ok ? "连通测试成功" : testResult[section.key]!.error || "连通测试失败"}
                      </span>
                      <button
                        className="ml-auto shrink-0 hover:opacity-70"
                        onClick={() => setTestResult((prev) => ({ ...prev, [section.key]: null }))}
                      >
                        <X className="h-3 w-3" />
                      </button>
                    </div>
                    {!testResult[section.key]!.ok && testResult[section.key]!.hint && (
                      <p className="mt-1.5 pt-1.5 border-t border-destructive/15 text-[11px] leading-relaxed text-amber-700 dark:text-amber-400">
                        💡 {testResult[section.key]!.hint}
                      </p>
                    )}
                  </div>
                )}
                <div className="flex flex-col sm:flex-row justify-end gap-2 mt-3">
                  {section.key !== "embedding" && (
                  <Button
                    size="sm"
                    variant="outline"
                    className="h-8 sm:h-7 text-xs gap-1"
                    onClick={() => handleTestConnection(section.key, {
                      name: section.key,
                      model: editDrafts[section.key]?.model,
                      base_url: editDrafts[section.key]?.base_url,
                      api_key: isMaskedApiKey(editDrafts[section.key]?.api_key || "") ? undefined : editDrafts[section.key]?.api_key,
                    })}
                    disabled={testingKey === section.key}
                  >
                    {testingKey === section.key ? (
                      <Loader2 className="h-3 w-3 animate-spin" />
                    ) : (
                      <Wifi className="h-3 w-3" />
                    )}
                    {testingKey === section.key ? "测试中..." : "连通测试"}
                  </Button>
                  )}
                  {section.key === "main" && (
                    <Button
                      size="sm"
                      variant="outline"
                      className="h-8 sm:h-7 text-xs gap-1"
                      onClick={() => handleProbeOne(section.key)}
                      disabled={probingKey === section.key}
                    >
                      {probingKey === section.key ? (
                        <Loader2 className="h-3 w-3 animate-spin" />
                      ) : (
                        <Zap className="h-3 w-3" />
                      )}
                      {probingKey === section.key ? "探测中" : "探测能力"}
                    </Button>
                  )}
                  <Button
                    size="sm"
                    className="h-8 sm:h-7 text-xs gap-1 text-white"
                    style={{ backgroundColor: "var(--em-primary)" }}
                    onClick={() => handleSaveSection(section.key)}
                    disabled={saving === section.key}
                  >
                    {saving === section.key ? (
                      <Loader2 className="h-3 w-3 animate-spin" />
                    ) : saved === section.key ? (
                      <CheckCircle2 className="h-3 w-3" />
                    ) : (
                      <Save className="h-3 w-3" />
                    )}
                    {saved === section.key ? "已保存" : "保存"}
                  </Button>
                </div>
              </div>
            )}
          </div>
          );
        })}
        </div>

        {/* ── 模型配置 ── */}
        <div className="rounded-lg border border-border order-1" data-coach-id="coach-settings-profiles">
          <div
            role="button"
            tabIndex={0}
            className="w-full flex items-center gap-2 px-3 py-2.5 text-left hover:bg-muted/30 transition-colors rounded-lg overflow-hidden cursor-pointer"
            onClick={() => toggleSection("profiles")}
            onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); toggleSection("profiles"); } }}
          >
            <span className="text-muted-foreground transition-transform flex-shrink-0" style={{ transform: expandedSections.profiles ? "rotate(90deg)" : "rotate(0deg)" }}>
              <ChevronRight className="h-3.5 w-3.5" />
            </span>
            <Dices className="h-4 w-4 flex-shrink-0" style={{ color: "var(--em-primary)" }} />
            <span className="font-semibold text-sm">模型配置</span>
            {config?.profiles && config.profiles.length > 0 && (
              <Badge variant="secondary" className="text-[10px]">{config.profiles.length} 个档案</Badge>
            )}
            <Button
              size="sm"
              variant="outline"
              className="h-6 text-[10px] gap-0.5 flex-shrink-0 ml-auto"
              onClick={(e) => {
                e.stopPropagation();
                setExpandedSections((prev) => ({ ...prev, profiles: true }));
                setNewProfile(true);
                setEditingProfile(null);
                setProfileDraft({ name: "", model: "", api_key: "", base_url: "", description: "", protocol: "auto", thinking_mode: "auto", model_family: "", custom_extra_body: "", custom_extra_headers: "" });
                scrollToForm();
              }}
            >
              <Plus className="h-3 w-3" />
              新增
            </Button>
          </div>

          {expandedSections.profiles && (
            <div className="px-4 pb-4 pt-1 border-t border-border/50">
              {/* Inline error */}
              {profileError && (
                <div className="rounded-md border border-destructive/40 bg-destructive/5 px-3 py-2 mb-3 flex items-center gap-2">
                  <AlertTriangle className="h-3.5 w-3.5 text-destructive shrink-0" />
                  <p className="text-xs text-destructive flex-1">{profileError}</p>
                  <button onClick={() => setProfileError(null)} className="text-destructive/60 hover:text-destructive shrink-0">
                    <X className="h-3 w-3" />
                  </button>
                </div>
              )}
              {/* Provider presets - quick add */}
              {!editingProfile && (
                <div className="mb-3 space-y-2.5">
                  <CodexOAuthAdminCard
                    onProfileCreated={() => { fetchConfig(true); useUIStore.getState().bumpModelProfiles(); }}
                    existingProfileNames={(config?.profiles || []).map((p) => p.name)}
                  />

                  <p className="text-xs text-muted-foreground mb-2">常见 API Key 提供方（点击预填表单，只需补充 API Key）</p>
                  <div className="grid grid-cols-2 min-[360px]:grid-cols-3 sm:grid-cols-5 gap-1.5">
                    {PROVIDER_PRESETS.map((preset) => (
                      <button
                        key={preset.id}
                        type="button"
                        className={`text-left rounded-lg border px-2 sm:px-2.5 py-1.5 sm:py-2 transition-all ${
                          newProfile && profileDraft.model === preset.model && profileDraft.base_url === preset.base_url
                            ? "border-[var(--em-primary)]/60 bg-[var(--em-primary)]/5"
                            : "border-border hover:border-[var(--em-primary)]/40 hover:bg-[var(--em-primary)]/5"
                        }`}
                        onClick={() => applyPresetToProfileDraft(preset)}
                      >
                        <div className="flex items-center gap-1 sm:gap-1.5">
                          <ProviderLogo id={preset.id} />
                          <span className="text-[11px] sm:text-xs font-medium truncate">{preset.label}</span>
                        </div>
                        <p className="text-[10px] font-mono text-muted-foreground truncate mt-0.5 hidden sm:block">{preset.model}</p>
                        <a
                          href={preset.purchaseUrl}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="items-center gap-0.5 text-[10px] mt-1 hover:underline hidden sm:inline-flex"
                          style={{ color: "var(--em-primary)" }}
                          onClick={(e) => e.stopPropagation()}
                        >
                          获取 Key <ExternalLink className="h-2.5 w-2.5" />
                        </a>
                      </button>
                    ))}
                  </div>
                </div>
              )}

              {/* New/Edit profile form */}
              {(newProfile || editingProfile) && (
                <div ref={formRef} className="rounded-lg border border-dashed border-border p-3 mb-3 space-y-2">
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                    <div>
                      <label className="text-xs text-muted-foreground">名称 *</label>
                      <Input
                        value={profileDraft.name}
                        onChange={(e) => setProfileDraft((d) => ({ ...d, name: e.target.value }))}
                        className="h-8 sm:h-7 text-xs"
                        placeholder="如: gpt4"
                      />
                    </div>
                    <div className="relative" ref={modelDropdownRef}>
                      <label className="text-xs text-muted-foreground">Model ID *</label>
                      <div className="flex gap-1">
                        <Input
                          value={profileDraft.model}
                          onChange={(e) => {
                            setProfileDraft((d) => ({ ...d, model: e.target.value }));
                            // 输入时过滤已加载的模型列表
                            if (remoteModels.length > 0) setModelDropdownTarget("_profile");
                          }}
                          onFocus={() => { if (remoteModels.length > 0) setModelDropdownTarget("_profile"); }}
                          className="h-8 sm:h-7 text-xs font-mono flex-1"
                          placeholder="如: gpt-4o"
                        />
                        <Button
                          type="button"
                          size="sm"
                          variant="outline"
                          className="h-8 sm:h-7 px-2 shrink-0"
                          title="从 API 自动检测可用模型"
                          disabled={fetchingModels}
                          onClick={() => handleFetchRemoteModels(
                            "_profile",
                            profileDraft.base_url || undefined,
                            profileDraft.api_key || undefined,
                            profileDraft.protocol || undefined,
                          )}
                        >
                          {fetchingModels ? (
                            <Loader2 className="h-3 w-3 animate-spin" />
                          ) : (
                            <Search className="h-3 w-3" />
                          )}
                        </Button>
                      </div>
                      {remoteModelError && (
                        <p className="text-[10px] text-destructive mt-0.5">{remoteModelError}</p>
                      )}
                      {modelDropdownTarget === "_profile" && remoteModels.length > 0 && (
                        <div className="absolute z-50 top-full left-0 right-0 mt-1 max-h-48 overflow-y-auto rounded-md border border-border bg-popover shadow-md">
                          {remoteModels
                            .filter((m) => !profileDraft.model || m.id.toLowerCase().includes(profileDraft.model.toLowerCase()))
                            .map((m) => (
                            <button
                              key={m.id}
                              type="button"
                              className="w-full text-left px-3 py-1.5 text-xs font-mono hover:bg-muted/60 transition-colors flex items-center justify-between gap-2"
                              onClick={() => {
                                setProfileDraft((d) => ({ ...d, model: m.id }));
                                setModelDropdownTarget(null);
                              }}
                            >
                              <span className="truncate">{m.id}</span>
                              {m.owned_by && (
                                <span className="text-[10px] text-muted-foreground shrink-0">{m.owned_by}</span>
                              )}
                            </button>
                          ))}
                          {remoteModels.filter((m) => !profileDraft.model || m.id.toLowerCase().includes(profileDraft.model.toLowerCase())).length === 0 && (
                            <p className="px-3 py-2 text-[10px] text-muted-foreground">无匹配模型</p>
                          )}
                        </div>
                      )}
                    </div>
                  </div>
                  <div>
                    <label className="text-xs text-muted-foreground">Base URL（空则继承主配置）</label>
                    <Input
                      value={profileDraft.base_url}
                      onChange={(e) => setProfileDraft((d) => ({ ...d, base_url: e.target.value }))}
                      className="h-8 sm:h-7 text-xs font-mono"
                      placeholder="https://..."
                    />
                  </div>
                  <div>
                    <label className="text-xs text-muted-foreground">API Key（空则继承主配置）</label>
                    <div className="relative">
                      <Input
                        value={profileDraft.api_key}
                        onChange={(e) => setProfileDraft((d) => ({ ...d, api_key: e.target.value }))}
                        className="h-8 sm:h-7 text-xs font-mono pr-8"
                        type={showKeys["profile_api_key"] ? "text" : "password"}
                        placeholder="sk-..."
                      />
                      <button
                        type="button"
                        className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground !min-h-0 !min-w-0 h-5 w-5 flex items-center justify-center"
                        onClick={() => setShowKeys((prev) => ({ ...prev, profile_api_key: !prev.profile_api_key }))}
                      >
                        {showKeys["profile_api_key"] ? (
                          <EyeOff className="h-3 w-3" />
                        ) : (
                          <Eye className="h-3 w-3" />
                        )}
                      </button>
                    </div>
                  </div>
                  <div>
                    <label className="text-xs text-muted-foreground">描述</label>
                    <Input
                      value={profileDraft.description}
                      onChange={(e) => setProfileDraft((d) => ({ ...d, description: e.target.value }))}
                      className="h-8 sm:h-7 text-xs"
                      placeholder="简短说明"
                    />
                  </div>
                  <div>
                    <label className="text-xs text-muted-foreground">协议</label>
                    <select
                      value={profileDraft.protocol || "auto"}
                      onChange={(e) => setProfileDraft((d) => ({ ...d, protocol: e.target.value }))}
                      className="w-full h-8 sm:h-7 text-xs rounded-md border border-input bg-background px-2 py-1 font-mono focus:outline-none focus:ring-1 focus:ring-ring"
                    >
                      <option value="auto">auto（自动检测）</option>
                      <option value="openai">openai（Chat Completions）</option>
                      <option value="openai_responses">openai_responses（Responses API）</option>
                      <option value="anthropic">anthropic（Claude 原生）</option>
                      <option value="gemini">gemini（Gemini 原生）</option>
                    </select>
                  </div>
                  {/* ── 高级配置（折叠区） ── */}
                  <div className="border border-border/50 rounded-md overflow-hidden">
                    <button
                      type="button"
                      className="flex items-center gap-1.5 w-full px-3 py-1.5 text-xs text-muted-foreground hover:bg-muted/50 transition-colors"
                      onClick={() => setExpandedSections((prev) => ({ ...prev, _profile_advanced: !prev._profile_advanced }))}
                    >
                      {expandedSections._profile_advanced ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
                      <Wrench className="h-3 w-3" />
                      高级配置
                      {(profileDraft.thinking_mode !== "auto" || profileDraft.model_family || profileDraft.custom_extra_body || profileDraft.custom_extra_headers) && (
                        <Badge variant="secondary" className="text-[9px] ml-1 px-1 py-0">已配置</Badge>
                      )}
                    </button>
                    {expandedSections._profile_advanced && (
                      <div className="px-3 pb-3 pt-1 border-t border-border/50 space-y-2">
                        <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                          <div>
                            <label className="text-xs text-muted-foreground">思考模式</label>
                            <select
                              value={profileDraft.thinking_mode || "auto"}
                              onChange={(e) => setProfileDraft((d) => ({ ...d, thinking_mode: e.target.value }))}
                              className="w-full h-8 sm:h-7 text-xs rounded-md border border-input bg-background px-2 py-1 font-mono focus:outline-none focus:ring-1 focus:ring-ring"
                            >
                              <option value="auto">auto（自动探测）</option>
                              <option value="disabled">disabled（禁用思考）</option>
                              <option value="claude">claude（原生 Extended Thinking）</option>
                              <option value="claude_compat">claude_compat（OAI 代理透传）</option>
                              <option value="enable_thinking">enable_thinking（DashScope/硅基流动）</option>
                              <option value="glm_thinking">glm_thinking（智谱 GLM）</option>
                              <option value="openai_reasoning">openai_reasoning（OpenAI o系列）</option>
                              <option value="openrouter">openrouter（OpenRouter）</option>
                              <option value="deepseek">deepseek（自动输出推理）</option>
                              <option value="reasoning_content_auto">reasoning_content_auto（自动）</option>
                            </select>
                          </div>
                          <div>
                            <label className="text-xs text-muted-foreground">模型族</label>
                            <select
                              value={profileDraft.model_family || ""}
                              onChange={(e) => setProfileDraft((d) => ({ ...d, model_family: e.target.value }))}
                              className="w-full h-8 sm:h-7 text-xs rounded-md border border-input bg-background px-2 py-1 font-mono focus:outline-none focus:ring-1 focus:ring-ring"
                            >
                              <option value="">auto（按模型名推断）</option>
                              <option value="claude">Claude</option>
                              <option value="gpt">GPT</option>
                              <option value="gemini">Gemini</option>
                              <option value="deepseek">DeepSeek</option>
                              <option value="qwen">Qwen（通义千问）</option>
                              <option value="glm">GLM（智谱）</option>
                              <option value="grok">Grok</option>
                            </select>
                          </div>
                        </div>
                        <div>
                          <label className="text-xs text-muted-foreground">自定义请求体 (JSON)</label>
                          <textarea
                            value={profileDraft.custom_extra_body || ""}
                            onChange={(e) => setProfileDraft((d) => ({ ...d, custom_extra_body: e.target.value }))}
                            placeholder='{"temperature": 0.7}'
                            rows={2}
                            className="w-full text-xs rounded-md border border-input bg-background px-2 py-1.5 font-mono focus:outline-none focus:ring-1 focus:ring-ring resize-y min-h-[2rem]"
                          />
                        </div>
                        <div>
                          <label className="text-xs text-muted-foreground">自定义请求头 (JSON)</label>
                          <textarea
                            value={profileDraft.custom_extra_headers || ""}
                            onChange={(e) => setProfileDraft((d) => ({ ...d, custom_extra_headers: e.target.value }))}
                            placeholder='{"x-custom-auth": "xxx"}'
                            rows={2}
                            className="w-full text-xs rounded-md border border-input bg-background px-2 py-1.5 font-mono focus:outline-none focus:ring-1 focus:ring-ring resize-y min-h-[2rem]"
                          />
                        </div>
                      </div>
                    )}
                  </div>
                  {testResult["_profile_form"] && (
                    <div className={`rounded-md px-3 py-2 text-xs border ${
                      testResult["_profile_form"]!.ok
                        ? "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border-emerald-500/20"
                        : "bg-destructive/10 text-destructive border-destructive/20"
                    }`}>
                      <div className="flex items-center gap-1.5">
                        {testResult["_profile_form"]!.ok ? (
                          <CheckCircle2 className="h-3.5 w-3.5 shrink-0" />
                        ) : (
                          <XCircle className="h-3.5 w-3.5 shrink-0" />
                        )}
                        <span className="truncate">
                          {testResult["_profile_form"]!.ok ? "连通测试成功" : testResult["_profile_form"]!.error || "连通测试失败"}
                        </span>
                        <button
                          className="ml-auto shrink-0 hover:opacity-70"
                          onClick={() => setTestResult((prev) => ({ ...prev, _profile_form: null }))}
                        >
                          <X className="h-3 w-3" />
                        </button>
                      </div>
                      {!testResult["_profile_form"]!.ok && testResult["_profile_form"]!.hint && (
                        <p className="mt-1.5 pt-1.5 border-t border-destructive/15 text-[11px] leading-relaxed text-amber-700 dark:text-amber-400">
                          💡 {testResult["_profile_form"]!.hint}
                        </p>
                      )}
                    </div>
                  )}
                  <div className="flex flex-col-reverse sm:flex-row justify-end gap-2 pt-1">
                    <Button
                      size="sm"
                      variant="ghost"
                      className="h-8 sm:h-7 text-xs gap-1"
                      onClick={() => {
                        setNewProfile(false);
                        setEditingProfile(null);
                        setProfileError(null);
                        setTestResult((prev) => ({ ...prev, _profile_form: null }));
                      }}
                    >
                      <X className="h-3 w-3" /> 取消
                    </Button>
                    <div className="flex gap-2">
                      <Button
                        size="sm"
                        variant="outline"
                        className="h-8 sm:h-7 text-xs gap-1 flex-1 sm:flex-initial"
                        disabled={!profileDraft.model || testingKey === "_profile_form"}
                        onClick={() => handleTestConnection("_profile_form", {
                          model: profileDraft.model,
                          base_url: profileDraft.base_url || undefined,
                          api_key: profileDraft.api_key || undefined,
                        })}
                      >
                        {testingKey === "_profile_form" ? (
                          <Loader2 className="h-3 w-3 animate-spin" />
                        ) : (
                          <Wifi className="h-3 w-3" />
                        )}
                        {testingKey === "_profile_form" ? "测试中..." : "连通测试"}
                      </Button>
                      <Button
                        size="sm"
                        className="h-8 sm:h-7 text-xs gap-1 text-white flex-1 sm:flex-initial"
                        style={{ backgroundColor: "var(--em-primary)" }}
                        disabled={!profileDraft.name || !profileDraft.model || addingProfile}
                        onClick={() =>
                          editingProfile
                            ? handleUpdateProfile(editingProfile)
                            : handleAddProfile()
                        }
                      >
                        {addingProfile ? (
                          <Loader2 className="h-3 w-3 animate-spin" />
                        ) : (
                          <Save className="h-3 w-3" />
                        )}
                        {addingProfile ? "添加中..." : editingProfile ? "更新" : "添加"}
                      </Button>
                    </div>
                  </div>
                </div>
              )}

              {/* Profile list */}
              <div className="space-y-2">
                {config?.profiles.map((p) => {
                  const pCaps = capsMap[p.name];
                  const isHighlighted = highlightProfile === p.name;
                  const isCodexProfile = p.model.startsWith("openai-codex/");
                  const isUnhealthy = isModelUnhealthy(pCaps);
                  const initials = p.name.slice(0, 2).toUpperCase();
                  const providerId = inferProfileProvider(p);
                  const hasProviderLogo = !!(providerId && PROVIDER_LOGO_SLUG[providerId]);
                  const providerColor = getProviderBrandColor(providerId);
                  return (
                  <div
                    key={p.name}
                    ref={(el) => { profileCardRefs.current[p.name] = el; }}
                    className={`group rounded-xl border text-sm transition-all duration-300 ${
                      isCodexProfile
                        ? "border-[var(--em-primary)]/25 bg-gradient-to-r from-[var(--em-primary)]/5 to-transparent"
                        : isHighlighted
                          ? "border-[var(--em-primary)] bg-[var(--em-primary)]/5 ring-1 ring-[var(--em-primary)]/20 scale-[1.005] cursor-pointer shadow-sm"
                          : isUnhealthy
                            ? "border-destructive/30 bg-destructive/3 cursor-pointer hover:border-destructive/50 hover:shadow-sm"
                            : "border-border/60 bg-card cursor-pointer hover:border-border hover:shadow-sm hover:bg-muted/20"
                    }`}
                    onClick={() => {
                      if (isCodexProfile) return;
                      setEditingProfile(p.name);
                      setNewProfile(false);
                      setProfileDraft({
                        name: p.name,
                        model: p.model,
                        api_key: "",
                        base_url: p.base_url,
                        description: p.description,
                        protocol: p.protocol || "auto",
                        thinking_mode: p.thinking_mode || "auto",
                        model_family: p.model_family || "",
                        custom_extra_body: p.custom_extra_body || "",
                        custom_extra_headers: p.custom_extra_headers || "",
                      });
                      scrollToForm();
                    }}
                  >
                    {/* ── Main row ── */}
                    <div className="flex items-center gap-3 px-3 py-2.5">
                      {/* Avatar */}
                      <div
                        className={`flex-shrink-0 w-8 h-8 rounded-lg flex items-center justify-center text-[11px] font-bold select-none ${
                        isCodexProfile
                          ? "bg-[var(--em-primary)]/15 text-[var(--em-primary)]"
                          : hasProviderLogo
                            ? "bg-muted/40"
                          : isUnhealthy
                            ? "bg-destructive/10 text-destructive"
                            : "bg-muted text-muted-foreground group-hover:bg-[var(--em-primary)]/10 group-hover:text-[var(--em-primary)] transition-colors"
                        }`}
                        style={hasProviderLogo
                          ? {
                            backgroundColor: withAlpha(providerColor, "1A"),
                            color: providerColor,
                          }
                          : undefined}
                      >
                        {hasProviderLogo && providerId
                          ? <ProviderLogo id={providerId} color={providerColor} />
                          : isCodexProfile
                            ? <Lock className="h-3.5 w-3.5" />
                            : initials}
                      </div>

                      {/* Name + model */}
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2 min-w-0">
                          <span className="font-semibold text-sm truncate">{p.name}</span>
                          {isCodexProfile && (
                            <span className="flex-shrink-0 inline-flex items-center gap-0.5 rounded-full px-1.5 py-0.5 text-[9px] font-semibold bg-[var(--em-primary)]/15 text-[var(--em-primary)]">
                              <Lock className="h-2 w-2" />
                              OAuth
                            </span>
                          )}
                          {isUnhealthy && (
                            <span className="flex-shrink-0 inline-flex items-center gap-0.5 text-destructive text-[9px] font-medium">
                              <AlertTriangle className="h-2.5 w-2.5" />
                              <span className="hidden sm:inline">不可用</span>
                            </span>
                          )}
                        </div>
                        <div className="flex items-center gap-1.5 mt-0.5 min-w-0">
                          <span className="text-[10px] font-mono text-muted-foreground truncate max-w-[140px] sm:max-w-[200px]">{formatModelIdForDisplay(p.model)}</span>
                          {!isUnhealthy && p.description && (
                            <span className="hidden sm:inline text-[10px] text-muted-foreground/70 truncate">· {p.description}</span>
                          )}
                        </div>
                      </div>

                      {/* Capability badges — hidden on mobile */}
                      <div className="hidden sm:block flex-shrink-0">
                        {isUnhealthy ? null : <CapabilityBadges caps={pCaps ?? null} />}
                      </div>

                      {/* Action buttons */}
                      <div className="flex items-center gap-0.5 flex-shrink-0 opacity-100 sm:opacity-60 sm:group-hover:opacity-100 transition-opacity">
                        {/* Apply role dropdown */}
                        <div className="relative">
                          <button
                            title="应用到角色"
                            className="h-7 w-7 sm:h-6 sm:w-6 flex items-center justify-center rounded-md text-muted-foreground hover:text-foreground hover:bg-muted/60 transition-colors disabled:opacity-40"
                            onClick={(e) => { e.stopPropagation(); setApplyMenuOpen(applyMenuOpen === p.name ? null : p.name); }}
                            disabled={applyingProfile === p.name}
                          >
                            {applyingProfile === p.name ? (
                              <Loader2 className="h-3.5 w-3.5 animate-spin" />
                            ) : (
                              <ArrowRightLeft className="h-3.5 w-3.5" />
                            )}
                          </button>
                          {applyMenuOpen === p.name && (
                            <>
                              <div className="fixed inset-0 z-40" onClick={(e) => { e.stopPropagation(); setApplyMenuOpen(null); }} />
                              <div className="absolute right-0 bottom-full mb-1.5 sm:bottom-auto sm:top-full sm:mb-0 sm:mt-1.5 z-50 w-40 rounded-xl border border-border/60 bg-popover shadow-lg overflow-hidden">
                                <div className="px-2.5 py-1.5 border-b border-border/40">
                                  <p className="text-[10px] font-medium text-muted-foreground uppercase tracking-wide">应用到角色</p>
                                </div>
                                <div className="p-1">
                                  {([
                                    { role: "main" as const, label: "主模型", icon: <Server className="h-3.5 w-3.5" />, color: "text-blue-500" },
                                    { role: "aux" as const, label: "辅助模型", icon: <Bot className="h-3.5 w-3.5" />, color: "text-violet-500" },
                                    { role: "vlm" as const, label: "视觉模型", icon: <ScanEye className="h-3.5 w-3.5" />, color: "text-emerald-500" },
                                  ]).map((item) => (
                                    <button
                                      key={item.role}
                                      className="flex items-center gap-2.5 w-full px-2.5 py-2 text-xs rounded-lg hover:bg-muted/60 active:bg-muted transition-colors text-left"
                                      onClick={(e) => { e.stopPropagation(); handleApplyProfileToRole(p, item.role); }}
                                    >
                                      <span className={item.color}>{item.icon}</span>
                                      <span className="font-medium">用作{item.label}</span>
                                    </button>
                                  ))}
                                </div>
                              </div>
                            </>
                          )}
                        </div>
                        {/* Probe */}
                        <button
                          title="探测能力"
                          className="h-7 w-7 sm:h-6 sm:w-6 flex items-center justify-center rounded-md text-muted-foreground hover:text-foreground hover:bg-muted/60 transition-colors disabled:opacity-40"
                          onClick={(e) => { e.stopPropagation(); handleProbeOne(p.name); }}
                          disabled={probingKey === p.name}
                        >
                          {probingKey === p.name ? (
                            <Loader2 className="h-3.5 w-3.5 animate-spin" />
                          ) : (
                            <Zap className="h-3.5 w-3.5" />
                          )}
                        </button>
                        {/* Edit */}
                        {!isCodexProfile && (
                          <button
                            title="编辑"
                            className="h-7 w-7 sm:h-6 sm:w-6 flex items-center justify-center rounded-md text-muted-foreground hover:text-foreground hover:bg-muted/60 transition-colors"
                            onClick={(e) => {
                              e.stopPropagation();
                              setEditingProfile(p.name);
                              setNewProfile(false);
                              setProfileDraft({
                                name: p.name,
                                model: p.model,
                                api_key: "",
                                base_url: p.base_url,
                                description: p.description,
                                protocol: p.protocol || "auto",
                                thinking_mode: p.thinking_mode || "auto",
                                model_family: p.model_family || "",
                                custom_extra_body: p.custom_extra_body || "",
                                custom_extra_headers: p.custom_extra_headers || "",
                              });
                              scrollToForm();
                            }}
                          >
                            <Pencil className="h-3.5 w-3.5" />
                          </button>
                        )}
                        {/* Delete */}
                        <button
                          title="删除"
                          className="h-7 w-7 sm:h-6 sm:w-6 flex items-center justify-center rounded-md text-muted-foreground hover:text-destructive hover:bg-destructive/10 transition-colors"
                          onClick={(e) => { e.stopPropagation(); handleDeleteProfile(p.name); }}
                        >
                          <Trash2 className="h-3.5 w-3.5" />
                        </button>
                      </div>
                    </div>

                    {/* ── Mobile capability + description row ── */}
                    {(!isUnhealthy || isCodexProfile) && (
                      <div className="sm:hidden flex items-center gap-2 px-3 pb-2.5 -mt-1">
                        <CapabilityBadges caps={pCaps ?? null} />
                        {p.description && !isCodexProfile && (
                          <span className="text-[10px] text-muted-foreground/70 truncate">· {p.description}</span>
                        )}
                        {isCodexProfile && (
                          <span className="text-[10px] text-muted-foreground/70">通过 Codex 卡片管理</span>
                        )}
                      </div>
                    )}

                    {/* ── Error row ── */}
                    {isUnhealthy && !isCodexProfile && (
                      <div className="flex items-center gap-1.5 px-3 pb-2.5 -mt-0.5">
                        <AlertTriangle className="h-3 w-3 flex-shrink-0 text-destructive" />
                        <p className="text-[10px] text-destructive truncate" title={getHealthError(pCaps)}>
                          {getHealthError(pCaps) || "连接失败"}
                        </p>
                      </div>
                    )}
                  </div>
                  );
                })}
                {config?.profiles.length === 0 && !newProfile && (
                  <div className="flex flex-col items-center gap-2 py-8 text-center">
                    <div className="w-10 h-10 rounded-xl bg-muted/50 flex items-center justify-center">
                      <Bot className="h-5 w-5 text-muted-foreground/50" />
                    </div>
                    <p className="text-xs text-muted-foreground">
                      暂无模型配置
                    </p>
                    <p className="text-[10px] text-muted-foreground/60">
                      点击上方提供方预设或「新增」来添加
                    </p>
                  </div>
                )}
              </div>
            </div>
          )}
        </div>

        {/* ── Advanced section with pills ── */}
        <div className="order-3 w-full">
        <Separator className="my-1" />
        <div>
          {/* Pills navigation */}
          <div className="flex items-center gap-1 mb-3 overflow-x-auto scrollbar-none">
            {([
              { key: "capabilities" as const, label: "能力探测", icon: <Zap className="h-3 w-3" /> },
              { key: "thinking" as const, label: "推理深度", icon: <Brain className="h-3 w-3" /> },
              { key: "transfer" as const, label: "导入导出", icon: <Download className="h-3 w-3" /> },
            ] as const).map((pill) => {
              const isActive = advancedPill === pill.key;
              return (
                <button
                  key={pill.key}
                  type="button"
                  className={`inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-medium transition-colors whitespace-nowrap border ${
                    isActive
                      ? "text-white border-transparent"
                      : "border-border text-muted-foreground hover:bg-muted/60 hover:text-foreground"
                  }`}
                  style={isActive ? { backgroundColor: "var(--em-primary)" } : undefined}
                  onClick={() => setAdvancedPill(pill.key)}
                >
                  {pill.icon}
                  {pill.label}
                </button>
              );
            })}
          </div>

          {/* ── Capabilities pill ── */}
          {advancedPill === "capabilities" && (
            <div>
              <div className="flex items-center justify-between gap-2 mb-3">
                <div className="min-w-0">
                  <p className="text-xs text-muted-foreground truncate">
                    当前主模型: {config?.main?.model || "未配置"}
                  </p>
                </div>
                <Button
                  size="sm"
                  variant="outline"
                  className="h-7 text-xs gap-1 flex-shrink-0"
                  onClick={handleProbeAll}
                  disabled={probingAll}
                >
                  {probingAll ? (
                    <Loader2 className="h-3 w-3 animate-spin" />
                  ) : (
                    <Zap className="h-3 w-3" />
                  )}
                  {probingAll ? "探测中..." : "一键探测全部"}
                </Button>
              </div>

              {capsMap.main ? (
                <div className="space-y-2">
                  <CapabilityRow
                    icon={<Wrench className="h-3.5 w-3.5" />}
                    label="工具调用 (Tool Calling)"
                    desc="模型是否支持 function calling"
                    value={capsMap.main.supports_tool_calling}
                    error={capsMap.main.probe_errors?.tool_calling}
                    onToggle={(v) => handleCapToggle("main", config?.main?.model || "", config?.main?.base_url || "", "supports_tool_calling", v)}
                  />
                  <CapabilityRow
                    icon={<ImageIcon className="h-3.5 w-3.5" />}
                    label="图像识别 (Vision)"
                    desc="模型是否支持图片输入"
                    value={capsMap.main.supports_vision}
                    error={capsMap.main.probe_errors?.vision}
                    onToggle={(v) => handleCapToggle("main", config?.main?.model || "", config?.main?.base_url || "", "supports_vision", v)}
                  />
                  <CapabilityRow
                    icon={<Brain className="h-3.5 w-3.5" />}
                    label="思考输出 (Thinking)"
                    desc={capsMap.main.thinking_type ? `类型: ${capsMap.main.thinking_type}` : "模型是否支持输出推理过程"}
                    value={capsMap.main.supports_thinking}
                    error={capsMap.main.probe_errors?.thinking}
                    onToggle={(v) => handleCapToggle("main", config?.main?.model || "", config?.main?.base_url || "", "supports_thinking", v)}
                  />
                  {capsMap.main.detected_at && (
                    <p className="text-[10px] text-muted-foreground mt-2">
                      上次探测: {new Date(capsMap.main.detected_at).toLocaleString()}
                      {capsMap.main.manual_override && (
                        <Badge variant="secondary" className="ml-1.5 text-[9px]">手动覆盖</Badge>
                      )}
                    </p>
                  )}
                </div>
              ) : (
                <div className="text-center py-6">
                  <p className="text-xs text-muted-foreground mb-2">
                    尚未探测模型能力
                  </p>
                  <Button
                    size="sm"
                    className="h-7 text-xs gap-1 text-white"
                    style={{ backgroundColor: "var(--em-primary)" }}
                    onClick={handleProbeAll}
                    disabled={probingAll}
                  >
                    {probingAll ? <Loader2 className="h-3 w-3 animate-spin" /> : <Zap className="h-3 w-3" />}
                    一键探测全部
                  </Button>
                </div>
              )}
            </div>
          )}

          {/* ── Thinking pill ── */}
          {advancedPill === "thinking" && (
            <div className="space-y-3">
              <p className="text-xs text-muted-foreground">
                控制模型思考链的深度，影响推理质量和 token 消耗
              </p>
              <div>
                <label className="text-xs text-muted-foreground mb-1.5 block">思考等级</label>
                <div className="grid grid-cols-3 sm:grid-cols-6 gap-1.5 sm:gap-1">
                  {(["none", "minimal", "low", "medium", "high", "xhigh"] as const).map((level) => {
                    const labels: Record<string, string> = {
                      none: "关闭", minimal: "极简", low: "低",
                      medium: "中", high: "高", xhigh: "极高",
                    };
                    const isActive = thinkingEffort === level;
                    return (
                      <button
                        key={level}
                        className={`px-2.5 py-2 sm:py-1 rounded-md text-xs font-medium transition-colors border ${
                          isActive
                            ? "text-white border-transparent"
                            : "border-border text-muted-foreground hover:bg-muted/60"
                        }`}
                        style={isActive ? { backgroundColor: "var(--em-primary)" } : undefined}
                        onClick={() => {
                          setThinkingEffort(level);
                          handleSaveThinking(level, thinkingBudget);
                        }}
                        disabled={thinkingSaving}
                      >
                        {labels[level]}
                      </button>
                    );
                  })}
                </div>
              </div>
              <div>
                <label className="text-xs text-muted-foreground mb-1.5 block">
                  Token 预算（可选，留空则按等级自动换算）
                </label>
                <div className="flex flex-col sm:flex-row sm:items-center gap-2">
                  <Input
                    value={thinkingBudget}
                    onChange={(e) => setThinkingBudget(e.target.value.replace(/\D/g, ""))}
                    className="h-8 text-xs font-mono w-full sm:w-32"
                    placeholder="自动"
                    inputMode="numeric"
                  />
                  <Button
                    size="sm"
                    className="h-8 sm:h-7 text-xs gap-1 text-white flex-shrink-0"
                    style={{ backgroundColor: "var(--em-primary)" }}
                    onClick={() => handleSaveThinking(thinkingEffort, thinkingBudget)}
                    disabled={thinkingSaving}
                  >
                    {thinkingSaving ? (
                      <Loader2 className="h-3 w-3 animate-spin" />
                    ) : thinkingSaved ? (
                      <CheckCircle2 className="h-3 w-3" />
                    ) : (
                      <Save className="h-3 w-3" />
                    )}
                    {thinkingSaved ? "已保存" : "保存"}
                  </Button>
                </div>
                {thinkingEffectiveBudget > 0 && (
                  <p className="text-[10px] text-muted-foreground mt-1">
                    当前生效预算: {thinkingEffectiveBudget.toLocaleString()} tokens
                  </p>
                )}
              </div>
            </div>
          )}

          {/* ── Transfer pill ── */}
          {advancedPill === "transfer" && (
            <ConfigTransferPanel config={config} />
          )}
        </div>
        </div>
    </div>
  );
}

function ConfigTransferPanel({ config }: { config: ModelConfig | null }) {
  const user = useAuthStore((s) => s.user);
  const authEnabled = useAuthConfigStore((s) => s.authEnabled);
  const isAdminScope = !authEnabled || !user || user.role === "admin";

  const [mode, setMode] = useState<"idle" | "export" | "import">("idle");
  const [exportMode, setExportMode] = useState<"password" | "simple">("password");
  const [exportSections, setExportSections] = useState<Record<string, boolean>>({
    main: true,
    aux: true,
    vlm: true,
    embedding: true,
    profiles: true,
    user: true,
  });
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [importToken, setImportToken] = useState("");
  const [importPassword, setImportPassword] = useState("");
  const [resultToken, setResultToken] = useState("");
  const [importing, setImporting] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [copied, setCopied] = useState(false);
  const [importResult, setImportResult] = useState<{ status: string; imported: Record<string, unknown> } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [needsPassword, setNeedsPassword] = useState<boolean | null>(null);

  const resetState = () => {
    setMode("idle");
    setPassword("");
    setConfirmPassword("");
    setImportToken("");
    setImportPassword("");
    setResultToken("");
    setImportResult(null);
    setError(null);
    setNeedsPassword(null);
    setCopied(false);
  };

  const handleExport = async () => {
    setError(null);
    if (exportMode === "password") {
      if (!password) { setError("请输入加密密码"); return; }
      if (password !== confirmPassword) { setError("两次密码不一致"); return; }
    }
    setExporting(true);
    try {
      const sections = isAdminScope
        ? Object.entries(exportSections)
            .filter(([k, v]) => ["main", "aux", "vlm", "embedding", "profiles"].includes(k) && v)
            .map(([k]) => k)
        : ["user"];
      const data = await apiPost<{ token: string }>("/config/export", {
        sections,
        mode: exportMode,
        password: exportMode === "password" ? password : null,
      }, { direct: true });
      setResultToken(data.token);
    } catch (e) {
      setError(e instanceof Error ? e.message : "导出失败");
    } finally {
      setExporting(false);
    }
  };

  const handleDetectToken = async (token: string) => {
    setImportToken(token);
    setNeedsPassword(null);
    setError(null);
    if (!token.trim()) return;
    try {
      const data = await apiPost<{ needs_password: boolean }>("/config/transfer/detect", { token }, { direct: true });
      setNeedsPassword(data.needs_password);
    } catch {
      setNeedsPassword(null);
    }
  };

  const handleImport = async () => {
    setError(null);
    if (!importToken.trim()) { setError("请粘贴配置令牌"); return; }
    if (needsPassword && !importPassword) { setError("此令牌需要密码"); return; }
    setImporting(true);
    try {
      const data = await apiPost<{ status: string; imported: Record<string, unknown>; exported_at: string }>("/config/import", {
        token: importToken,
        password: needsPassword ? importPassword : null,
      }, { direct: true });
      setImportResult(data);
    } catch (e) {
      setError(e instanceof Error ? e.message : "导入失败");
    } finally {
      setImporting(false);
    }
  };

  const handleCopy = async () => {
    await navigator.clipboard.writeText(resultToken);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const sectionLabels: Record<string, string> = isAdminScope
    ? { main: "主模型", aux: "辅助模型", vlm: "VLM 视觉模型", embedding: "Embedding 词嵌入", profiles: "多模型配置" }
    : { user: "个人 LLM 配置" };

  return (
    <div>
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2 mb-3">
        <div className="min-w-0">
          <h3 className="font-semibold text-sm flex items-center gap-1.5">
            <Download className="h-4 w-4 flex-shrink-0" style={{ color: "var(--em-primary)" }} />
            配置导出 / 导入
          </h3>
          <p className="text-xs text-muted-foreground">
            {isAdminScope
              ? "一键导出全局模型配置（含 Key），加密分享给他人"
              : "导出个人 LLM 配置，加密备份或迁移"}
          </p>
        </div>
        <div className="flex gap-1.5 flex-shrink-0 flex-wrap">
          {mode !== "export" && (
            <Button size="sm" variant="outline" className="h-8 sm:h-7 text-xs gap-1" onClick={() => { resetState(); setMode("export"); }}>
              <Download className="h-3 w-3" /> 导出
            </Button>
          )}
          {mode !== "import" && (
            <Button size="sm" variant="outline" className="h-8 sm:h-7 text-xs gap-1" onClick={() => { resetState(); setMode("import"); }}>
              <Import className="h-3 w-3" /> 导入
            </Button>
          )}
          {mode !== "idle" && (
            <Button size="sm" variant="ghost" className="h-8 sm:h-7 text-xs gap-1" onClick={resetState}>
              <X className="h-3 w-3" /> 关闭
            </Button>
          )}
        </div>
      </div>

      {error && (
        <div className="rounded-md border border-destructive/40 bg-destructive/5 px-3 py-2 mb-3">
          <p className="text-xs text-destructive">{error}</p>
        </div>
      )}

      {/* Export Panel */}
      {mode === "export" && !resultToken && (
        <div className="rounded-lg border border-dashed border-border p-3 space-y-3">
          <div>
            <p className="text-xs font-medium mb-2">选择导出区块</p>
            <div className="flex flex-wrap gap-x-3 gap-y-1.5">
              {Object.entries(sectionLabels).map(([key, label]) => (
                <MiniCheckbox
                  key={key}
                  checked={exportSections[key]}
                  onChange={(v) => setExportSections((prev) => ({ ...prev, [key]: v }))}
                  label={label}
                />
              ))}
            </div>
          </div>
          <div>
            <p className="text-xs font-medium mb-2">加密模式</p>
            <div className="flex flex-col sm:flex-row gap-2 sm:gap-3">
              <label className="inline-flex items-center gap-1.5 text-xs cursor-pointer">
                <input type="radio" name="export-mode" checked={exportMode === "password"} onChange={() => setExportMode("password")} />
                <Lock className="h-3 w-3" /> 口令加密（推荐）
              </label>
              <label className="inline-flex items-center gap-1.5 text-xs cursor-pointer">
                <input type="radio" name="export-mode" checked={exportMode === "simple"} onChange={() => setExportMode("simple")} />
                <Unlock className="h-3 w-3" /> 简单分享
              </label>
            </div>
          </div>
          {exportMode === "password" && (
            <div className="space-y-2">
              <div>
                <div className="flex items-center justify-between mb-0.5">
                  <label className="text-xs text-muted-foreground">设置密码</label>
                  <button
                    type="button"
                    className="inline-flex items-center gap-1 text-[11px] hover:underline"
                    style={{ color: "var(--em-primary)" }}
                    onClick={() => {
                      const chars = "ABCDEFGHJKMNPQRSTWXYZabcdefghjkmnpqrstwxyz23456789!@#$&*";
                      const arr = new Uint8Array(16);
                      crypto.getRandomValues(arr);
                      const pw = Array.from(arr, (b) => chars[b % chars.length]).join("");
                      setPassword(pw);
                      setConfirmPassword(pw);
                    }}
                  >
                    <Dices className="h-3 w-3" /> 随机生成
                  </button>
                </div>
                <Input type={password && password === confirmPassword && password.length >= 12 ? "text" : "password"} value={password} onChange={(e) => setPassword(e.target.value)} className="h-7 text-xs font-mono" placeholder="输入加密密码..." />
              </div>
              <div>
                <label className="text-xs text-muted-foreground">确认密码</label>
                <Input type="password" value={confirmPassword} onChange={(e) => setConfirmPassword(e.target.value)} className="h-7 text-xs font-mono" placeholder="再次输入密码..." />
              </div>
              {password && password === confirmPassword && password.length >= 12 && (
                <p className="text-[11px] text-emerald-600 dark:text-emerald-400 flex items-center gap-1">
                  <Check className="h-3 w-3" /> 密码已就绪，请妥善记录后发送给接收方
                </p>
              )}
            </div>
          )}
          {exportMode === "simple" && (
            <p className="text-[11px] text-amber-600 dark:text-amber-400">
              简单分享模式使用内置密钥，不能防止逆向工程。建议仅在信任的环境中使用。
            </p>
          )}
          <div className="flex justify-end">
            <Button
              size="sm"
              className="h-7 text-xs gap-1 text-white"
              style={{ backgroundColor: "var(--em-primary)" }}
              onClick={handleExport}
              disabled={exporting || (isAdminScope && !Object.entries(exportSections).some(([k, v]) => ["main","aux","vlm","embedding","profiles"].includes(k) && v))}
            >
              {exporting ? <Loader2 className="h-3 w-3 animate-spin" /> : <Download className="h-3 w-3" />}
              {exporting ? "加密中..." : "生成令牌"}
            </Button>
          </div>
        </div>
      )}

      {/* Export Result */}
      {mode === "export" && resultToken && (
        <div className="rounded-lg border border-border p-3 space-y-3">
          <div className="flex items-center gap-2">
            <CheckCircle2 className="h-4 w-4 text-emerald-500" />
            <span className="text-sm font-medium">配置导出成功</span>
          </div>
          <div className="relative">
            <textarea
              readOnly
              value={resultToken}
              className="w-full h-20 rounded-md border border-border bg-muted/30 px-3 py-2 text-[10px] font-mono resize-none focus:outline-none"
            />
            <Button
              size="sm"
              variant="outline"
              className="absolute top-2 right-2 h-6 text-[10px] gap-1"
              onClick={handleCopy}
            >
              {copied ? <Check className="h-3 w-3" /> : <Copy className="h-3 w-3" />}
              {copied ? "已复制" : "复制"}
            </Button>
          </div>
          {exportMode === "password" && (
            <p className="text-[11px] text-muted-foreground">
              请将此令牌和密码一起发送给接收方。没有密码无法解密。
            </p>
          )}
        </div>
      )}

      {/* Import Panel */}
      {mode === "import" && !importResult && (
        <div className="rounded-lg border border-dashed border-border p-3 space-y-3">
          <div>
            <label className="text-xs text-muted-foreground">粘贴配置令牌</label>
            <textarea
              value={importToken}
              onChange={(e) => handleDetectToken(e.target.value)}
              className="w-full h-20 rounded-md border border-border bg-background px-3 py-2 text-[10px] font-mono resize-none focus:outline-none focus:ring-1 focus:ring-ring mt-1"
              placeholder="粘贴 EMX1:... 令牌"
            />
          </div>
          {needsPassword === true && (
            <div>
              <label className="text-xs text-muted-foreground flex items-center gap-1">
                <Lock className="h-3 w-3" /> 此令牌需要密码
              </label>
              <Input
                type="password"
                value={importPassword}
                onChange={(e) => setImportPassword(e.target.value)}
                className="h-7 text-xs mt-1"
                placeholder="输入解密密码..."
              />
            </div>
          )}
          {needsPassword === false && (
            <p className="text-[11px] text-muted-foreground flex items-center gap-1">
              <Unlock className="h-3 w-3" /> 简单分享模式，无需密码
            </p>
          )}
          <div className="flex justify-end">
            <Button
              size="sm"
              className="h-7 text-xs gap-1 text-white"
              style={{ backgroundColor: "var(--em-primary)" }}
              onClick={handleImport}
              disabled={importing || !importToken.trim()}
            >
              {importing ? <Loader2 className="h-3 w-3 animate-spin" /> : <ClipboardPaste className="h-3 w-3" />}
              {importing ? "导入中..." : "导入配置"}
            </Button>
          </div>
        </div>
      )}

      {/* Import Result */}
      {mode === "import" && importResult && (
        <div className="rounded-lg border border-border p-3 space-y-2">
          <div className="flex items-center gap-2">
            <CheckCircle2 className="h-4 w-4 text-emerald-500" />
            <span className="text-sm font-medium">配置导入成功</span>
          </div>
          <div className="text-xs text-muted-foreground space-y-1">
            {Object.entries(importResult.imported).map(([key, value]) => (
              <p key={key}>
                <span className="font-medium">{sectionLabels[key] || key}</span>：
                {Array.isArray(value) ? value.join(", ") : String(value)}
              </p>
            ))}
          </div>
          <p className="text-[11px] text-amber-600 dark:text-amber-400">
            配置已生效。建议刷新页面以查看最新配置。
          </p>
        </div>
      )}

      {mode === "idle" && (
        <p className="text-xs text-muted-foreground text-center py-3">
          导出配置可加密分享给他人，导入令牌即可一键还原所有模型设置
        </p>
      )}
    </div>
  );
}

function CapabilityBadges({ caps }: { caps: ModelCapabilities | null }) {
  const items: { key: string; label: string; icon: React.ReactNode; value: boolean | null }[] = [
    { key: "tools", label: "工具", icon: <Wrench className="h-2.5 w-2.5" />, value: caps?.supports_tool_calling ?? null },
    { key: "vision", label: "视觉", icon: <ImageIcon className="h-2.5 w-2.5" />, value: caps?.supports_vision ?? null },
    { key: "thinking", label: "思考", icon: <Brain className="h-2.5 w-2.5" />, value: caps?.supports_thinking ?? null },
  ];

  return (
    <span className="inline-flex items-center gap-1 shrink-0 whitespace-nowrap">
      {items.map((item) => {
        let cls: string;
        let tip: string;

        if (item.value === true) {
          cls = "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400";
          tip = `${item.label}: 支持`;
        } else if (item.value === false) {
          cls = "bg-rose-500/10 text-rose-400/80 dark:text-rose-400/70";
          tip = `${item.label}: 不支持`;
        } else {
          cls = "bg-muted/60 text-muted-foreground/50";
          tip = `${item.label}: 未探测`;
        }

        return (
          <span
            key={item.key}
            title={tip}
            className={`inline-flex items-center gap-0.5 rounded-md px-1.5 py-0.5 text-[9px] leading-none font-medium transition-colors ${cls}`}
          >
            {item.icon}
            <span>{item.label}</span>
          </span>
        );
      })}
    </span>
  );
}

function CapabilityRow({
  icon,
  label,
  desc,
  value,
  error,
  onToggle,
}: {
  icon: React.ReactNode;
  label: string;
  desc: string;
  value: boolean | null;
  error?: string;
  onToggle: (v: boolean) => void;
}) {
  return (
    <div className="flex items-center gap-2.5 sm:gap-3 rounded-lg border border-border px-3 py-3 sm:py-2.5">
      <span
        className="flex-shrink-0"
        style={{ color: value === true ? "var(--em-primary)" : value === false ? "var(--destructive, #ef4444)" : "var(--muted-foreground)" }}
      >
        {icon}
      </span>
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-1.5">
          <span className="text-sm font-medium">{label}</span>
          {value === true && (
            <Badge className="text-[9px] h-4 bg-emerald-500/15 text-emerald-600 border-emerald-500/20">
              支持
            </Badge>
          )}
          {value === false && (
            <Badge variant="secondary" className="text-[9px] h-4">
              不支持
            </Badge>
          )}
          {value === null && (
            <Badge variant="outline" className="text-[9px] h-4">
              未知
            </Badge>
          )}
        </div>
        <p className="text-[11px] text-muted-foreground truncate">{desc}</p>
        {error && (
          <p className="text-[10px] text-destructive truncate mt-0.5" title={error}>
            {error}
          </p>
        )}
      </div>
      <Switch
        checked={value === true}
        onCheckedChange={onToggle}
        className="flex-shrink-0"
      />
    </div>
  );
}
