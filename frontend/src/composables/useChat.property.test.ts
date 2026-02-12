// Feature: vue-frontend, Property 2: 空白消息拒绝
// **Validates: Requirements 3.3**

import { describe, it, expect, vi, beforeEach } from 'vitest'
import fc from 'fast-check'

// Mock api 模块，阻止真实网络请求
vi.mock('../api', () => ({
  sendMessage: vi.fn(),
}))

// Mock useSession composable
vi.mock('./useSession', () => ({
  useSession: () => ({
    sessionId: { value: null },
    setSessionId: vi.fn(),
    clearSession: vi.fn(),
  }),
}))

import { sendMessage as apiSendMessage } from '../api'
import { useChat } from './useChat'

describe('Property 2: 空白消息拒绝', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  // 对于任意仅由空白字符组成的字符串，调用发送操作后，
  // 消息列表长度应保持不变，且不应触发任何 API 请求
  it('空白字符串不应添加消息，也不应调用 API', async () => {
    // 生成仅包含空白字符（空格、制表符、换行符等）的任意字符串
    const whitespaceArb = fc.stringOf(
      fc.constantFrom(' ', '\t', '\n', '\r', '\f', '\v'),
    )

    await fc.assert(
      fc.asyncProperty(whitespaceArb, async (input) => {
        vi.clearAllMocks()
        const { messages, sendMessage } = useChat()

        // 发送前消息列表为空
        expect(messages.value.length).toBe(0)

        await sendMessage(input)

        // 发送后消息列表长度不变
        expect(messages.value.length).toBe(0)

        // 不应触发 API 请求
        expect(apiSendMessage).not.toHaveBeenCalled()
      }),
      { numRuns: 100 },
    )
  })
})

// Feature: vue-frontend, Property 1: 对话完整性
// **Validates: Requirements 3.1, 3.2**

describe('Property 1: 对话完整性', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  // 对于任意非空消息字符串和任意有效的后端回复，执行一次完整的发送-接收流程后，
  // 消息列表应同时包含一条 role 为 user 的消息和一条 role 为 assistant 的消息
  it('发送非空消息后，消息列表应包含用户消息和代理回复', async () => {
    // 生成任意非空字符串（至少包含一个非空白字符）
    const nonEmptyArb = fc.string({ minLength: 1 }).filter((s) => s.trim().length > 0)

    await fc.assert(
      fc.asyncProperty(nonEmptyArb, nonEmptyArb, async (userMsg, replyContent) => {
        vi.clearAllMocks()

        // Mock API 返回生成的回复内容和 session_id
        vi.mocked(apiSendMessage).mockResolvedValue({
          session_id: 'test-session',
          reply: replyContent,
        })

        const { messages, sendMessage } = useChat()

        // 执行发送
        await sendMessage(userMsg)

        // 消息列表应恰好包含 2 条消息
        expect(messages.value.length).toBe(2)

        // 第一条为用户消息，内容等于发送内容
        const userMessage = messages.value.find((m) => m.role === 'user')
        expect(userMessage).toBeDefined()
        expect(userMessage!.content).toBe(userMsg)

        // 第二条为代理回复，内容等于后端回复内容
        const assistantMessage = messages.value.find((m) => m.role === 'assistant')
        expect(assistantMessage).toBeDefined()
        expect(assistantMessage!.content).toBe(replyContent)
      }),
      { numRuns: 100 },
    )
  })
})

// Feature: vue-frontend, Property 3: Loading 状态禁用发送
// **Validates: Requirements 3.5**

describe('Property 3: Loading 状态禁用发送', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  // 对于任意处于 loading 状态的对话上下文，尝试发送任意消息应被阻止，
  // 消息列表长度不应增加
  it('loading 为 true 时，发送任意非空消息应被阻止', async () => {
    // 生成任意非空字符串（至少包含一个非空白字符）
    const nonEmptyArb = fc.string({ minLength: 1 }).filter((s) => s.trim().length > 0)

    await fc.assert(
      fc.asyncProperty(nonEmptyArb, nonEmptyArb, async (firstMsg, secondMsg) => {
        vi.clearAllMocks()

        // 创建一个永不 resolve 的 promise，使 loading 保持为 true
        vi.mocked(apiSendMessage).mockReturnValue(new Promise(() => {}))

        const { messages, loading, sendMessage } = useChat()

        // 发送第一条消息（不会 resolve，loading 将保持 true）
        const pendingPromise = sendMessage(firstMsg)

        // 此时 loading 应为 true，消息列表应有 1 条用户消息
        expect(loading.value).toBe(true)
        expect(messages.value.length).toBe(1)

        const countBeforeSecond = messages.value.length

        // 尝试发送第二条消息，应被 loading 状态阻止
        await sendMessage(secondMsg)

        // 消息列表长度不应增加
        expect(messages.value.length).toBe(countBeforeSecond)

        // API 应只被调用一次（第一条消息）
        expect(apiSendMessage).toHaveBeenCalledTimes(1)
      }),
      { numRuns: 100 },
    )
  })
})
