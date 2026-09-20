import {
  CODEX_OAUTH_PRESET,
  PROVIDER_PRESETS,
} from "../settings/model/constants";

export interface GuideStep {
  title: string;
  description: string;
}

export interface ProviderGuide {
  id: string;
  label: string;
  description: string;
  pricing: string;
  recommended?: boolean;
  purchaseUrl: string;
  model: string;
  base_url: string;
  protocol: string;
  thinking_mode: string;
  model_family: string;
  steps: GuideStep[];
}

type GuideCopy = Pick<ProviderGuide, "description" | "pricing" | "recommended" | "steps">;

// Provider identity, model ID, endpoint, protocol, thinking mode, family and logo
// are owned by settings/model/constants.tsx. This file only owns onboarding copy.
const GUIDE_COPY: Record<string, GuideCopy> = {
  openai: {
    description: "GPT-6 Astra — 通用能力强、生态完善",
    pricing: "按官方实时价格计费（请以 OpenAI 控制台为准）",
    recommended: true,
    steps: [
      { title: "1. 注册 OpenAI 账号", description: "打开 platform.openai.com，点击「Sign Up」注册。需要邮箱验证，部分地区可能需要手机号验证。" },
      { title: "2. 充值余额", description: "进入 Settings → Billing，添加支付方式并充值。OpenAI API 为预付费模式。" },
      { title: "3. 创建 API Key", description: "进入 API Keys 页面，点击「Create new secret key」，复制生成的 Key 并粘贴到下方。注意：Key 只显示一次，请妥善保存。" },
    ],
  },
  anthropic: {
    description: "Claude Sonnet 5 — 代码与推理能力一流",
    pricing: "按官方实时价格计费（请以 Anthropic 控制台为准）",
    recommended: true,
    steps: [
      { title: "1. 注册 Anthropic 账号", description: "打开 console.anthropic.com，使用邮箱注册并完成验证。" },
      { title: "2. 充值余额", description: "进入 Settings → Billing，添加信用卡并充值。最低 $5 起充。" },
      { title: "3. 创建 API Key", description: "进入 Settings → API Keys，点击「Create Key」，复制 Key 并粘贴到下方。" },
    ],
  },
  gemini: {
    description: "Gemini 3.8 Flash — 速度快、性价比高",
    pricing: "按官方实时价格计费（请以控制台为准）",
    steps: [
      { title: "1. 访问 Google AI Studio", description: "打开 aistudio.google.com/apikey，用 Google 账号登录后创建 API Key。Gemini 提供免费额度，无需预充值。" },
      { title: "2. 创建 API Key", description: "点击页面中的「Create API Key」按钮，选择一个 Google Cloud 项目（或创建新项目），即可生成 API Key。" },
      { title: "3. 复制 Key 并粘贴", description: "点击复制按钮将 API Key 复制到剪贴板，然后回到本页面粘贴到下方输入框中。" },
    ],
  },
  deepseek: {
    description: "DeepSeek-V4.1 Flash — 原生多模态，中文理解出色，性价比极高",
    pricing: "按官方实时价格计费（新用户通常有试用额度）",
    recommended: true,
    steps: [
      { title: "1. 注册 DeepSeek 账号", description: "打开 platform.deepseek.com，使用手机号或邮箱注册。新用户注册可获赠免费 token 额度。" },
      { title: "2. 创建 API Key", description: "登录后进入「API Keys」页面，点击「创建 API Key」，为 Key 取一个名称后确认。" },
      { title: "3. 复制并充值（可选）", description: "复制生成的 API Key 并粘贴到下方。免费额度用完后可在「充值」页面充值。" },
    ],
  },
  qwen: {
    description: "通义千问 Qwen3.8 Max — 原生多模态，国内直连",
    pricing: "按官方实时价格计费（新用户通常有试用额度）",
    recommended: true,
    steps: [
      { title: "1. 注册阿里云账号", description: "使用手机号注册阿里云账号（或用已有账号登录），首次使用需开通「百炼」服务。" },
      { title: "2. 获取 API Key", description: "进入百炼控制台 → API Key 管理页面，点击「创建 API Key」即可生成。" },
      { title: "3. 复制并使用", description: "复制 API Key 粘贴到下方。阿里云百炼在国内直连，延迟低且稳定。" },
    ],
  },
  zhipu: {
    description: "GLM-5.3 — 国产大模型，国内直连",
    pricing: "按官方实时价格计费（新用户通常有试用额度）",
    steps: [
      { title: "1. 注册智谱 AI 账号", description: "打开 open.bigmodel.cn，使用手机号注册并完成实名认证。" },
      { title: "2. 获取 API Key", description: "进入用户中心 → API Keys 页面，点击「新建 API Key」即可生成。" },
      { title: "3. 复制并使用", description: "复制 Key 粘贴到下方。智谱 AI 国内直连，新注册用户有免费 token 额度。" },
    ],
  },
  openrouter: {
    description: "全球模型聚合路由 — 一个 Key 用遍全球",
    pricing: "按模型计费，支持多种支付方式",
    steps: [
      { title: "1. 注册 OpenRouter 账号", description: "打开 openrouter.ai，使用 Google 或 GitHub 账号快速注册。" },
      { title: "2. 充值并获取 Key", description: "进入 Keys 页面创建新的 API Key，并在 Credits 页面充值。" },
      { title: "3. 复制并使用", description: "复制 Key 粘贴到下方。OpenRouter 支持多家模型的统一接口调用。" },
    ],
  },
  kimi: {
    description: "Kimi K3 — 超长上下文、中文理解出色",
    pricing: "按官方实时价格计费（新用户通常有试用额度）",
    steps: [
      { title: "1. 注册 Moonshot 账号", description: "打开 platform.moonshot.cn，使用手机号注册并完成验证。" },
      { title: "2. 创建 API Key", description: "进入控制台 → API Key 管理页面，点击「新建 API Key」即可生成。" },
      { title: "3. 复制并使用", description: "复制 Key 粘贴到下方。Kimi 国内直连，新注册用户有免费 token 额度。" },
    ],
  },
  minimax: {
    description: "MiniMax M3 — 通用推理与多模态能力",
    pricing: "按官方实时价格计费（请以 MiniMax 控制台为准）",
    steps: [
      { title: "1. 注册 MiniMax 账号", description: "打开 platform.minimax.io，注册并完成账号验证。" },
      { title: "2. 创建 API Key", description: "进入控制台的 API Keys 页面创建密钥。" },
      { title: "3. 复制并使用", description: "将 API Key 粘贴到下方，Base URL 使用 https://api.minimax.io/v1。" },
    ],
  },
  xai: {
    description: "Grok 4.6 — 强推理与实时信息能力",
    pricing: "按官方实时价格计费（请以 xAI 控制台为准）",
    steps: [
      { title: "1. 注册 xAI 账号", description: "打开 console.x.ai，使用 X 账号登录并完成验证。" },
      { title: "2. 创建 API Key", description: "进入 API Keys 页面创建密钥并配置项目额度。" },
      { title: "3. 复制并使用", description: "将 API Key 粘贴到下方，Base URL 使用 https://api.x.ai/v1。" },
    ],
  },
  doubao: {
    description: "Doubao Seed 2.1 Pro — 国内直连，中文任务表现稳定",
    pricing: "按官方实时价格计费（新用户通常有试用额度）",
    steps: [
      { title: "1. 注册火山引擎账号", description: "打开火山引擎控制台，完成实名认证并进入方舟服务。" },
      { title: "2. 创建 API Key", description: "在方舟控制台创建 API Key，并开通对应模型的调用权限。" },
      { title: "3. 复制并使用", description: "将 API Key 粘贴到下方；模型部署 ID 也可以在方舟控制台中替换为实际 endpoint。" },
    ],
  },
  "openai-codex": {
    description: "ChatGPT Plus/Pro 订阅模型（支持 Codex 登录）",
    pricing: "使用 ChatGPT Plus/Pro 订阅，无需单独 API 充值",
    steps: [
      { title: "1. 登录 ChatGPT", description: "确保账号已开通 ChatGPT Plus/Pro 的 Codex 订阅能力。" },
      { title: "2. 完成 Codex 授权", description: "进入「设置 → 模型配置」，使用 OpenAI Codex 区域的浏览器授权或 auth.json 完成连接。" },
      { title: "3. 保存模型", description: "使用 openai-codex/gpt-6-astra 作为 Model ID，即可在模型选择器中使用 Codex。" },
    ],
  },
};

const CANONICAL_PRESETS = [...PROVIDER_PRESETS, CODEX_OAUTH_PRESET];

export const PROVIDER_GUIDES: ProviderGuide[] = CANONICAL_PRESETS.map((preset) => ({
  id: preset.id,
  label: preset.label,
  ...GUIDE_COPY[preset.id],
  purchaseUrl: preset.purchaseUrl,
  model: preset.model,
  base_url: preset.base_url,
  protocol: preset.protocol,
  thinking_mode: preset.thinking_mode,
  model_family: preset.model_family,
}));
