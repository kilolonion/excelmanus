# gemini-cli 多模型 Provider 抽象层 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 在 gemini-cli/packages/core 内新增 Provider 抽象层，以 OpenAI Chat Completions 格式为中间表示，第一版仅实现 Gemini adapter，通过功能开关控制。

**Architecture:** `LLMProvider` 接口使用 OpenAI 格式类型。`GeminiProvider` 实现该接口，内部包装 `@google/genai` SDK。`ProviderBridge` 实现现有 `ContentGenerator` 接口，做 Gemini ↔ OpenAI 格式的双向转换。功能开关 `GEMINI_CLI_USE_PROVIDER_ABSTRACTION=1` 控制是否走新路径。

**Tech Stack:** TypeScript, `@google/genai` SDK, vitest

**Design Doc:** `docs/plans/2026-02-15-gemini-cli-multi-model-provider-design.md`

---

## Task 1: 统一类型定义

**Files:**
- Create: `gemini-cli/packages/core/src/providers/types.ts`
- Test: `gemini-cli/packages/core/src/providers/types.test.ts`

**Step 1: 创建类型定义文件**

```typescript
// gemini-cli/packages/core/src/providers/types.ts

// ─── Messages ───────────────────────────────────
export interface ChatMessage {
  role: 'system' | 'user' | 'assistant' | 'tool';
  content: string | null;
  tool_calls?: ToolCall[];
  tool_call_id?: string;
}

export interface ToolCall {
  id: string;
  type: 'function';
  function: { name: string; arguments: string };
}

// ─── Tool Definitions ───────────────────────────
export interface ToolDefinition {
  type: 'function';
  function: {
    name: string;
    description: string;
    parameters?: Record<string, unknown>;
  };
}

export type ToolChoice =
  | 'auto'
  | 'none'
  | 'required'
  | { type: 'function'; function: { name: string } };

// ─── Request ────────────────────────────────────
export interface GenerateParams {
  model: string;
  messages: ChatMessage[];
  tools?: ToolDefinition[];
  tool_choice?: ToolChoice;
  temperature?: number;
  max_tokens?: number;
  top_p?: number;
  top_k?: number;
  response_format?: { type: 'json_object' | 'text' };
  abort_signal?: AbortSignal;
}

// ─── Response ───────────────────────────────────
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

// ─── Streaming ──────────────────────────────────
export interface ChatCompletionChunk {
  id: string;
  model: string;
  choices: ChatChunkChoice[];
  usage?: TokenUsage;
}

export interface ChatChunkChoice {
  index: number;
  delta: Partial<ChatMessage>;
  finish_reason: string | null;
}
```

**Step 2: 写类型编译验证测试**

```typescript
// gemini-cli/packages/core/src/providers/types.test.ts
import { describe, it, expect } from 'vitest';
import type {
  ChatMessage,
  ToolCall,
  ToolDefinition,
  ToolChoice,
  GenerateParams,
  ChatCompletionResponse,
  ChatCompletionChunk,
} from './types.js';

describe('Provider types', () => {
  it('ChatMessage supports all roles', () => {
    const msgs: ChatMessage[] = [
      { role: 'system', content: 'You are helpful.' },
      { role: 'user', content: 'Hello' },
      { role: 'assistant', content: 'Hi!', tool_calls: [{ id: 'tc1', type: 'function', function: { name: 'read', arguments: '{}' } }] },
      { role: 'tool', content: '{"result": "ok"}', tool_call_id: 'tc1' },
    ];
    expect(msgs).toHaveLength(4);
  });

  it('ToolChoice supports all variants', () => {
    const choices: ToolChoice[] = [
      'auto',
      'none',
      'required',
      { type: 'function', function: { name: 'read_cells' } },
    ];
    expect(choices).toHaveLength(4);
  });

  it('GenerateParams is constructible', () => {
    const params: GenerateParams = {
      model: 'gemini-2.5-flash',
      messages: [{ role: 'user', content: 'hi' }],
      tools: [{ type: 'function', function: { name: 'test', description: 'test tool' } }],
      tool_choice: 'auto',
      temperature: 0.7,
    };
    expect(params.model).toBe('gemini-2.5-flash');
  });

  it('ChatCompletionResponse is constructible', () => {
    const resp: ChatCompletionResponse = {
      id: 'resp-1',
      model: 'gemini-2.5-flash',
      choices: [{
        index: 0,
        message: { role: 'assistant', content: 'Hello!' },
        finish_reason: 'stop',
      }],
      usage: { prompt_tokens: 10, completion_tokens: 5, total_tokens: 15 },
    };
    expect(resp.choices[0].finish_reason).toBe('stop');
  });

  it('ChatCompletionChunk is constructible', () => {
    const chunk: ChatCompletionChunk = {
      id: 'chunk-1',
      model: 'gemini-2.5-flash',
      choices: [{ index: 0, delta: { content: 'Hel' }, finish_reason: null }],
    };
    expect(chunk.choices[0].delta.content).toBe('Hel');
  });
});
```

**Step 3: 运行测试确认通过**

Run: `cd gemini-cli && npx vitest run src/providers/types.test.ts`
Expected: 5 tests PASS

**Step 4: Commit**

```bash
git add gemini-cli/packages/core/src/providers/types.ts gemini-cli/packages/core/src/providers/types.test.ts
git commit -m "feat(providers): add unified OpenAI-format type definitions"
```

---

## Task 2: LLMProvider 接口

**Files:**
- Create: `gemini-cli/packages/core/src/providers/provider.ts`

**Step 1: 创建接口文件**

```typescript
// gemini-cli/packages/core/src/providers/provider.ts
import type {
  ChatMessage,
  GenerateParams,
  ChatCompletionResponse,
  ChatCompletionChunk,
} from './types.js';

export interface LLMProvider {
  readonly name: string;

  generate(params: GenerateParams): Promise<ChatCompletionResponse>;

  generateStream(
    params: GenerateParams,
  ): AsyncGenerator<ChatCompletionChunk>;

  countTokens?(
    messages: ChatMessage[],
    model: string,
  ): Promise<number>;

  embedContent?(input: string, model: string): Promise<number[]>;

  close?(): Promise<void>;
}
```

**Step 2: Commit**

```bash
git add gemini-cli/packages/core/src/providers/provider.ts
git commit -m "feat(providers): add LLMProvider interface"
```

