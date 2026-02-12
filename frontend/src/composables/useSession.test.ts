import { describe, it, expect, beforeEach } from 'vitest'
import { useSession } from './useSession'

const STORAGE_KEY = 'excelmanus_session_id'

describe('useSession', () => {
  beforeEach(() => {
    localStorage.clear()
  })

  it('初始化时 sessionId 为 null（无持久化数据）', () => {
    const { sessionId } = useSession()
    expect(sessionId.value).toBeNull()
  })

  it('初始化时从 localStorage 恢复 sessionId', () => {
    localStorage.setItem(STORAGE_KEY, 'abc-123')
    const { sessionId } = useSession()
    expect(sessionId.value).toBe('abc-123')
  })

  it('setSessionId 同时更新 ref 和 localStorage', () => {
    const { sessionId, setSessionId } = useSession()
    setSessionId('new-session')
    expect(sessionId.value).toBe('new-session')
    expect(localStorage.getItem(STORAGE_KEY)).toBe('new-session')
  })

  it('clearSession 清除 ref 和 localStorage', () => {
    const { sessionId, setSessionId, clearSession } = useSession()
    setSessionId('to-clear')
    clearSession()
    expect(sessionId.value).toBeNull()
    expect(localStorage.getItem(STORAGE_KEY)).toBeNull()
  })

  it('多次 setSessionId 覆盖旧值', () => {
    const { sessionId, setSessionId } = useSession()
    setSessionId('first')
    setSessionId('second')
    expect(sessionId.value).toBe('second')
    expect(localStorage.getItem(STORAGE_KEY)).toBe('second')
  })
})
