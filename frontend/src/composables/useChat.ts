import { ref, type Ref } from 'vue'
import { sendMessage as apiSendMessage } from '../api'
import { useSession } from './useSession'

// ========== 类型定义 ==========

/** 消息对象 */
export interface Message {
  id: string
  role: 'user' | 'assistant' | 'error'
  content: string
  timestamp: number
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
 */
export function useChat(): UseChat {
  const messages = ref<Message[]>([])
  const loading = ref(false)
  const { sessionId, setSessionId } = useSession()

  /** 发送消息：验证输入 → 添加用户消息 → 调用 API → 添加回复 */
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

    loading.value = true
    try {
      const res = await apiSendMessage({
        message: content,
        session_id: sessionId.value,
      })
      // 更新 session_id
      setSessionId(res.session_id)
      // 添加代理回复
      messages.value.push({
        id: generateId(),
        role: 'assistant',
        content: res.reply,
        timestamp: Date.now(),
      })
    } catch (err) {
      // 添加错误消息
      messages.value.push({
        id: generateId(),
        role: 'error',
        content: err instanceof Error ? err.message : '未知错误',
        timestamp: Date.now(),
      })
    } finally {
      loading.value = false
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