---

## Task 3: Gemini ↔ OpenAI 格式转换器

**Files:**
- Create: `gemini-cli/packages/core/src/providers/gemini/converter.ts`
- Test: `gemini-cli/packages/core/src/providers/gemini/converter.test.ts`

这是最大的单个任务（~500 行实现 + ~400 行测试）。转换器包含以下函数：

### 3a. OpenAI → Gemini 请求转换

**Step 1: 写 `openaiMessagesToGemini` 的失败测试**

```typescript
// gemini-cli/packages/core/src/providers/gemini/converter.test.ts
import { describe, it, expect } from 'vitest';
import {
  openaiMessagesToGemini,
  openaiToolsToGemini,
  mapToolChoiceToGemini,
  geminiResponseToOpenai,
  geminiChunkToOpenai,
  geminiRequestToOpenai,
  openaiResponseToGemini,
} from './converter.js';
import type { ChatMessage, ToolDefinition, ToolChoice } from '../types.js';

describe('openaiMessagesToGemini', () => {
  it('extracts system messages into systemInstruction', () => {
    const messages: ChatMessage[] = [
      { role: 'system', content: 'Be helpful.' },
      { role: 'user', content: 'Hello' },
    ];
    const { systemInstruction, contents } = openaiMessagesToGemini(messages);
    expect(systemInstruction).toEqual({ parts: [{ text: 'Be helpful.' }] });
    expect(contents).toHaveLength(1);
    expect(contents[0].role).toBe('user');
  });

  it('maps assistant role to model', () => {
    const messages: ChatMessage[] = [
      { role: 'user', content: 'Hi' },
      { role: 'assistant', content: 'Hello!' },
    ];
    const { contents } = openaiMessagesToGemini(messages);
    expect(contents[1].role).toBe('model');
    expect(contents[1].parts[0].text).toBe('Hello!');
  });

  it('converts tool_calls to functionCall parts', () => {
    const messages: ChatMessage[] = [
      { role: 'user', content: 'Read data' },
      {
        role: 'assistant',
        content: null,
        tool_calls: [{
          id: 'tc1',
          type: 'function',
          function: { name: 'read_cells', arguments: '{"range":"A1:B5"}' },
        }],
      },
    ];
    const { contents } = openaiMessagesToGemini(messages);
    const modelParts = contents[1].parts;
    expect(modelParts[0].functionCall).toEqual({
      name: 'read_cells',
      args: { range: 'A1:B5' },
    });
  });

  it('converts tool result to functionResponse', () => {
    const messages: ChatMessage[] = [
      { role: 'user', content: 'Read data' },
      {
        role: 'assistant',
        content: null,
        tool_calls: [{
          id: 'tc1',
          type: 'function',
          function: { name: 'read_cells', arguments: '{}' },
        }],
      },
      { role: 'tool', content: '{"data": [1,2,3]}', tool_call_id: 'tc1' },
    ];
    const { contents } = openaiMessagesToGemini(messages);
    // tool result mapped to user role with functionResponse
    const toolContent = contents[2];
    expect(toolContent.role).toBe('user');
    expect(toolContent.parts[0].functionResponse).toBeDefined();
    expect(toolContent.parts[0].functionResponse.name).toBe('read_cells');
  });

  it('merges consecutive same-role messages', () => {
    const messages: ChatMessage[] = [
      { role: 'user', content: 'First' },
      { role: 'user', content: 'Second' },
    ];
    const { contents } = openaiMessagesToGemini(messages);
    expect(contents).toHaveLength(1);
    expect(contents[0].parts).toHaveLength(2);
  });
});

describe('openaiToolsToGemini', () => {
  it('converts tool definitions to functionDeclarations', () => {
    const tools: ToolDefinition[] = [{
      type: 'function',
      function: {
        name: 'read_cells',
        description: 'Read cells from a range',
        parameters: { type: 'object', properties: { range: { type: 'string' } } },
      },
    }];
    const result = openaiToolsToGemini(tools);
    expect(result).toHaveLength(1);
    expect(result![0].functionDeclarations).toHaveLength(1);
    expect(result![0].functionDeclarations[0].name).toBe('read_cells');
  });

  it('returns undefined for empty tools', () => {
    expect(openaiToolsToGemini(undefined)).toBeUndefined();
    expect(openaiToolsToGemini([])).toBeUndefined();
  });
});

describe('mapToolChoiceToGemini', () => {
  it('maps auto to AUTO mode', () => {
    const result = mapToolChoiceToGemini('auto');
    expect(result?.functionCallingConfig?.mode).toBe('AUTO');
  });

  it('maps required to ANY mode', () => {
    const result = mapToolChoiceToGemini('required');
    expect(result?.functionCallingConfig?.mode).toBe('ANY');
  });

  it('maps none to NONE mode', () => {
    const result = mapToolChoiceToGemini('none');
    expect(result?.functionCallingConfig?.mode).toBe('NONE');
  });

  it('maps specific function to ANY with allowedFunctionNames', () => {
    const choice: ToolChoice = { type: 'function', function: { name: 'read_cells' } };
    const result = mapToolChoiceToGemini(choice);
    expect(result?.functionCallingConfig?.mode).toBe('ANY');
    expect(result?.functionCallingConfig?.allowedFunctionNames).toEqual(['read_cells']);
  });
});
```

**Step 2: 运行测试确认失败**

Run: `cd gemini-cli && npx vitest run src/providers/gemini/converter.test.ts`
Expected: FAIL (module not found)

**Step 3: 实现 OpenAI → Gemini 方向的转换函数**

