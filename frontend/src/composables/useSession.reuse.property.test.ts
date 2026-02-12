// Feature: vue-frontend, Property 5: Session ID 复用
// **Validates: Requirements 4.1**

import { describe, it, expect, vi, beforeEach } from 'vitest'
import fc from 'fast-check'
import type { StreamEvent } from '../api'

// Mock api 模块，阻止真实网络请求
vi.mock('../api', () => ({
  sendMessageStream: vi.fn(),
}))

import { sendMessageStream } from '../api'
import { useChat } from './useChat'

/** 模拟 SSE 流：依次触发事件回调 */
function mockStreamSuccess(sessionId: string, reply: string) {
  vi.mocked(sendMessageStream).mockImplementation(async (_req, onEvent) => {
    onEvent({ type: 'session_init', session_id: sessionId } as StreamEvent)
    onEvent({ type: 'reply', content: reply, skills_used: ['data_basic'], tool_scope: ['read_excel'], route_mode: 'hint_direct' } as StreamEvent)
    onEvent({ type: 'done' } as StreamEvent)
  })
}

describe('Property 5: Session ID 复用', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.clear()
  })

  // 对于任意消息序列（长度 ≥ 2），第一次请求后获取的 session_id
  // 应在所有后续请求中被携带，且所有后续请求携带的 session_id 值相同
  it('首次请求获取的 session_id 应在所有后续请求中被复用', async () => {
    // 生成非空消息数组（长度 ≥ 2）和一个固定的 session_id
    const messagesArb = fc.array(
      fc.string({ minLength: 1 }).filter((s) => s.trim().length > 0),
      { minLength: 2, maxLength: 10 },
    )
    const sessionIdArb = fc.string({ minLength: 1 })

    await fc.assert(
      fc.asyncProperty(messagesArb, sessionIdArb, async (msgs, fixedSessionId) => {
        vi.clearAllMocks()
        localStorage.clear()

        // Mock SSE 流：每次调用都返回相同的 session_id
        mockStreamSuccess(fixedSessionId, '回复')

        const { sendMessage } = useChat()

        // 依次发送所有消息
        for (const msg of msgs) {
          await sendMessage(msg)
        }

        // 验证 API 被调用了 msgs.length 次
        expect(sendMessageStream).toHaveBeenCalledTimes(msgs.length)

        // 第一次调用时 session_id 为 null（首次对话）
        expect(sendMessageStream).toHaveBeenNthCalledWith(1,
          { message: msgs[0], session_id: null },
          expect.any(Function),
        )

        // 所有后续调用应携带相同的 session_id
        for (let i = 1; i < msgs.length; i++) {
          expect(sendMessageStream).toHaveBeenNthCalledWith(i + 1,
            { message: msgs[i], session_id: fixedSessionId },
            expect.any(Function),
          )
        }
      }),
      { numRuns: 100 },
    )
  })
})
