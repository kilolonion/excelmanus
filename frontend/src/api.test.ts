// API 客户端单元测试
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import {
  sendMessage,
  deleteSession,
  checkHealth,
  getErrorMessage,
} from './api'

// 模拟 fetch
const mockFetch = vi.fn()

beforeEach(() => {
  vi.stubGlobal('fetch', mockFetch)
})

afterEach(() => {
  vi.restoreAllMocks()
})

// ========== getErrorMessage 测试 ==========

describe('getErrorMessage', () => {
  it('429 返回会话超限提示', () => {
    expect(getErrorMessage(429)).toBe('会话数量已达上限，请稍后重试')
  })

  it('409 返回任务处理中提示', () => {
    expect(getErrorMessage(409)).toBe('当前任务正在处理中，请等待完成')
  })

  it('通用错误包含状态码和描述', () => {
    expect(getErrorMessage(500, { error: '内部错误' })).toBe(
      '请求失败（500）：内部错误',
    )
  })

  it('通用错误无 body 时仅包含状态码', () => {
    expect(getErrorMessage(500)).toBe('请求失败（500）')
  })
})

// ========== sendMessage 测试 ==========

describe('sendMessage', () => {
  it('成功发送消息并返回响应', async () => {
    const mockResponse = {
      session_id: 'abc',
      reply: '你好',
      skills_used: ['data_basic'],
      tool_scope: ['read_excel'],
      route_mode: 'hint_direct',
    }
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve(mockResponse),
    })

    const result = await sendMessage({ message: '你好' })
    expect(result).toEqual(mockResponse)
    expect(mockFetch).toHaveBeenCalledWith('/api/v1/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message: '你好' }),
    })
  })

  it('携带 session_id 发送', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve({
        session_id: 's1',
        reply: 'ok',
        skills_used: ['data_basic'],
        tool_scope: ['read_excel'],
        route_mode: 'hint_direct',
      }),
    })

    await sendMessage({ message: 'test', session_id: 's1' })
    const body = JSON.parse(mockFetch.mock.calls[0][1].body)
    expect(body.session_id).toBe('s1')
  })

  it('HTTP 429 抛出会话超限错误', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: false,
      status: 429,
      json: () => Promise.resolve({ error: 'too many' }),
    })

    await expect(sendMessage({ message: 'hi' })).rejects.toThrow(
      '会话数量已达上限，请稍后重试',
    )
  })

  it('HTTP 409 抛出任务处理中错误', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: false,
      status: 409,
      json: () => Promise.resolve({ error: 'busy' }),
    })

    await expect(sendMessage({ message: 'hi' })).rejects.toThrow(
      '当前任务正在处理中，请等待完成',
    )
  })

  it('网络错误抛出连接失败', async () => {
    mockFetch.mockRejectedValueOnce(new TypeError('Failed to fetch'))

    await expect(sendMessage({ message: 'hi' })).rejects.toThrow(
      '网络连接失败',
    )
  })

  it('缺少 v3 字段时抛出协议错误', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve({ session_id: 's1', reply: 'ok' }),
    })

    await expect(sendMessage({ message: 'hi' })).rejects.toThrow(
      '后端响应格式不符合 v3 协议（chat）',
    )
  })
})

// ========== deleteSession 测试 ==========

describe('deleteSession', () => {
  it('成功删除会话', async () => {
    mockFetch.mockResolvedValueOnce({ ok: true })

    await expect(deleteSession('s1')).resolves.toBeUndefined()
    expect(mockFetch).toHaveBeenCalledWith('/api/v1/sessions/s1', {
      method: 'DELETE',
    })
  })

  it('删除失败抛出错误', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: false,
      status: 404,
      json: () => Promise.resolve({ error: 'not found' }),
    })

    await expect(deleteSession('s1')).rejects.toThrow('请求失败（404）')
  })
})

// ========== checkHealth 测试 ==========

describe('checkHealth', () => {
  it('成功返回健康状态', async () => {
    const mockHealth = {
      status: 'ok',
      version: '3.0.0',
      model: 'test-model',
      tools: ['read_excel'],
      skillpacks: ['data_basic'],
    }
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve(mockHealth),
    })

    const result = await checkHealth()
    expect(result).toEqual(mockHealth)
    expect(mockFetch).toHaveBeenCalledWith('/api/v1/health')
  })

  it('网络错误抛出连接失败', async () => {
    mockFetch.mockRejectedValueOnce(new TypeError('Failed to fetch'))

    await expect(checkHealth()).rejects.toThrow('网络连接失败')
  })

  it('缺少 v3 字段时抛出协议错误', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve({ status: 'ok', version: '3.0.0' }),
    })

    await expect(checkHealth()).rejects.toThrow(
      '后端响应格式不符合 v3 协议（health）',
    )
  })
})