```typescript
// gemini-cli/packages/core/src/providers/gemini/converter.ts
import type { Content, Part, Tool, GenerateContentResponse, GenerateContentParameters } from '@google/genai';
import type {
  ChatMessage,
  ToolCall,
  ToolDefinition,
  ToolChoice,
  GenerateParams,
  ChatCompletionResponse,
  ChatChoice,
  TokenUsage,
  ChatCompletionChunk,
  ChatChunkChoice,
} from '../types.js';

// ─── OpenAI → Gemini: Messages ──────────────────

export function openaiMessagesToGemini(
  messages: ChatMessage[],
): { systemInstruction: { parts: Part[] } | undefined; contents: Content[] } {
  const systemParts: string[] = [];
  const contents: Content[] = [];

  for (const msg of messages) {
    if (msg.role === 'system') {
      if (msg.content?.trim()) {
        systemParts.push(msg.content);
      }
      continue;
    }

    if (msg.role === 'user') {
      contents.push({
        role: 'user',
        parts: [{ text: msg.content ?? '' }],
      });
      continue;
    }

    if (msg.role === 'assistant') {
      const parts: Part[] = [];
      if (msg.content) {
        parts.push({ text: msg.content });
      }
      if (msg.tool_calls) {
        for (const tc of msg.tool_calls) {
          let args: Record<string, unknown> = {};
          try {
            args = JSON.parse(tc.function.arguments);
          } catch {
            args = {};
          }
          parts.push({ functionCall: { name: tc.function.name, args } });
        }
      }
      if (parts.length > 0) {
        contents.push({ role: 'model', parts });
      }
      continue;
    }

    if (msg.role === 'tool') {
      const funcName = findFunctionNameByCallId(messages, msg.tool_call_id ?? '');
      let response: Record<string, unknown>;
      try {
        const parsed = JSON.parse(msg.content ?? '');
        response = typeof parsed === 'object' && parsed !== null && !Array.isArray(parsed)
          ? parsed
          : { result: parsed };
      } catch {
        response = { result: msg.content ?? '' };
      }
      contents.push({
        role: 'user',
        parts: [{ functionResponse: { name: funcName, response } }],
      });
      continue;
    }
  }

  const merged = mergeConsecutiveRoles(contents);
  const systemInstruction = systemParts.length > 0
    ? { parts: [{ text: systemParts.join('\n\n') }] }
    : undefined;

  return { systemInstruction, contents: merged };
}

function findFunctionNameByCallId(messages: ChatMessage[], callId: string): string {
  if (!callId) return 'unknown_function';
  for (let i = messages.length - 1; i >= 0; i--) {
    const msg = messages[i];
    if (msg.role === 'assistant' && msg.tool_calls) {
      for (const tc of msg.tool_calls) {
        if (tc.id === callId) {
          return tc.function.name;
        }
      }
    }
  }
  return 'unknown_function';
}

function mergeConsecutiveRoles(contents: Content[]): Content[] {
  if (contents.length === 0) return contents;
  const merged: Content[] = [contents[0]];
  for (let i = 1; i < contents.length; i++) {
    const item = contents[i];
    const last = merged[merged.length - 1];
    if (item.role === last.role) {
      last.parts = [...(last.parts ?? []), ...(item.parts ?? [])];
    } else {
      merged.push(item);
    }
  }
  return merged;
}

// ─── OpenAI → Gemini: Tools ────────────────────

export function openaiToolsToGemini(
  tools: ToolDefinition[] | undefined,
): Tool[] | undefined {
  if (!tools || tools.length === 0) return undefined;
  const declarations = tools
    .filter((t) => t.type === 'function')
    .map((t) => ({
      name: t.function.name,
      description: t.function.description,
      ...(t.function.parameters && { parameters: cleanSchemaForGemini(t.function.parameters) }),
    }));
  if (declarations.length === 0) return undefined;
  return [{ functionDeclarations: declarations }];
}

function cleanSchemaForGemini(schema: Record<string, unknown>): Record<string, unknown> {
  const cleaned = { ...schema };
  for (const key of ['additionalProperties', '$schema', 'title']) {
    delete cleaned[key];
  }
  if (cleaned['properties'] && typeof cleaned['properties'] === 'object') {
    const props = cleaned['properties'] as Record<string, unknown>;
    cleaned['properties'] = Object.fromEntries(
      Object.entries(props).map(([k, v]) => [
        k,
        typeof v === 'object' && v !== null ? cleanSchemaForGemini(v as Record<string, unknown>) : v,
      ]),
    );
  }
  if (cleaned['items'] && typeof cleaned['items'] === 'object') {
    cleaned['items'] = cleanSchemaForGemini(cleaned['items'] as Record<string, unknown>);
  }
  return cleaned;
}

// ─── OpenAI → Gemini: ToolChoice ────────────────

export function mapToolChoiceToGemini(
  toolChoice: ToolChoice | undefined,
): { functionCallingConfig: { mode: string; allowedFunctionNames?: string[] } } | undefined {
  if (toolChoice === undefined) return undefined;

  if (typeof toolChoice === 'string') {
    const map: Record<string, string> = { auto: 'AUTO', required: 'ANY', none: 'NONE' };
    const mode = map[toolChoice];
    return mode ? { functionCallingConfig: { mode } } : undefined;
  }

  if (toolChoice.type === 'function' && toolChoice.function?.name) {
    return {
      functionCallingConfig: {
        mode: 'ANY',
        allowedFunctionNames: [toolChoice.function.name],
      },
    };
  }
  return undefined;
}

// ─── Gemini → OpenAI: Response ──────────────────

export function geminiResponseToOpenai(
  data: GenerateContentResponse,
  model: string,
): ChatCompletionResponse {
  const candidates = data.candidates ?? [];
  if (candidates.length === 0) {
    const blockReason = data.promptFeedback?.blockReason ?? 'UNKNOWN';
    return {
      id: `gemini-${randomHex(12)}`,
      model,
      choices: [{
        index: 0,
        message: { role: 'assistant', content: `[Gemini safety filter] Blocked: ${blockReason}` },
        finish_reason: 'content_filter',
      }],
      usage: { prompt_tokens: 0, completion_tokens: 0, total_tokens: 0 },
    };
  }

  const candidate = candidates[0];
  const parts = candidate.content?.parts ?? [];
  const textParts: string[] = [];
  const toolCalls: ToolCall[] = [];

  for (const part of parts) {
    if (part.text !== undefined) {
      textParts.push(part.text);
    }
    if (part.functionCall) {
      toolCalls.push({
        id: `call_${randomHex(24)}`,
        type: 'function',
        function: {
          name: part.functionCall.name ?? '',
          arguments: JSON.stringify(part.functionCall.args ?? {}),
        },
      });
    }
  }

  const finishReasonMap: Record<string, ChatChoice['finish_reason']> = {
    STOP: 'stop',
    MAX_TOKENS: 'length',
    SAFETY: 'content_filter',
    RECITATION: 'content_filter',
  };
  let finishReason = finishReasonMap[candidate.finishReason ?? 'STOP'] ?? 'stop';
  if (toolCalls.length > 0) finishReason = 'tool_calls';

  const usage = data.usageMetadata ?? {};
  const promptTokens = usage.promptTokenCount ?? 0;
  const completionTokens = usage.candidatesTokenCount ?? 0;

  return {
    id: `gemini-${randomHex(12)}`,
    model,
    choices: [{
      index: 0,
      message: {
        role: 'assistant',
        content: textParts.length > 0 ? textParts.join('\n') : null,
        ...(toolCalls.length > 0 && { tool_calls: toolCalls }),
      },
      finish_reason: finishReason,
    }],
    usage: {
      prompt_tokens: promptTokens,
      completion_tokens: completionTokens,
      total_tokens: promptTokens + completionTokens,
    },
  };
}

// ─── Gemini → OpenAI: Stream Chunk ──────────────

export function geminiChunkToOpenai(
  chunk: GenerateContentResponse,
  model: string,
): ChatCompletionChunk {
  const candidate = chunk.candidates?.[0];
  const parts = candidate?.content?.parts ?? [];
  const textParts: string[] = [];
  const toolCalls: ToolCall[] = [];

  for (const part of parts) {
    if (part.text !== undefined && !part.thought) {
      textParts.push(part.text);
    }
    if (part.functionCall) {
      toolCalls.push({
        id: `call_${randomHex(24)}`,
        type: 'function',
        function: {
          name: part.functionCall.name ?? '',
          arguments: JSON.stringify(part.functionCall.args ?? {}),
        },
      });
    }
  }

  const delta: Partial<ChatMessage> = {};
  if (textParts.length > 0) delta.content = textParts.join('');
  if (toolCalls.length > 0) delta.tool_calls = toolCalls;

  let finishReason: string | null = null;
  if (candidate?.finishReason) {
    const map: Record<string, string> = {
      STOP: 'stop',
      MAX_TOKENS: 'length',
      SAFETY: 'content_filter',
    };
    finishReason = toolCalls.length > 0 ? 'tool_calls' : (map[candidate.finishReason] ?? 'stop');
  }

  const usage = chunk.usageMetadata
    ? {
        prompt_tokens: chunk.usageMetadata.promptTokenCount ?? 0,
        completion_tokens: chunk.usageMetadata.candidatesTokenCount ?? 0,
        total_tokens: (chunk.usageMetadata.promptTokenCount ?? 0) + (chunk.usageMetadata.candidatesTokenCount ?? 0),
      }
    : undefined;

  return {
    id: `gemini-${randomHex(12)}`,
    model,
    choices: [{ index: 0, delta, finish_reason: finishReason }],
    ...(usage && { usage }),
  };
}

// ─── Reverse: Gemini request → OpenAI (for Bridge) ─

export function geminiRequestToOpenai(
  request: GenerateContentParameters,
): GenerateParams {
  const messages: ChatMessage[] = [];

  // system instruction → system message
  const sysInstr = request.config?.systemInstruction;
  if (sysInstr) {
    let sysText = '';
    if (typeof sysInstr === 'string') {
      sysText = sysInstr;
    } else if (Array.isArray(sysInstr)) {
      sysText = sysInstr.map((p) => (typeof p === 'string' ? p : p.text ?? '')).join('\n');
    } else if ('parts' in sysInstr && Array.isArray(sysInstr.parts)) {
      sysText = sysInstr.parts.map((p) => (typeof p === 'string' ? p : p.text ?? '')).join('\n');
    } else if ('text' in sysInstr) {
      sysText = (sysInstr as { text: string }).text;
    }
    if (sysText.trim()) {
      messages.push({ role: 'system', content: sysText });
    }
  }

  // contents → messages
  for (const content of request.contents ?? []) {
    const role = content.role === 'model' ? 'assistant' : 'user';
    const textParts: string[] = [];
    const toolCalls: ToolCall[] = [];
    let functionResponseName = '';
    let functionResponseContent = '';

    for (const part of content.parts ?? []) {
      if (part.text !== undefined && !part.thought) {
        textParts.push(part.text);
      }
      if (part.functionCall) {
        toolCalls.push({
          id: `call_${randomHex(24)}`,
          type: 'function',
          function: {
            name: part.functionCall.name ?? '',
            arguments: JSON.stringify(part.functionCall.args ?? {}),
          },
        });
      }
      if (part.functionResponse) {
        functionResponseName = part.functionResponse.name ?? '';
        functionResponseContent = JSON.stringify(part.functionResponse.response ?? {});
      }
    }

    if (functionResponseName) {
      // Gemini functionResponse → OpenAI tool message
      messages.push({
        role: 'tool',
        content: functionResponseContent,
        tool_call_id: findToolCallIdByName(messages, functionResponseName),
      });
    } else if (role === 'assistant') {
      messages.push({
        role: 'assistant',
        content: textParts.length > 0 ? textParts.join('\n') : null,
        ...(toolCalls.length > 0 && { tool_calls: toolCalls }),
      });
    } else {
      messages.push({
        role: 'user',
        content: textParts.join('\n') || null,
      });
    }
  }

  // tools
  const tools: ToolDefinition[] | undefined = extractToolDefinitions(request.config?.tools);

  return {
    model: request.model ?? '',
    messages,
    ...(tools && { tools }),
    ...(request.config?.temperature !== undefined && { temperature: request.config.temperature }),
    ...(request.config?.maxOutputTokens !== undefined && { max_tokens: request.config.maxOutputTokens }),
    ...(request.config?.topP !== undefined && { top_p: request.config.topP }),
    ...(request.config?.topK !== undefined && { top_k: request.config.topK }),
    ...(request.config?.abortSignal && { abort_signal: request.config.abortSignal }),
  };
}

function findToolCallIdByName(messages: ChatMessage[], funcName: string): string {
  for (let i = messages.length - 1; i >= 0; i--) {
    const msg = messages[i];
    if (msg.role === 'assistant' && msg.tool_calls) {
      for (const tc of msg.tool_calls) {
        if (tc.function.name === funcName) return tc.id;
      }
    }
  }
  return `call_${randomHex(24)}`;
}

function extractToolDefinitions(tools: Tool[] | undefined): ToolDefinition[] | undefined {
  if (!tools) return undefined;
  const defs: ToolDefinition[] = [];
  for (const tool of tools) {
    if ('functionDeclarations' in tool && Array.isArray(tool.functionDeclarations)) {
      for (const decl of tool.functionDeclarations) {
        defs.push({
          type: 'function',
          function: {
            name: decl.name ?? '',
            description: decl.description ?? '',
            ...(decl.parameters && { parameters: decl.parameters as Record<string, unknown> }),
          },
        });
      }
    }
  }
  return defs.length > 0 ? defs : undefined;
}

// ─── Reverse: OpenAI response → Gemini (for Bridge) ─

export function openaiResponseToGemini(
  response: ChatCompletionResponse,
): GenerateContentResponse {
  const choice = response.choices[0];
  if (!choice) {
    return { candidates: [] } as unknown as GenerateContentResponse;
  }

  const parts: Part[] = [];
  if (choice.message.content) {
    parts.push({ text: choice.message.content });
  }
  if (choice.message.tool_calls) {
    for (const tc of choice.message.tool_calls) {
      let args: Record<string, unknown> = {};
      try {
        args = JSON.parse(tc.function.arguments);
      } catch {
        args = {};
      }
      parts.push({ functionCall: { name: tc.function.name, args } });
    }
  }

  const finishReasonMap: Record<string, string> = {
    stop: 'STOP',
    tool_calls: 'STOP',
    length: 'MAX_TOKENS',
    content_filter: 'SAFETY',
  };

  return {
    candidates: [{
      content: { role: 'model', parts },
      finishReason: finishReasonMap[choice.finish_reason] ?? 'STOP',
    }],
    usageMetadata: {
      promptTokenCount: response.usage.prompt_tokens,
      candidatesTokenCount: response.usage.completion_tokens,
      totalTokenCount: response.usage.total_tokens,
    },
  } as unknown as GenerateContentResponse;
}

// ─── Utils ──────────────────────────────────────

function randomHex(length: number): string {
  const chars = '0123456789abcdef';
  let result = '';
  for (let i = 0; i < length; i++) {
    result += chars[Math.floor(Math.random() * 16)];
  }
  return result;
}
```

