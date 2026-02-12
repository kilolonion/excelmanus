// Feature: vue-frontend, Property 8: HTTP 错误消息生成
// **Validates: Requirements 5.1**

import { describe, it, expect, vi, beforeEach } from 'vitest'
import fc from 'fast-check'
import { getErrorMessage } from './api'

// Mock api 模块中的 sendMessage，阻止真实网络请求
vi.mock('./api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('./api')>()
  return {
    ...actual,
    sendMessage: vi.fn(),
  }
})

// Mock useSession composable
vi.mock('./composables/useSession', () => ({
  useSession: () => ({
    sessionId: { value: null },
    setSessionId: vi.fn(),
    clearSession: vi.fn(),
  }),
}))

import { sendMessage as apiSendMessage } from './api'
import { useChat } from './composables/useChat'

// HTTP 错误状态码生成器（400-599）
const httpErrorStatusArb = fc.integer({ min: 400, max: 599 })

describe('Property 8: HTTP 错误消息生成', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  // 对于任意 HTTP 错误状态码（400-599），getErrorMessage 应返回非空字符串
  it('getErrorMessage 对任意 400-599 状态码应返回非空字符串', () => {
    fc.assert(
      fc.property(httpErrorStatusArb, (status) => {
        const msg = getErrorMessage(status)
        expect(typeof msg).toBe('string')
        expect(msg.length).toBeGreaterThan(0)
      }),
      { numRuns: 100 },
    )
  })

  // 对于任意 HTTP 错误状态码（400-599），当 API 抛出该状态码对应的错误时，
  // 消息列表应包含一条 role 为 error 的消息，且内容为非空字符串
  it('API 抛出错误后，消息列表应包含非空 error 消息', async () => {
    await fc.assert(
      fc.asyncProperty(httpErrorStatusArb, async (status) => {
        vi.clearAllMocks()

        // 模拟 API 抛出对应状态码的错误消息
        const errorMsg = getErrorMessage(status)
        vi.mocked(apiSendMessage).mockRejectedValue(new Error(errorMsg))

        const { messages, sendMessage } = useChat()

        // 发送一条有效消息，触发 API 调用
        await sendMessage('测试消息')

        // 消息列表应包含 2 条：用户消息 + 错误消息
        expect(messages.value.length).toBe(2)

        // 最后一条应为 error 角色，内容非空
        const errorMessage = messages.value.find((m) => m.role === 'error')
        expect(errorMessage).toBeDefined()
        expect(typeof errorMessage!.content).toBe('string')
        expect(errorMessage!.content.length).toBeGreaterThan(0)
      }),
      { numRuns: 100 },
    )
  })
})
