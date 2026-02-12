// Feature: vue-frontend, Property 6: 新建会话清空状态
// **Validates: Requirements 4.2**

import { describe, it, expect, vi, beforeEach } from 'vitest'
import fc from 'fast-check'

// Mock api 模块，阻止真实网络请求
vi.mock('../api', () => ({
  sendMessage: vi.fn(),
}))

import { sendMessage as apiSendMessage } from '../api'
import { useChat } from './useChat'
import { useSession } from './useSession'

describe('Property 6: 新建会话清空状态', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.clear()
  })

  // 对于任意已有消息列表和已有 session_id 的对话状态，
  // 执行新建会话操作后，session_id 应为 null，消息列表应为空数组
  it('执行新建会话后，session_id 为 null，消息列表为空', async () => {
    // 生成非空消息数组（至少 1 条）和一个非空 session_id
    const messagesArb = fc.array(
      fc.string({ minLength: 1 }).filter((s) => s.trim().length > 0),
      { minLength: 1, maxLength: 8 },
    )
    const sessionIdArb = fc.string({ minLength: 1 }).filter((s) => s.length > 0)

    await fc.assert(
      fc.asyncProperty(messagesArb, sessionIdArb, async (msgs, fixedSessionId) => {
        vi.clearAllMocks()
        localStorage.clear()

        // Mock API 返回固定 session_id
        vi.mocked(apiSendMessage).mockResolvedValue({
          session_id: fixedSessionId,
          reply: '回复',
        })

        const { messages, sendMessage, clearMessages } = useChat()
        const { sessionId, clearSession } = useSession()

        // 步骤 1：发送消息，建立对话状态
        for (const msg of msgs) {
          await sendMessage(msg)
        }

        // 验证消息列表非空
        expect(messages.value.length).toBeGreaterThan(0)
        // 验证 session_id 已持久化到 localStorage
        expect(localStorage.getItem('excelmanus_session_id')).toBe(fixedSessionId)

        // 步骤 2：执行新建会话操作（清空消息 + 清除会话）
        clearMessages()
        clearSession()

        // 步骤 3：验证状态已清空
        expect(messages.value).toEqual([])
        expect(sessionId.value).toBeNull()
        expect(localStorage.getItem('excelmanus_session_id')).toBeNull()
      }),
      { numRuns: 100 },
    )
  })
})