**Step 4: 运行测试确认通过**

Run: `cd gemini-cli && npx vitest run src/providers/gemini/converter.test.ts`
Expected: ALL PASS

**Step 5: 为 geminiResponseToOpenai 和 geminiRequestToOpenai 补充测试**

在 `converter.test.ts` 末尾追加:

```typescript
describe('geminiResponseToOpenai', () => {
  it('converts text response', () => {
    const geminiResp = {
      candidates: [{
        content: { role: 'model', parts: [{ text: 'Hello!' }] },
        finishReason: 'STOP',
      }],
      usageMetadata: { promptTokenCount: 10, candidatesTokenCount: 5 },
    } as unknown as GenerateContentResponse;

    const result = geminiResponseToOpenai(geminiResp, 'gemini-2.5-flash');
    expect(result.choices[0].message.content).toBe('Hello!');
    expect(result.choices[0].finish_reason).toBe('stop');
    expect(result.usage.prompt_tokens).toBe(10);
  });

  it('converts function call response', () => {
    const geminiResp = {
      candidates: [{
        content: {
          role: 'model',
          parts: [{ functionCall: { name: 'read_cells', args: { range: 'A1' } } }],
        },
        finishReason: 'STOP',
      }],
      usageMetadata: { promptTokenCount: 10, candidatesTokenCount: 5 },
    } as unknown as GenerateContentResponse;

    const result = geminiResponseToOpenai(geminiResp, 'gemini-2.5-flash');
    expect(result.choices[0].finish_reason).toBe('tool_calls');
    expect(result.choices[0].message.tool_calls).toHaveLength(1);
    expect(result.choices[0].message.tool_calls![0].function.name).toBe('read_cells');
  });

  it('handles blocked response', () => {
    const geminiResp = {
      candidates: [],
      promptFeedback: { blockReason: 'SAFETY' },
    } as unknown as GenerateContentResponse;

    const result = geminiResponseToOpenai(geminiResp, 'gemini-2.5-flash');
    expect(result.choices[0].finish_reason).toBe('content_filter');
  });
});

describe('geminiRequestToOpenai', () => {
  it('converts basic request with system instruction', () => {
    const request = {
      model: 'gemini-2.5-flash',
      contents: [
        { role: 'user', parts: [{ text: 'Hello' }] },
      ],
      config: {
        systemInstruction: 'Be helpful.',
        temperature: 0.7,
      },
    } as unknown as GenerateContentParameters;

    const result = geminiRequestToOpenai(request);
    expect(result.model).toBe('gemini-2.5-flash');
    expect(result.messages[0]).toEqual({ role: 'system', content: 'Be helpful.' });
    expect(result.messages[1]).toEqual({ role: 'user', content: 'Hello' });
    expect(result.temperature).toBe(0.7);
  });
});

describe('openaiResponseToGemini', () => {
  it('round-trips through geminiResponseToOpenai', () => {
    const original: ChatCompletionResponse = {
      id: 'test-1',
      model: 'gemini-2.5-flash',
      choices: [{
        index: 0,
        message: { role: 'assistant', content: 'Hello!' },
        finish_reason: 'stop',
      }],
      usage: { prompt_tokens: 10, completion_tokens: 5, total_tokens: 15 },
    };
    const gemini = openaiResponseToGemini(original);
    expect(gemini.candidates).toHaveLength(1);
    expect(gemini.candidates![0].content?.parts?.[0].text).toBe('Hello!');

    // Round trip back
    const roundTripped = geminiResponseToOpenai(gemini, 'gemini-2.5-flash');
    expect(roundTripped.choices[0].message.content).toBe('Hello!');
    expect(roundTripped.choices[0].finish_reason).toBe('stop');
  });
});
```

