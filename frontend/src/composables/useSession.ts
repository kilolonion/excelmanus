import { ref, type Ref } from 'vue'

// localStorage 存储键名
const STORAGE_KEY = 'excelmanus_session_id'

export interface UseSession {
  sessionId: Ref<string | null>
  setSessionId(id: string): void
  clearSession(): void
}

/**
 * 会话管理 composable
 * 管理 session_id 状态，支持 localStorage 持久化与恢复
 */
export function useSession(): UseSession {
  // 初始化时从 localStorage 恢复 session_id
  const sessionId = ref<string | null>(restore())

  /** 从 localStorage 读取已保存的 session_id */
  function restore(): string | null {
    try {
      return localStorage.getItem(STORAGE_KEY)
    } catch {
      // localStorage 不可用时降级为 null
      return null
    }
  }

  /** 设置 session_id，同时写入 localStorage */
  function setSessionId(id: string): void {
    sessionId.value = id
    try {
      localStorage.setItem(STORAGE_KEY, id)
    } catch {
      // localStorage 不可用时仅保留内存状态
    }
  }

  /** 清除 session_id，同时移除 localStorage 中的记录 */
  function clearSession(): void {
    sessionId.value = null
    try {
      localStorage.removeItem(STORAGE_KEY)
    } catch {
      // localStorage 不可用时忽略
    }
  }

  return { sessionId, setSessionId, clearSession }
}
