// Feature: vue-frontend, Property 7: Session ID 持久化 round-trip
// **Validates: Requirements 4.3**

import { describe, it, expect, beforeEach } from 'vitest'
import fc from 'fast-check'
import { useSession } from './useSession'

const STORAGE_KEY = 'excelmanus_session_id'

describe('Property 7: Session ID 持久化 round-trip', () => {
  beforeEach(() => {
    localStorage.clear()
  })

  // 对于任意有效的 session_id 字符串，调用 setSessionId(id) 后，
  // 从 localStorage 读取 excelmanus_session_id 键应得到相同的字符串值
  it('setSessionId(id) 后 localStorage 应存储相同值', () => {
    fc.assert(
      fc.property(fc.string({ minLength: 1 }), (id) => {
        localStorage.clear()
        const { setSessionId } = useSession()
        setSessionId(id)
        expect(localStorage.getItem(STORAGE_KEY)).toBe(id)
      }),
      { numRuns: 100 },
    )
  })
})
