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
}

/** 健康检查响应 */
export interface HealthResponse {
  status: string
  version: string
  skills: string[]
}

/** API 错误响应体 */
export interface ApiError {
  error: string
  error_id?: string
}

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
  return handleResponse<ChatResponse>(response)
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
  return handleResponse<HealthResponse>(response)
}
