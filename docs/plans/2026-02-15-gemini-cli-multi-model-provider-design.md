# gemini-cli 多模型 Provider 抽象层设计

> 创建时间：2026-02-15
> 状态：已确认，待实施
> 关联：`migration/README.md` 第 9.4 项（多模型提供商适配）

---

## 一、背景与目标

gemini-cli 当前深度耦合 Google `@google/genai` SDK，`ContentGenerator` 接口、`GeminiChat`、`BaseLlmClient` 全部基于 Gemini 原生类型（`Content`, `Part`, `GenerateContentResponse`）。认证仅支持 Gemini 系列（OAuth / API Key / Vertex AI），没有 Provider 抽象层。

**目标**：在 `packages/core/src/providers/` 新增 Provider 抽象层，使 gemini-cli 可扩展支持多个 LLM Provider（OpenAI、Claude 等），同时保持现有行为零变化。

**设计决策**：
- 采用 **OpenAI Chat Completions 格式**作为中间表示
- 第一批仅实现 **Gemini adapter**，其他 Provider 留接口
- **流式（streaming）从第一版必须支持**
- 代码放在 `gemini-cli/packages/core/src/providers/` 内
- 通过功能开关控制，**默认关闭**

---

## 二、架构总览

```
┌─────────────────────────────────────────────────┐
│  GeminiChat / client.ts / BaseLlmClient         │  ← Phase 1 不改
│  (uses ContentGenerator interface)               │
├─────────────────────────────────────────────────┤
│  ProviderBridge  (implements ContentGenerator)   │  ← 新增
│  ┌───────────────┐    ┌──────────────────────┐  │
│  │ Gemini→OpenAI  │ ←→ │ OpenAI→Gemini        │  │
│  │ request convert│    │ response convert     │  │
│  └───────┬───────┘    └──────────┬───────────┘  │
├──────────┼───────────────────────┼──────────────┤
│          ▼                       ▲               │
│  LLMProvider interface (OpenAI-format types)     │  ← 新增
├─────────────────────────────────────────────────┤
│  GeminiProvider  │  (future) OpenAIProvider │ …  │  ← 新增
│  wraps @google/  │  wraps openai SDK       │     │
│  genai SDK       │                         │     │
└─────────────────────────────────────────────────┘
```

**数据流（Phase 1）**：

```
GeminiChat 发出请求 (Gemini 格式)
  → ProviderBridge.generateContentStream()
    → geminiRequestToOpenAI()          // Gemini → OpenAI 格式
    → GeminiProvider.generateStream()  // OpenAI → Gemini SDK 调用
    → openaiChunkToGemini()            // OpenAI chunk → Gemini chunk
  → GeminiChat 接收响应 (Gemini 格式)
```

---

## 三、统一类型定义

文件：`src/providers/types.ts`

与 OpenAI Chat Completions API 1:1 对齐，任何 OpenAI 兼容端点可零成本接入。

### 3.1 消息类型

```typescript
export interface ChatMessage {
  role: 'system' | 'user' | 'assistant' | 'tool';
  content: string | null;
  tool_calls?: ToolCall[];
  tool_call_id?: string;       // 当 role='tool' 时必填
}

export interface ToolCall {
  id: string;
  type: 'function';
  function: { name: string; arguments: string }; // arguments 为 JSON 字符串
}
```

### 3.2 工具定义

```typescript
export interface ToolDefinition {
  type: 'function';
  function: {
    name: string;
    description: string;
    parameters?: Record<string, unknown>;
  };
}

export type ToolChoice =
  | 'auto' | 'none' | 'required'
  | { type: 'function'; function: { name: string } };
```

### 3.3 请求参数

```typescript
export interface GenerateParams {
  model: string;
  messages: ChatMessage[];
  tools?: ToolDefinition[];
  tool_choice?: ToolChoice;
  temperature?: number;
  max_tokens?: number;
  response_format?: { type: 'json_object' | 'text' };
  abort_signal?: AbortSignal;
}
```

### 3.4 响应

```typescript
export interface ChatCompletionResponse {
  id: string;
  model: string;
  choices: ChatChoice[];
  usage: TokenUsage;
}

export interface ChatChoice {
  index: number;
  message: ChatMessage;
  finish_reason: 'stop' | 'tool_calls' | 'length' | 'content_filter';
}

export interface TokenUsage {
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
}
```

### 3.5 流式

```typescript
export interface ChatCompletionChunk {
  id: string;
  model: string;
  choices: ChatChunkChoice[];
  usage?: TokenUsage;          // 最后一个 chunk 可能携带
}

export interface ChatChunkChoice {
  index: number;
  delta: Partial<ChatMessage>;
  finish_reason: string | null;
}
```

---

## 四、Provider 接口

文件：`src/providers/provider.ts`

```typescript
export interface LLMProvider {
  readonly name: string;           // e.g. 'gemini', 'openai', 'claude'

  generate(params: GenerateParams): Promise<ChatCompletionResponse>;

  generateStream(params: GenerateParams): AsyncGenerator<ChatCompletionChunk>;

  countTokens?(messages: ChatMessage[], model: string): Promise<number>;

  embedContent?(input: string, model: string): Promise<number[]>;

  close?(): Promise<void>;
}
```

---

## 五、文件结构

```
packages/core/src/providers/
├── types.ts                    # OpenAI 格式统一类型
├── provider.ts                 # LLMProvider 接口
├── bridge.ts                   # ProviderBridge (LLMProvider → ContentGenerator)
├── gemini/
│   ├── adapter.ts              # GeminiProvider 实现
│   └── converter.ts            # OpenAI ↔ Gemini 格式转换器
└── index.ts                    # 导出 + createProvider 工厂
```

---

## 六、核心模块设计

