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

export const PROVIDER_GUIDES: ProviderGuide[] = [
  {
    id: "gemini",
    label: "Google Gemini",
    description: "Gemini 2.5 Flash — 速度快、性价比高，推荐新手首选",
    pricing: "按官方实时价格计费（请以控制台为准）",
    recommended: true,
    purchaseUrl: "https://aistudio.google.com/apikey",
    model: "gemini-2.5-flash",
    base_url: "https://generativelanguage.googleapis.com/v1beta/openai",
    protocol: "openai",
    thinking_mode: "auto",
    model_family: "gemini",
    steps: [
      {
        title: "1. 访问 Google AI Studio",
        description:
          "打开 aistudio.google.com/apikey，使用 Google 账号登录。如果是首次使用，需要同意服务条款。",
      },
      {
        title: "2. 创建 API Key",
        description:
          '点击页面中的「Create API Key」按钮，选择一个 Google Cloud 项目（或创建新项目），即可生成 API Key。',
      },
      {
        title: "3. 复制 Key 并粘贴",
        description:
          "点击复制按钮将 API Key 复制到剪贴板，然后回到本页面粘贴到下方输入框中。Gemini 提供免费额度，无需预充值。",
      },
    ],
  },
  {
    id: "deepseek",
    label: "DeepSeek",
    description: "DeepSeek-V3 — 中文理解出色，性价比极高",
    pricing: "按官方实时价格计费（新用户通常有试用额度）",
    recommended: true,
    purchaseUrl: "https://platform.deepseek.com/api_keys",
    model: "deepseek-chat",
    base_url: "https://api.deepseek.com/v1",
    protocol: "openai",
    thinking_mode: "deepseek",
    model_family: "deepseek",
    steps: [
      {
        title: "1. 注册 DeepSeek 账号",
        description:
          "打开 platform.deepseek.com，使用手机号或邮箱注册。新用户注册可获赠免费 token 额度。",
      },
      {
        title: "2. 创建 API Key",
        description:
          '登录后进入「API Keys」页面，点击「创建 API Key」，为 Key 取一个名称后确认。',
      },
      {
        title: "3. 复制并充值（可选）",
        description:
          "复制生成的 API Key 并粘贴到下方。免费额度用完后可在「充值」页面充值，最低 ¥10 起。",
      },
    ],
  },
  {
    id: "openai",
    label: "OpenAI",
    description: "GPT-5 — 通用能力强、生态完善",
    pricing: "按官方实时价格计费（请以 OpenAI 控制台为准）",
    purchaseUrl: "https://platform.openai.com/api-keys",
    model: "gpt-5",
    base_url: "https://api.openai.com/v1",
    protocol: "openai",
    thinking_mode: "auto",
    model_family: "gpt",
    steps: [
      {
        title: "1. 注册 OpenAI 账号",
        description:
          "打开 platform.openai.com，点击「Sign Up」注册。需要邮箱验证，部分地区可能需要手机号验证。",
      },
      {
        title: "2. 充值余额",
        description:
          '进入 Settings → Billing，添加支付方式并充值。最低 $5 起充。OpenAI API 为预付费模式。',
      },
      {
        title: "3. 创建 API Key",
        description:
          '进入 API Keys 页面，点击「Create new secret key」，复制生成的 Key 并粘贴到下方。注意：Key 只显示一次，请妥善保存。',
      },
    ],
  },
  {
    id: "anthropic",
    label: "Anthropic",
    description: "Claude Sonnet 4 — 代码与推理能力一流",
    pricing: "按官方实时价格计费（请以 Anthropic 控制台为准）",
    purchaseUrl: "https://console.anthropic.com/settings/keys",
    model: "claude-sonnet-4",
    base_url: "https://api.anthropic.com",
    protocol: "anthropic",
    thinking_mode: "claude",
    model_family: "claude",
    steps: [
      {
        title: "1. 注册 Anthropic 账号",
        description:
          "打开 console.anthropic.com，使用邮箱注册并完成验证。",
      },
      {
        title: "2. 充值余额",
        description:
          '进入 Settings → Billing，添加信用卡并充值。最低 $5 起充。',
      },
      {
        title: "3. 创建 API Key",
        description:
          '进入 Settings → API Keys，点击「Create Key」，复制 Key 并粘贴到下方。',
      },
    ],
  },
  {
    id: "qwen",
    label: "阿里云百炼",
    description: "通义千问 Qwen — 国内直连、中文优化",
    pricing: "按官方实时价格计费（新用户通常有试用额度）",
    recommended: true,
    purchaseUrl: "https://dashscope.console.aliyun.com/apiKey",
    model: "qwen-plus",
    base_url: "https://dashscope.aliyuncs.com/compatible-mode/v1",
    protocol: "openai",
    thinking_mode: "enable_thinking",
    model_family: "qwen",
    steps: [
      {
        title: "1. 注册阿里云账号",
        description:
          "打开 aliyun.com，使用手机号注册阿里云账号（或用已有账号登录）。首次使用需开通「百炼」服务。",
      },
      {
        title: "2. 获取 API Key",
        description:
          '进入百炼控制台 → API Key 管理页面，点击「创建 API Key」即可生成。',
      },
      {
        title: "3. 复制并使用",
        description:
          "复制 API Key 粘贴到下方。阿里云百炼在国内直连无需代理，延迟低且稳定。新用户有免费额度可用。",
      },
    ],
  },
  {
    id: "zhipu",
    label: "智谱 AI",
    description: "GLM-4 Plus — 国产大模型，国内直连",
    pricing: "按官方实时价格计费（新用户通常有试用额度）",
    purchaseUrl: "https://open.bigmodel.cn/usercenter/apikeys",
    model: "glm-4-plus",
    base_url: "https://open.bigmodel.cn/api/paas/v4",
    protocol: "openai",
    thinking_mode: "glm_thinking",
    model_family: "glm",
    steps: [
      {
        title: "1. 注册智谱 AI 账号",
        description:
          "打开 open.bigmodel.cn，使用手机号注册并完成实名认证。",
      },
      {
        title: "2. 获取 API Key",
        description:
          '进入用户中心 → API Keys 页面，点击「新建 API Key」即可生成。',
      },
      {
        title: "3. 复制并使用",
        description:
          "复制 Key 粘贴到下方。智谱 AI 国内直连，新注册用户有免费 token 额度。",
      },
    ],
  },
  {
    id: "openai-codex",
    label: "OpenAI Codex",
    description: "ChatGPT Plus/Pro 订阅模型（支持 Codex 登录）",
    pricing: "使用 ChatGPT Plus/Pro 订阅，无需单独 API 充值",
    purchaseUrl: "https://chatgpt.com",
    model: "openai-codex/gpt-5.3-codex-spark",
    base_url: "https://api.openai.com/v1",
    protocol: "openai",
    thinking_mode: "openai_reasoning",
    model_family: "gpt",
    steps: [
      {
        title: "1. 登录 ChatGPT",
        description:
          "确保你的账号已开通 ChatGPT Plus/Pro（Codex 订阅能力）。",
      },
      {
        title: "2. 在模型设置中完成 Codex 授权",
        description:
          "进入「设置 → 模型配置」，使用 OpenAI Codex 区域的「浏览器授权」或粘贴 auth.json 完成连接。",
      },
      {
        title: "3. 预填并保存模型",
        description:
          "将 Model ID 设置为 openai-codex/gpt-5.3-codex-spark 并保存，即可在模型选择器中使用 Codex Spark。",
      },
    ],
  },
  {
    id: "openrouter",
    label: "OpenRouter",
    description: "全球模型聚合路由 — 一个 Key 用遍全球模型",
    pricing: "按模型计费，支持多种支付方式",
    purchaseUrl: "https://openrouter.ai/keys",
    model: "anthropic/claude-sonnet-4",
    base_url: "https://openrouter.ai/api/v1",
    protocol: "openai",
    thinking_mode: "openrouter",
    model_family: "",
    steps: [
      {
        title: "1. 注册 OpenRouter 账号",
        description:
          "打开 openrouter.ai，使用 Google 或 GitHub 账号快速注册。",
      },
      {
        title: "2. 充值并获取 Key",
        description:
          '进入 Keys 页面，创建新的 API Key。在 Credits 页面充值（支持信用卡、加密货币等）。',
      },
      {
        title: "3. 复制并使用",
        description:
          "复制 Key 粘贴到下方。OpenRouter 支持 OpenAI/Anthropic/Google 等多家模型，统一接口调用。",
      },
    ],
  },
];

export const PROVIDER_LOGO_SLUG: Record<string, string> = {
  openai: "openai",
  anthropic: "anthropic",
  gemini: "gemini",
  deepseek: "deepseek",
  qwen: "qwen",
  zhipu: "zhipu",
  "openai-codex": "openai",
  openrouter: "openrouter",
};
