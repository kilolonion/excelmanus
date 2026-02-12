import { describe, it, expect, beforeEach, vi } from 'vitest'
import { useChat } from './useChat'
import type { StreamEvent } from '../api'

// Mock api 模块：使用 sendMessageStream 替代旧的 sendMessage
vi.mock('../api', () => ({
  sendMessageStream: vi.fn(),
}))

// Mock useSession
vi.mock('./useSession', () => ({
  useSession: vi.fn(() => ({
    sessionId: { value: null },
    setSessionId: vi.fn(),
    clearSession: vi.fn(),
  })),
}))

import { sendMessageStream } from '../api'
import { useSession } from './useSession'

const mockSendMessageStream = vi.mocked(sendMessageStream)
const mockUseSession = vi.mocked(useSession)

/** 模拟 SSE 流：依次触发事件回调 */
function mockStreamSuccess(sessionId: string, reply: string) {
  mockSendMessageStream.mockImplementation(async (_req, onEvent) => {
    onEvent({ type: 'session_init', session_id: sessionId } as StreamEvent)
    onEvent({ type: 'reply', content: reply, skills_used: ['data_basic'], tool_scope: ['read_excel'], route_mode: 'hint_direct' } as StreamEvent)
    onEvent({ type: 'done' } as StreamEvent)
  })
}