**Step 6: 运行全部测试确认通过**

Run: `cd gemini-cli && npx vitest run src/providers/gemini/converter.test.ts`
Expected: ALL PASS

**Step 7: Commit**

```bash
git add gemini-cli/packages/core/src/providers/gemini/converter.ts gemini-cli/packages/core/src/providers/gemini/converter.test.ts
git commit -m "feat(providers): add Gemini ↔ OpenAI format converter with tests"
```

---

## Task 4: GeminiProvider 适配器

**Files:**
- Create: `gemini-cli/packages/core/src/providers/gemini/adapter.ts`
- Test: `gemini-cli/packages/core/src/providers/gemini/adapter.test.ts`

**Step 1: 写失败测试**

```typescript
// gemini-cli/packages/core/src/providers/gemini/adapter.test.ts
import { describe, it, expect, vi } from 'vitest';
import { GeminiProvider } from './adapter.js';
import type { GenerateParams } from '../types.js';

// Mock @google/genai
vi.mock('@google/genai', () => ({
  GoogleGenAI: vi.fn().mockImplementation(() => ({
    models: {
      generateContent: vi.fn().mockResolvedValue({
        candidates: [{
          content: { role: 'model', parts: [{ text: 'Hello!' }] },
          finishReason: 'STOP',
        }],
        usageMetadata: { promptTokenCount: 10, candidatesTokenCount: 5 },
      }),
      generateContentStream: vi.fn().mockResolvedValue(
        (async function* () {
          yield {
            candidates: [{
              content: { role: 'model', parts: [{ text: 'Hel' }] },
            }],
          };
          yield {
            candidates: [{
              content: { role: 'model', parts: [{ text: 'lo!' }] },
              finishReason: 'STOP',
            }],
            usageMetadata: { promptTokenCount: 10, candidatesTokenCount: 5 },
          };
        })(),
      ),
      countTokens: vi.fn().mockResolvedValue({ totalTokens: 15 }),
      embedContent: vi.fn().mockResolvedValue({ embedding: { values: [0.1, 0.2] } }),
    },
  })),
}));

describe('GeminiProvider', () => {
  const provider = new GeminiProvider({ apiKey: 'test-key' });

  it('has correct name', () => {
    expect(provider.name).toBe('gemini');
  });

  it('generate returns OpenAI format response', async () => {
    const params: GenerateParams = {
      model: 'gemini-2.5-flash',
      messages: [{ role: 'user', content: 'Hello' }],
    };
    const result = await provider.generate(params);
    expect(result.choices[0].message.content).toBe('Hello!');
    expect(result.choices[0].finish_reason).toBe('stop');
    expect(result.usage.prompt_tokens).toBe(10);
  });

  it('generateStream yields OpenAI format chunks', async () => {
    const params: GenerateParams = {
      model: 'gemini-2.5-flash',
      messages: [{ role: 'user', content: 'Hello' }],
    };
    const chunks = [];
    for await (const chunk of provider.generateStream(params)) {
      chunks.push(chunk);
    }
    expect(chunks).toHaveLength(2);
    expect(chunks[0].choices[0].delta.content).toBe('Hel');
    expect(chunks[1].choices[0].delta.content).toBe('lo!');
  });

  it('countTokens delegates to SDK', async () => {
    const result = await provider.countTokens!(
      [{ role: 'user', content: 'Hello' }],
      'gemini-2.5-flash',
    );
    expect(result).toBe(15);
  });
});
```

