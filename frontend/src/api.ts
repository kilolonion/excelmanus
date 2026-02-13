// API 客户端 — 封装与后端 REST API 的通信

// ========== 类型定义 ==========

/** 聊天请求参数 */
export interface ChatRequest {
  message: string
  session_id?: string | null
}

/** 聊天响应 */
export interface ChatResponse {
  session_id: string
  reply: string
  skills_used: string[]
  tool_scope: string[]
  route_mode: string
}

/** 健康检查响应 */
export interface HealthResponse {
  status: string
  version: string
  model: string
  tools: string[]
  skillpacks: string[]
}

/** API 错误响应体 */
export interface ApiError {
  error: string
  error_id?: string
}

// ========== SSE 流式事件类型 ==========

export interface SessionInitEvent {
  type: 'session_init'
  session_id: string
}

export interface ThinkingEvent {
  type: 'thinking'
  content: string
  iteration: number
}

export interface ToolCallStartEvent {
  type: 'tool_call_start'
  tool_name: string
  arguments: Record<string, unknown>
  iteration: number
}

export interface ToolCallEndEvent {
  type: 'tool_call_end'
  tool_name: string
  success: boolean
  result: string
  error: string | null
  iteration: number
}

export interface IterationStartEvent {
  type: 'iteration_start'
  iteration: number
}

export interface ReplyEvent {
  type: 'reply'
  content: string
  skills_used: string[]
  tool_scope: string[]
  route_mode: string
}

export interface DoneEvent {
  type: 'done'
}

export interface ErrorEvent {
  type: 'error'
  error: string
  error_id?: string
}

/** SSE 流式事件联合类型 */
export type StreamEvent =
  | SessionInitEvent
  | ThinkingEvent
  | ToolCallStartEvent
  | ToolCallEndEvent
  | IterationStartEvent
  | ReplyEvent
  | DoneEvent
  | ErrorEvent

// ========== 错误处理 ==========

/** 根据 HTTP 状态码生成中文错误消息 */
export function getErrorMessage(status: number, body?: ApiError): string {
  switch (status) {
    case 429:
      return '会话数量已达上限，请稍后重试'
    case 409:
      return '当前任务正在处理中，请等待完成'
    default:
      return body?.error
        ? `请求失败（${status}）：${body.error}`
        : `请求失败（${status}）`
  }
}

/** 统一处理 fetch 响应，非 2xx 时抛出带中文描述的错误 */
async function handleResponse<T>(response: Response): Promise<T> {
  if (!response.ok) {
    let body: ApiError | undefined
    try {
      body = await response.json()
    } catch {
      // 响应体不是 JSON，忽略
    }
    throw new Error(getErrorMessage(response.status, body))
  }
  return response.json()
}

/** 运行时校验 chat 响应必须符合 v3 协议 */
function assertChatResponse(raw: unknown): ChatResponse {
  const data = raw as Partial<ChatResponse> | null
  if (
    !data
    || typeof data.session_id !== 'string'
    || typeof data.reply !== 'string'
    || !Array.isArray(data.skills_used)
    || !Array.isArray(data.tool_scope)
    || typeof data.route_mode !== 'string'
  ) {
    throw new Error('后端响应格式不符合 v3 协议（chat）')
  }
  return data as ChatResponse
}

/** 运行时校验 health 响应必须符合 v3 协议 */
function assertHealthResponse(raw: unknown): HealthResponse {
  const data = raw as Partial<HealthResponse> | null
  if (
    !data
    || typeof data.status !== 'string'
    || typeof data.version !== 'string'
    || typeof data.model !== 'string'
    || !Array.isArray(data.tools)
    || !Array.isArray(data.skillpacks)
  ) {
    throw new Error('后端响应格式不符合 v3 协议（health）')
  }
  return data as HealthResponse
}

// ========== API 函数 ==========

/** 发送消息到后端代理 */
export async function sendMessage(req: ChatRequest): Promise<ChatResponse> {
  let response: Response
  try {
    response = await fetch('/api/v1/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(req),
    })
  } catch (err) {
    // 网络错误或超时
    if (err instanceof TypeError) {
      throw new Error('网络连接失败')
    }
    throw new Error('请求超时')
  }
  const raw = await handleResponse<unknown>(response)
  return assertChatResponse(raw)
}

/** 删除指定会话 */
export async function deleteSession(sessionId: string): Promise<void> {
  let response: Response
  try {
    response = await fetch(`/api/v1/sessions/${sessionId}`, {
      method: 'DELETE',
    })
  } catch (err) {
    if (err instanceof TypeError) {
      throw new Error('网络连接失败')
    }
    throw new Error('请求超时')
  }
  if (!response.ok) {
    let body: ApiError | undefined
    try {
      body = await response.json()
    } catch {
      // 忽略
    }
    throw new Error(getErrorMessage(response.status, body))
  }
}

/** SSE 流式发送消息，通过回调逐事件推送 */
export async function sendMessageStream(
  req: ChatRequest,
  onEvent: (event: StreamEvent) => void,
): Promise<void> {
  let response: Response
  try {
    response = await fetch('/api/v1/chat/stream', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(req),
    })
  } catch (err) {
    if (err instanceof TypeError) {
      throw new Error('网络连接失败')
    }
    throw new Error('请求超时')
  }

  if (!response.ok) {
    let body: ApiError | undefined
    try {
      body = await response.json()
    } catch {
      // 响应体不是 JSON，忽略
    }
    throw new Error(getErrorMessage(response.status, body))
  }

  if (!response.body) {
    throw new Error('浏览器不支持流式响应')
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) break

      buffer += decoder.decode(value, { stream: true })

      // 解析 SSE 格式：以双换行分隔事件
      const parts = buffer.split('\n\n')
      // 最后一段可能不完整，保留在 buffer 中
      buffer = parts.pop() || ''

      for (const part of parts) {
        if (!part.trim()) continue
        const parsed = parseSSEEvent(part)
        if (parsed) {
          onEvent(parsed)
        }
      }
    }

    // 处理 buffer 中剩余的数据
    if (buffer.trim()) {
      const parsed = parseSSEEvent(buffer)
      if (parsed) {
        onEvent(parsed)
      }
    }
  } finally {
    reader.releaseLock()
  }
}

/** 解析单个 SSE 事件文本 */
function parseSSEEvent(text: string): StreamEvent | null {
  let eventType = ''
  let dataStr = ''

  for (const line of text.split('\n')) {
    if (line.startsWith('event: ')) {
      eventType = line.slice(7).trim()
    } else if (line.startsWith('data: ')) {
      dataStr = line.slice(6)
    }
  }

  if (!eventType || !dataStr) return null

  try {
    const data = JSON.parse(dataStr)
    return { type: eventType, ...data } as StreamEvent
  } catch {
    return null
  }
}

/** 检查后端健康状态 */
export async function checkHealth(): Promise<HealthResponse> {
  let response: Response
  try {
    response = await fetch('/api/v1/health')
  } catch (err) {
    if (err instanceof TypeError) {
      throw new Error('网络连接失败')
    }
    throw new Error('请求超时')
  }
  const raw = await handleResponse<unknown>(response)
  return assertHealthResponse(raw)
}