describe('useChat', () => {
  let mockSetSessionId: ReturnType<typeof vi.fn>
  let mockSessionId: { value: string | null }

  beforeEach(() => {
    vi.clearAllMocks()
    mockSetSessionId = vi.fn()
    mockSessionId = { value: null }
    mockUseSession.mockReturnValue({
      sessionId: mockSessionId as any,
      setSessionId: mockSetSessionId,
      clearSession: vi.fn(),
    })
  })

  // ========== sendMessage 基本流程 ==========

  it('发送消息后消息列表包含用户消息和代理回复', async () => {
    mockStreamSuccess('sess-1', '处理完成')

    const { messages, sendMessage } = useChat()
    await sendMessage('你好')

    expect(messages.value).toHaveLength(2)
    expect(messages.value[0].role).toBe('user')
    expect(messages.value[0].content).toBe('你好')
    expect(messages.value[1].role).toBe('assistant')
    expect(messages.value[1].content).toBe('处理完成')
  })

  it('发送成功后更新 session_id', async () => {
    mockStreamSuccess('new-sess', 'ok')

    const { sendMessage } = useChat()
    await sendMessage('测试')

    expect(mockSetSessionId).toHaveBeenCalledWith('new-sess')
  })

  it('发送时携带已有的 session_id', async () => {
    mockSessionId.value = 'existing-sess'
    mockStreamSuccess('existing-sess', 'ok')

    const { sendMessage } = useChat()
    await sendMessage('测试')

    expect(mockSendMessageStream).toHaveBeenCalledWith(
      { message: '测试', session_id: 'existing-sess' },
      expect.any(Function),
    )
  })

  // ========== 空白消息拒绝 ==========

  it('空字符串不发送', async () => {
    const { messages, sendMessage } = useChat()
    await sendMessage('')
    expect(messages.value).toHaveLength(0)
    expect(mockSendMessageStream).not.toHaveBeenCalled()
  })

  it('纯空格不发送', async () => {
    const { messages, sendMessage } = useChat()
    await sendMessage('   ')
    expect(messages.value).toHaveLength(0)
    expect(mockSendMessageStream).not.toHaveBeenCalled()
  })

  it('制表符和换行不发送', async () => {
    const { messages, sendMessage } = useChat()
    await sendMessage('\t\n  \r\n')
    expect(messages.value).toHaveLength(0)
    expect(mockSendMessageStream).not.toHaveBeenCalled()
  })

  // ========== loading 状态 ==========

  it('发送期间 loading 为 true', async () => {
    let resolveStream: () => void
    mockSendMessageStream.mockImplementation(async (_req, onEvent) => {
      onEvent({ type: 'session_init', session_id: 's' } as StreamEvent)
      await new Promise<void>((r) => { resolveStream = r })
      onEvent({ type: 'reply', content: 'r', skills_used: [], tool_scope: [], route_mode: '' } as StreamEvent)
    })

    const { loading, sendMessage } = useChat()
    const promise = sendMessage('测试')

    expect(loading.value).toBe(true)

    resolveStream!()
    await promise

    expect(loading.value).toBe(false)
  })

  it('loading 期间阻止重复发送', async () => {
    let resolveStream: () => void
    mockSendMessageStream.mockImplementation(async (_req, onEvent) => {
      onEvent({ type: 'session_init', session_id: 's' } as StreamEvent)
      await new Promise<void>((r) => { resolveStream = r })
      onEvent({ type: 'reply', content: 'r', skills_used: [], tool_scope: [], route_mode: '' } as StreamEvent)
    })

    const { messages, sendMessage } = useChat()
    const p1 = sendMessage('第一条')
    // loading 中尝试发送第二条
    await sendMessage('第二条')

    // 只有第一条用户消息被添加（+ 流式 assistant 占位）
    expect(messages.value.filter((m) => m.role === 'user')).toHaveLength(1)
    expect(mockSendMessageStream).toHaveBeenCalledTimes(1)

    resolveStream!()
    await p1
  })

  // ========== 错误处理 ==========

  it('API 错误时添加 error 消息', async () => {
    mockSendMessageStream.mockRejectedValue(new Error('网络连接失败'))

    const { messages, sendMessage, loading } = useChat()
    await sendMessage('测试')

    // user + error（空 assistant 消息被移除）
    expect(messages.value).toHaveLength(2)
    expect(messages.value[1].role).toBe('error')
    expect(messages.value[1].content).toBe('网络连接失败')
    expect(loading.value).toBe(false)
  })

  it('非 Error 异常时显示"未知错误"', async () => {
    mockSendMessageStream.mockRejectedValue('字符串异常')

    const { messages, sendMessage } = useChat()
    await sendMessage('测试')

    expect(messages.value[1].role).toBe('error')
    expect(messages.value[1].content).toBe('未知错误')
  })

  // ========== clearMessages ==========

  it('clearMessages 清空消息列表', async () => {
    mockStreamSuccess('s', 'ok')

    const { messages, sendMessage, clearMessages } = useChat()
    await sendMessage('测试')
    expect(messages.value.length).toBeGreaterThan(0)

    clearMessages()
    expect(messages.value).toHaveLength(0)
  })

  // ========== retryLast ==========

  it('retryLast 重新发送最后一条用户消息', async () => {
    // 第一次发送失败
    mockSendMessageStream.mockRejectedValueOnce(new Error('失败'))

    const { messages, sendMessage, retryLast } = useChat()
    await sendMessage('重试测试')

    // user + error（空 assistant 被移除）
    expect(messages.value).toHaveLength(2)
    expect(messages.value[1].role).toBe('error')

    // 重试成功
    mockStreamSuccess('s', '成功')
    await retryLast()

    // 重试后：用户消息 + 代理回复
    expect(messages.value).toHaveLength(2)
    expect(messages.value[0].role).toBe('user')
    expect(messages.value[1].role).toBe('assistant')
    expect(messages.value[1].content).toBe('成功')
  })

  it('retryLast 无用户消息时不执行', async () => {
    const { retryLast } = useChat()
    await retryLast()
    expect(mockSendMessageStream).not.toHaveBeenCalled()
  })

  // ========== 消息结构 ==========

  it('消息包含 id、role、content、timestamp', async () => {
    mockStreamSuccess('s', '回复')

    const { messages, sendMessage } = useChat()
    await sendMessage('测试')

    for (const msg of messages.value) {
      expect(msg.id).toBeTruthy()
      expect(typeof msg.id).toBe('string')
      expect(['user', 'assistant', 'error']).toContain(msg.role)
      expect(typeof msg.content).toBe('string')
      expect(typeof msg.timestamp).toBe('number')
    }
  })
})