**Step 2: 运行测试确认失败**

Run: `cd gemini-cli && npx vitest run src/providers/gemini/adapter.test.ts`
Expected: FAIL

**Step 3: 实现 GeminiProvider**

```typescript
// gemini-cli/packages/core/src/providers/gemini/adapter.ts
import { GoogleGenAI } from '@google/genai';
import type { GenerateContentParameters } from '@google/genai';
import type { LLMProvider } from '../provider.js';
import type {
  ChatMessage,
  GenerateParams,
  ChatCompletionResponse,
  ChatCompletionChunk,
} from '../types.js';
import {
  openaiMessagesToGemini,
  openaiToolsToGemini,
  mapToolChoiceToGemini,
  geminiResponseToOpenai,
  geminiChunkToOpenai,
} from './converter.js';

export interface GeminiProviderConfig {
  apiKey?: string;
  vertexai?: boolean;
  httpOptions?: { headers?: Record<string, string> };
  apiVersion?: string;
}

export class GeminiProvider implements LLMProvider {
  readonly name = 'gemini';
  private readonly genai: GoogleGenAI;

  constructor(config: GeminiProviderConfig) {
    this.genai = new GoogleGenAI({
      apiKey: config.apiKey === '' ? undefined : config.apiKey,
      vertexai: config.vertexai,
      ...(config.httpOptions && { httpOptions: config.httpOptions }),
      ...(config.apiVersion && { apiVersion: config.apiVersion }),
    });
  }

  async generate(params: GenerateParams): Promise<ChatCompletionResponse> {
    const request = this.buildGeminiRequest(params);
    const response = await this.genai.models.generateContent(request);
    return geminiResponseToOpenai(response, params.model);
  }

  async *generateStream(params: GenerateParams): AsyncGenerator<ChatCompletionChunk> {
    const request = this.buildGeminiRequest(params);
    const stream = await this.genai.models.generateContentStream(request);
    for await (const chunk of stream) {
      yield geminiChunkToOpenai(chunk, params.model);
    }
  }

  async countTokens(messages: ChatMessage[], model: string): Promise<number> {
    const { contents } = openaiMessagesToGemini(messages);
    const result = await this.genai.models.countTokens({ model, contents });
    return result.totalTokens ?? 0;
  }

  async embedContent(input: string, model: string): Promise<number[]> {
    const result = await this.genai.models.embedContent({
      model,
      contents: input,
    });
    return result.embedding?.values ?? [];
  }

  private buildGeminiRequest(params: GenerateParams): GenerateContentParameters {
    const { systemInstruction, contents } = openaiMessagesToGemini(params.messages);
    const tools = openaiToolsToGemini(params.tools);
    const toolConfig = mapToolChoiceToGemini(params.tool_choice);

    return {
      model: params.model,
      contents,
      config: {
        ...(systemInstruction && { systemInstruction }),
        ...(tools && { tools }),
        ...(toolConfig && { toolConfig }),
        ...(params.temperature !== undefined && { temperature: params.temperature }),
        ...(params.max_tokens !== undefined && { maxOutputTokens: params.max_tokens }),
        ...(params.top_p !== undefined && { topP: params.top_p }),
        ...(params.top_k !== undefined && { topK: params.top_k }),
        ...(params.abort_signal && { abortSignal: params.abort_signal }),
        ...(params.response_format?.type === 'json_object' && {
          responseMimeType: 'application/json',
        }),
      },
    };
  }
}
```

