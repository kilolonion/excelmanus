import { ref, type Ref } from 'vue'
import { sendMessageStream, type StreamEvent } from '../api'
import { useSession } from './useSession'

// ========== 类型定义 ==========

/** 工具调用信息 */
export interface ToolCallInfo {
  tool_name: string
  arguments: Record<string, unknown>
  status: 'running' | 'success' | 'error'
  result?: string
  error?: string | null
}

/** 消息对象 */
export interface Message {
  id: string
  role: 'user' | 'assistant' | 'error'
  content: string
  timestamp: number
  /** LLM 思考过程（仅 assistant 消息） */
  thinking?: string
  /** 工具调用记录（仅 assistant 消息） */
  toolCalls?: ToolCallInfo[]
  /** 是否正在流式接收中 */
  streaming?: boolean
}

export interface UseChat {
  messages: Ref<Message[]>
  loading: Ref<boolean>
  sendMessage(content: string): Promise<void>
  clearMessages(): void
  retryLast(): Promise<void>
}

/** 生成唯一消息 ID */
function generateId(): string {
  return typeof crypto !== 'undefined' && crypto.randomUUID
    ? crypto.randomUUID()
    : Date.now().toString()
}

/**
 * 对话管理 composable
 * 管理消息列表、loading 状态、发送/重试/清空操作
 * 使用 SSE 流式 API 实时展示思考过程和工具调用
 */
export function useChat(): UseChat {
  const messages = ref<Message[]>([])
  const loading = ref(false)
  const { sessionId, setSessionId } = useSession()

  /** 发送消息：验证输入 → 添加用户消息 → 流式接收回复 */
  async function sendMessage(content: string): Promise<void> {
    // 空白消息静默拒绝
    if (!content.trim()) return
    // loading 期间阻止发送
    if (loading.value) return

    // 添加用户消息
    messages.value.push({
      id: generateId(),
      role: 'user',
      content,
      timestamp: Date.now(),
    })

    // 创建流式 assistant 消息占位
    const assistantMsg: Message = {
      id: generateId(),
      role: 'assistant',
      content: '',
      timestamp: Date.now(),
      thinking: '',
      toolCalls: [],
      streaming: true,
    }
    messages.value.push(assistantMsg)

    loading.value = true
    try {
      await sendMessageStream(
        {
          message: content,
          session_id: sessionId.value,
        },
        (event: StreamEvent) => {
          _handleStreamEvent(event, assistantMsg)
        },
      )
    } catch (err) {
      // 如果 assistant 消息还是空的，移除它并添加错误消息
      if (!assistantMsg.content && !assistantMsg.toolCalls?.length && !assistantMsg.thinking) {
        const idx = messages.value.indexOf(assistantMsg)
        if (idx >= 0) messages.value.splice(idx, 1)
      }
      messages.value.push({
        id: generateId(),
        role: 'error',
        content: err instanceof Error ? err.message : '未知错误',
        timestamp: Date.now(),
      })
    } finally {
      assistantMsg.streaming = false
      loading.value = false
    }
  }

  /** 处理单个 SSE 流事件，实时更新 assistant 消息 */
  function _handleStreamEvent(event: StreamEvent, msg: Message): void {
    switch (event.type) {
      case 'session_init':
        setSessionId(event.session_id)
        break

      case 'thinking':
        // 追加思考内容
        msg.thinking = (msg.thinking || '') + event.content
        break

      case 'tool_call_start':
        if (!msg.toolCalls) msg.toolCalls = []
        msg.toolCalls.push({
          tool_name: event.tool_name,
          arguments: event.arguments,
          status: 'running',
        })
        break

      case 'tool_call_end': {
        if (!msg.toolCalls) break
        // 找到最后一个同名且 running 的工具调用并更新
        const tc = [...msg.toolCalls]
          .reverse()
          .find((t) => t.tool_name === event.tool_name && t.status === 'running')
        if (tc) {
          tc.status = event.success ? 'success' : 'error'
          tc.result = event.result
          tc.error = event.error
        }
        break
      }

      case 'reply':
        msg.content = event.content
        break

      case 'error':
        // 将错误信息作为内容展示
        if (!msg.content) {
          msg.content = event.error || '服务内部错误'
        }
        break

      case 'iteration_start':
      case 'done':
        // 不需要额外处理
        break
    }
  }

  /** 清空所有消息 */
  function clearMessages(): void {
    messages.value = []
  }

  /** 重试最后一条用户消息 */
  async function retryLast(): Promise<void> {
    // 找到最后一条用户消息
    const lastUserMsg = [...messages.value]
      .reverse()
      .find((m) => m.role === 'user')
    if (!lastUserMsg) return

    // 移除最后一条用户消息之后的所有消息（错误/回复）
    const idx = messages.value.lastIndexOf(lastUserMsg)
    messages.value.splice(idx)

    // 重新发送
    await sendMessage(lastUserMsg.content)
  }

  return { messages, loading, sendMessage, clearMessages, retryLast }
}
