import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import MessageBubble from './MessageBubble.vue'
import type { Message } from '../composables/useChat'

/** 创建测试用消息 */
function makeMsg(overrides: Partial<Message> = {}): Message {
  return {
    id: '1',
    role: 'user',
    content: '你好',
    timestamp: new Date(2025, 0, 15, 14, 30).getTime(),
    ...overrides,
  }
}

describe('MessageBubble', () => {
  // ========== 用户消息 ==========
  it('用户消息右对齐、蓝色气泡', () => {
    const wrapper = mount(MessageBubble, {
      props: { message: makeMsg({ role: 'user', content: '分析销售数据' }) },
    })
    expect(wrapper.find('.message-row--user').exists()).toBe(true)
    expect(wrapper.find('.bubble--user').exists()).toBe(true)
    expect(wrapper.find('.bubble-content').text()).toBe('分析销售数据')
  })

  // ========== 代理回复 ==========
  it('代理回复左对齐、白色气泡，渲染 Markdown', () => {
    const wrapper = mount(MessageBubble, {
      props: { message: makeMsg({ role: 'assistant', content: '**加粗文本**' }) },
    })
    expect(wrapper.find('.message-row--assistant').exists()).toBe(true)
    expect(wrapper.find('.bubble--assistant').exists()).toBe(true)
    // Markdown 渲染后应包含 <strong> 标签
    const html = wrapper.find('.markdown-body').html()
    expect(html).toContain('<strong>')
    expect(html).toContain('加粗文本')
  })

  it('代理回复渲染代码块', () => {
    const content = '```python\nprint("hello")\n```'
    const wrapper = mount(MessageBubble, {
      props: { message: makeMsg({ role: 'assistant', content }) },
    })
    // 使用 element.innerHTML 获取不含外层标签的内容
    const inner = wrapper.find('.markdown-body').element.innerHTML
    expect(inner).toContain('<pre>')
    expect(inner).toContain('<code')
  })

  // ========== 错误消息 ==========
  it('错误消息左对齐、红色气泡', () => {
    const wrapper = mount(MessageBubble, {
      props: { message: makeMsg({ role: 'error', content: '网络连接失败' }) },
    })
    expect(wrapper.find('.message-row--error').exists()).toBe(true)
    expect(wrapper.find('.bubble--error').exists()).toBe(true)
    expect(wrapper.find('.bubble-content').text()).toBe('网络连接失败')
  })

  // ========== 时间戳 ==========
  it('显示格式化的时间戳 HH:MM', () => {
    const wrapper = mount(MessageBubble, {
      props: { message: makeMsg() }, // timestamp = 14:30
    })
    expect(wrapper.find('.timestamp').text()).toBe('14:30')
  })

  it('时间戳小时和分钟补零', () => {
    // 03:05
    const ts = new Date(2025, 0, 15, 3, 5).getTime()
    const wrapper = mount(MessageBubble, {
      props: { message: makeMsg({ timestamp: ts }) },
    })
    expect(wrapper.find('.timestamp').text()).toBe('03:05')
  })

  // ========== Markdown 降级 ==========
  it('用户消息不渲染 Markdown，显示纯文本', () => {
    const wrapper = mount(MessageBubble, {
      props: { message: makeMsg({ role: 'user', content: '**不应加粗**' }) },
    })
    // 用户消息不使用 v-html
    expect(wrapper.find('.markdown-body').exists()).toBe(false)
    expect(wrapper.find('.bubble-content').text()).toBe('**不应加粗**')
  })
})