**Step 4: 运行测试确认通过**

Run: `cd gemini-cli && npx vitest run src/providers/gemini/adapter.test.ts`
Expected: ALL PASS

**Step 5: Commit**

```bash
git add gemini-cli/packages/core/src/providers/gemini/adapter.ts gemini-cli/packages/core/src/providers/gemini/adapter.test.ts
git commit -m "feat(providers): add GeminiProvider adapter with tests"
```

---

## Task 5: ProviderBridge

**Files:**
- Create: `gemini-cli/packages/core/src/providers/bridge.ts`
- Test: `gemini-cli/packages/core/src/providers/bridge.test.ts`

**Step 1: 写失败测试**

```typescript
// gemini-cli/packages/core/src/providers/bridge.test.ts
import { describe, it, expect, vi } from 'vitest';
import { ProviderBridge } from './bridge.js';
import type { LLMProvider } from './provider.js';
import type { GenerateContentParameters } from '@google/genai';

function createMockProvider(): LLMProvider {
  return {
    name: 'mock',
    generate: vi.fn().mockResolvedValue({
      id: 'resp-1',
      model: 'mock-model',
      choices: [{
        index: 0,
        message: { role: 'assistant', content: 'Hello from mock!' },
        finish_reason: 'stop',
      }],
      usage: { prompt_tokens: 10, completion_tokens: 5, total_tokens: 15 },
    }),
    generateStream: vi.fn().mockImplementation(async function* () {
      yield {
        id: 'chunk-1',
        model: 'mock-model',
        choices: [{ index: 0, delta: { content: 'Hello' }, finish_reason: null }],
      };
      yield {
        id: 'chunk-2',
        model: 'mock-model',
        choices: [{ index: 0, delta: { content: ' world!' }, finish_reason: 'stop' }],
        usage: { prompt_tokens: 10, completion_tokens: 5, total_tokens: 15 },
      };
    }),
    countTokens: vi.fn().mockResolvedValue(15),
  };
}

describe('ProviderBridge', () => {
  it('generateContent converts request and response', async () => {
    const provider = createMockProvider();
    const bridge = new ProviderBridge(provider);

    const request: GenerateContentParameters = {
      model: 'gemini-2.5-flash',
      contents: [{ role: 'user', parts: [{ text: 'Hello' }] }],
      config: { systemInstruction: 'Be helpful.' },
    };

    const result = await bridge.generateContent(request, 'prompt-1');
    expect(result.candidates).toHaveLength(1);
    expect(result.candidates![0].content?.parts?.[0].text).toBe('Hello from mock!');

    // Verify provider was called with OpenAI format
    expect(provider.generate).toHaveBeenCalledTimes(1);
    const callArgs = (provider.generate as ReturnType<typeof vi.fn>).mock.calls[0][0];
    expect(callArgs.messages[0].role).toBe('system');
    expect(callArgs.messages[1].role).toBe('user');
  });

  it('generateContentStream converts chunks', async () => {
    const provider = createMockProvider();
    const bridge = new ProviderBridge(provider);

    const request: GenerateContentParameters = {
      model: 'gemini-2.5-flash',
      contents: [{ role: 'user', parts: [{ text: 'Hello' }] }],
    };

    const stream = await bridge.generateContentStream(request, 'prompt-1');
    const chunks = [];
    for await (const chunk of stream) {
      chunks.push(chunk);
    }
    expect(chunks).toHaveLength(2);
    expect(chunks[0].candidates?.[0]?.content?.parts?.[0]?.text).toBe('Hello');
  });

  it('countTokens delegates through provider', async () => {
    const provider = createMockProvider();
    const bridge = new ProviderBridge(provider);

    const result = await bridge.countTokens({
      model: 'gemini-2.5-flash',
      contents: [{ role: 'user', parts: [{ text: 'Hello' }] }],
    });
    expect(result.totalTokens).toBe(15);
  });
});
```

**Step 2: 实现 ProviderBridge**

```typescript
// gemini-cli/packages/core/src/providers/bridge.ts
import type {
  GenerateContentResponse,
  GenerateContentParameters,
  CountTokensParameters,
  CountTokensResponse,
  EmbedContentParameters,
  EmbedContentResponse,
} from '@google/genai';
import type { ContentGenerator } from '../core/contentGenerator.js';
import type { LLMProvider } from './provider.js';
import {
  geminiRequestToOpenai,
  openaiResponseToGemini,
} from './gemini/converter.js';
import type { ChatCompletionChunk } from './types.js';

export class ProviderBridge implements ContentGenerator {
  constructor(private readonly provider: LLMProvider) {}

  async generateContent(
    request: GenerateContentParameters,
    _userPromptId: string,
  ): Promise<GenerateContentResponse> {
    const params = geminiRequestToOpenai(request);
    const response = await this.provider.generate(params);
    return openaiResponseToGemini(response);
  }

  async generateContentStream(
    request: GenerateContentParameters,
    _userPromptId: string,
  ): Promise<AsyncGenerator<GenerateContentResponse>> {
    const params = geminiRequestToOpenai(request);
    const provider = this.provider;

    async function* streamBridge(): AsyncGenerator<GenerateContentResponse> {
      for await (const chunk of provider.generateStream(params)) {
        yield chunkToGeminiResponse(chunk, params.model);
      }
    }
    return streamBridge();
  }

  async countTokens(request: CountTokensParameters): Promise<CountTokensResponse> {
    if (this.provider.countTokens) {
      const messages = (request.contents ?? []).map((c) => ({
        role: c.role === 'model' ? 'assistant' as const : 'user' as const,
        content: c.parts?.map((p) => p.text ?? '').join('') ?? null,
      }));
      const total = await this.provider.countTokens(messages, request.model ?? '');
      return { totalTokens: total } as CountTokensResponse;
    }
    return { totalTokens: 0 } as CountTokensResponse;
  }

  async embedContent(request: EmbedContentParameters): Promise<EmbedContentResponse> {
    if (this.provider.embedContent) {
      const input = typeof request.contents === 'string'
        ? request.contents
        : '';
      const values = await this.provider.embedContent(input, request.model ?? '');
      return { embedding: { values } } as EmbedContentResponse;
    }
    throw new Error(`Provider '${this.provider.name}' does not support embedContent`);
  }
}

function chunkToGeminiResponse(
  chunk: ChatCompletionChunk,
  model: string,
): GenerateContentResponse {
  const delta = chunk.choices[0]?.delta ?? {};
  const parts: Array<{ text?: string; functionCall?: { name: string; args: Record<string, unknown> } }> = [];

  if (delta.content) {
    parts.push({ text: delta.content });
  }
  if (delta.tool_calls) {
    for (const tc of delta.tool_calls) {
      let args: Record<string, unknown> = {};
      try { args = JSON.parse(tc.function.arguments); } catch { args = {}; }
      parts.push({ functionCall: { name: tc.function.name, args } });
    }
  }

  const finishReason = chunk.choices[0]?.finish_reason;
  const finishReasonMap: Record<string, string> = {
    stop: 'STOP',
    tool_calls: 'STOP',
    length: 'MAX_TOKENS',
    content_filter: 'SAFETY',
  };

  return {
    candidates: [{
      content: { role: 'model', parts },
      ...(finishReason && { finishReason: finishReasonMap[finishReason] ?? 'STOP' }),
    }],
    ...(chunk.usage && {
      usageMetadata: {
        promptTokenCount: chunk.usage.prompt_tokens,
        candidatesTokenCount: chunk.usage.completion_tokens,
        totalTokenCount: chunk.usage.total_tokens,
      },
    }),
  } as unknown as GenerateContentResponse;
}
```