### 6.1 ProviderBridge (`bridge.ts`)

实现现有 `ContentGenerator` 接口，内部委托给 `LLMProvider`。

```typescript
class ProviderBridge implements ContentGenerator {
  constructor(private provider: LLMProvider) {}

  async generateContent(
    request: GenerateContentParameters,
    userPromptId: string,
  ): Promise<GenerateContentResponse> {
    const params = geminiRequestToOpenAI(request);
    const response = await this.provider.generate(params);
    return openaiResponseToGemini(response);
  }

  async *generateContentStream(
    request: GenerateContentParameters,
    userPromptId: string,
  ): AsyncGenerator<GenerateContentResponse> {
    const params = geminiRequestToOpenAI(request);
    for await (const chunk of this.provider.generateStream(params)) {
      yield openaiChunkToGemini(chunk);
    }
  }

  async countTokens(request: CountTokensParameters): Promise<CountTokensResponse> {
    // 委托给 provider.countTokens 或 fallback 估算
  }

  async embedContent(request: EmbedContentParameters): Promise<EmbedContentResponse> {
    // 委托给 provider.embedContent 或抛错
  }
}
```

### 6.2 GeminiProvider (`gemini/adapter.ts`)

```typescript
class GeminiProvider implements LLMProvider {
  readonly name = 'gemini';
  private readonly genai: GoogleGenAI;

  constructor(config: GeminiProviderConfig) {
    this.genai = new GoogleGenAI({ apiKey: config.apiKey, ... });
  }

  async generate(params: GenerateParams): Promise<ChatCompletionResponse> {
    const geminiRequest = openaiToGeminiRequest(params);
    const response = await this.genai.models.generateContent(geminiRequest);
    return geminiToOpenaiResponse(response, params.model);
  }

  async *generateStream(params: GenerateParams): AsyncGenerator<ChatCompletionChunk> {
    const geminiRequest = openaiToGeminiRequest(params);
    const stream = await this.genai.models.generateContentStream(geminiRequest);
    for await (const chunk of stream) {
      yield geminiChunkToOpenai(chunk, params.model);
    }
  }
}
```

### 6.3 Converter (`gemini/converter.ts`)

从 ExcelManus `providers/gemini.py` 移植的核心转换逻辑：

| Python 函数 | TypeScript 对应 |
|-------------|----------------|
| `_openai_messages_to_gemini()` | `openaiMessagesToGemini()` |
| `_gemini_response_to_openai()` | `geminiResponseToOpenai()` |
| `_openai_tools_to_gemini()` | `openaiToolsToGemini()` |
| `_map_openai_tool_choice_to_gemini()` | `mapToolChoiceToGemini()` |
| `_clean_schema_for_gemini()` | `cleanSchemaForGemini()` |
| `_merge_consecutive_roles()` | `mergeConsecutiveRoles()` |

Bridge 层额外需要反向转换（GeminiChat 发来的 Gemini 格式 → OpenAI 格式）：

| 函数 | 方向 |
|------|------|
| `geminiRequestToOpenAI()` | Gemini `GenerateContentParameters` → OpenAI `GenerateParams` |
| `openaiResponseToGemini()` | OpenAI `ChatCompletionResponse` → Gemini `GenerateContentResponse` |
| `openaiChunkToGemini()` | OpenAI `ChatCompletionChunk` → Gemini `GenerateContentResponse` (chunk) |

---

## 七、集成策略

### 7.1 功能开关

环境变量：`GEMINI_CLI_USE_PROVIDER_ABSTRACTION=1`

在 `contentGenerator.ts` 的 `createContentGenerator()` 中增加分支：

```typescript
// 在现有逻辑之前
if (process.env['GEMINI_CLI_USE_PROVIDER_ABSTRACTION'] === '1') {
  const provider = createProvider('gemini', config);
  const bridge = new ProviderBridge(provider);
  return new LoggingContentGenerator(bridge, gcConfig);
}
// ... 现有逻辑不变
```

### 7.2 修改范围

**现有文件修改**：仅 `contentGenerator.ts`，增加 ~20 行条件分支。

**新增文件**：6 个文件，约 1200-1500 行。

**行为变化**：功能开关默认关闭，零行为变化。

---

## 八、迁移路径

### Phase 1（本次）
- 新增 `src/providers/` 全部文件
- 修改 `contentGenerator.ts` 加功能开关
- 单元测试覆盖格式转换
- 集成测试覆盖 ProviderBridge 链路

### Phase 2（后续）
- 新增 `OpenAIProvider`、`ClaudeProvider`
- 配置体系支持 provider 选择 + 多 Provider 凭证管理
- 开关默认打开或移除开关

### Phase 3（远期）
- GeminiChat 直接依赖 `LLMProvider` 接口
- 移除 ProviderBridge 中间层
- 统一消息格式贯穿全栈

---

## 九、测试策略

| 测试类型 | 覆盖范围 | 工具 |
|----------|----------|------|
| Converter 单元测试 | messages/tools/tool_choice/response 双向转换 | vitest |
| Bridge 集成测试 | 通过 FakeContentGenerator 模式验证完整链路 | vitest |
| 回归测试 | 开关开/关两种路径跑现有测试套件 | vitest + 现有 evals |

---

## 十、风险与缓解

| 风险 | 缓解措施 |
|------|----------|
| 双重转换性能开销 | Phase 1 可忽略（纯内存操作）；Phase 3 去掉 Bridge 层 |
| Gemini SDK 更新破坏转换逻辑 | Converter 单元测试覆盖，CI 自动检测 |
| 功能开关导致两条路径长期并存 | Phase 2 完成后移除开关 |
| Streaming chunk 格式不一致 | 严格测试 chunk 边界场景（空 chunk、多 tool_calls 等） |