**Step 3: 运行测试确认通过**

Run: `cd gemini-cli && npx vitest run src/providers/bridge.test.ts`
Expected: ALL PASS

**Step 4: Commit**

```bash
git add gemini-cli/packages/core/src/providers/bridge.ts gemini-cli/packages/core/src/providers/bridge.test.ts
git commit -m "feat(providers): add ProviderBridge (LLMProvider → ContentGenerator) with tests"
```

---

## Task 6: 导出模块 + 工厂函数

**Files:**
- Create: `gemini-cli/packages/core/src/providers/index.ts`

**Step 1: 创建 index.ts**

```typescript
// gemini-cli/packages/core/src/providers/index.ts
export type {
  ChatMessage,
  ToolCall,
  ToolDefinition,
  ToolChoice,
  GenerateParams,
  ChatCompletionResponse,
  ChatChoice,
  TokenUsage,
  ChatCompletionChunk,
  ChatChunkChoice,
} from './types.js';

export type { LLMProvider } from './provider.js';
export { ProviderBridge } from './bridge.js';
export { GeminiProvider, type GeminiProviderConfig } from './gemini/adapter.js';
```

**Step 2: Commit**

```bash
git add gemini-cli/packages/core/src/providers/index.ts
git commit -m "feat(providers): add index module exports"
```

---

## Task 7: 集成到 contentGenerator.ts

**Files:**
- Modify: `gemini-cli/packages/core/src/core/contentGenerator.ts:133-210`

**Step 1: 在 `createContentGenerator` 函数顶部添加 Provider 抽象路径**

在 `createContentGenerator` 函数的 `const generator = await (async () => {` 之前，加入：

```typescript
// Provider abstraction layer (feature flag)
if (process.env['GEMINI_CLI_USE_PROVIDER_ABSTRACTION'] === '1') {
  const { GeminiProvider, ProviderBridge } = await import('../providers/index.js');

  const apiVersionEnv = process.env['GOOGLE_GENAI_API_VERSION'];
  const version = await getVersion();
  const model = resolveModel(gcConfig.getModel());
  const customHeadersEnv = process.env['GEMINI_CLI_CUSTOM_HEADERS'] || undefined;
  const userAgent = `GeminiCLI/${version}/${model} (${process.platform}; ${process.arch})`;
  const customHeadersMap = parseCustomHeaders(customHeadersEnv);

  const provider = new GeminiProvider({
    apiKey: config.apiKey,
    vertexai: config.vertexai,
    httpOptions: { headers: { ...customHeadersMap, 'User-Agent': userAgent } },
    ...(apiVersionEnv && { apiVersion: apiVersionEnv }),
  });
  const bridge = new ProviderBridge(provider);
  const generator = new LoggingContentGenerator(bridge, gcConfig);

  if (gcConfig.recordResponses) {
    return new RecordingContentGenerator(generator, gcConfig.recordResponses);
  }
  return generator;
}
```

**Step 2: 运行现有测试确认回归无破坏**

Run: `cd gemini-cli && npx vitest run`
Expected: ALL existing tests PASS (功能开关默认关闭，不影响任何现有路径)

**Step 3: Commit**

```bash
git add gemini-cli/packages/core/src/core/contentGenerator.ts
git commit -m "feat(providers): wire provider abstraction into createContentGenerator with feature flag"
```

---

## Task 8: 全量测试 + 更新迁移表

**Files:**
- Modify: `migration/README.md:135` (更新 9.4 状态)

**Step 1: 运行所有 provider 测试**

Run: `cd gemini-cli && npx vitest run src/providers/`
Expected: ALL PASS

**Step 2: 运行全量测试确认无回归**

Run: `cd gemini-cli && npx vitest run`
Expected: ALL PASS

**Step 3: 更新迁移表 9.4 状态**

将 `migration/README.md` 第 135 行的:
```
| 9.4 | 多模型提供商 | `providers/` (Claude/Gemini/OpenAI) | 仅 Gemini | ⚪/🔶 | 锁定 Gemini 则放弃；需多模型则适配 |
```
改为:
```
| 9.4 | 多模型提供商 | `providers/` (Claude/Gemini/OpenAI) | `src/providers/` (LLMProvider) | 🔶 适配中 | Phase 1 完成: Provider 抽象层 + Gemini adapter，功能开关控制 |
```

**Step 4: Commit**

```bash
git add migration/README.md
git commit -m "docs: update migration table - provider abstraction Phase 1 complete"
```

---

## 完成检查清单

- [ ] Task 1: `types.ts` + 类型测试
- [ ] Task 2: `provider.ts` 接口
- [ ] Task 3: `gemini/converter.ts` + 转换测试
- [ ] Task 4: `gemini/adapter.ts` + adapter 测试
- [ ] Task 5: `bridge.ts` + bridge 测试
- [ ] Task 6: `index.ts` 导出
- [ ] Task 7: `contentGenerator.ts` 功能开关集成
- [ ] Task 8: 全量测试 + 迁移表更新
