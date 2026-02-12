import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import { nextTick } from 'vue'
import ChatPanel from './ChatPanel.vue'
import type { Message } from '../composables/useChat'

/** 创建测试用消息 */
function makeMsg(overrides: Partial<Message> = {}): Message {
  return {
    id: '1',
    role: 'user',
    content: '你好',
    timestamp: Date.now(),
    ...overrides,
  }
}

describe('ChatPanel', () => {
  // ========== 空状态 ==========
  it('消息为空且非 loading 时显示欢迎提示', () => {
    const wrapper = mount(ChatPanel, {
      props: { messages: [], loading: false },
    })
    expect(wrapper.find('.empty-state').exists()).toBe(true)
    expect(wrapper.find('.welcome-text').text()).toContain('👋 你好！请在下方输入框中描述你的 Excel 任务。')
  })

  it('消息为空但 loading 时不显示欢迎提示（有流式消息占位）', () => {
    // loading 时会有流式 assistant 消息占位，messages 不为空
    const wrapper = mount(ChatPanel, {
      props: { messages: [makeMsg({ role: 'assistant', content: '', id: 'stream' })], loading: true },
    })
    expect(wrapper.find('.empty-state').exists()).toBe(false)
  })

  // ========== 消息列表渲染 ==========
  it('渲染消息列表中的每条消息', () => {
    const messages: Message[] = [
      makeMsg({ id: '1', role: 'user', content: '分析数据' }),
      makeMsg({ id: '2', role: 'assistant', content: '好的，正在分析' }),
    ]
    const wrapper = mount(ChatPanel, {
      props: { messages, loading: false },
    })
    // MessageBubble 组件应被渲染两次
    const bubbles = wrapper.findAllComponents({ name: 'MessageBubble' })
    expect(bubbles).toHaveLength(2)
  })

  it('有消息时不显示欢迎提示', () => {
    const wrapper = mount(ChatPanel, {
      props: { messages: [makeMsg()], loading: false },
    })
    expect(wrapper.find('.empty-state').exists()).toBe(false)
  })

  // ========== 流式状态渲染 ==========
  it('loading 时渲染流式 assistant 消息', () => {
    const messages: Message[] = [
      makeMsg({ id: '1', role: 'user', content: '分析' }),
      makeMsg({ id: '2', role: 'assistant', content: '', streaming: true }),
    ]
    const wrapper = mount(ChatPanel, {
      props: { messages, loading: true },
    })
    const bubbles = wrapper.findAllComponents({ name: 'MessageBubble' })
    expect(bubbles).toHaveLength(2)
  })

  // ========== 自动滚动 ==========
  it('消息变化时触发自动滚动', async () => {
    const wrapper = mount(ChatPanel, {
      props: { messages: [], loading: false },
    })
    const panel = wrapper.find('.chat-panel').element

    // 模拟 scrollHeight 大于 clientHeight
    Object.defineProperty(panel, 'scrollHeight', { value: 1000, configurable: true })

    // 更新 props 添加消息
    await wrapper.setProps({
      messages: [makeMsg({ id: '1', content: '新消息' })],
    })
    await nextTick()
    await nextTick() // watch + nextTick 需要额外一轮

    // scrollTop 应被设置为 scrollHeight
    expect(panel.scrollTop).toBe(1000)
  })

  // ========== 多条消息 key 唯一性 ==========
  it('每条消息使用 msg.id 作为 key', () => {
    const messages: Message[] = [
      makeMsg({ id: 'a', content: '消息1' }),
      makeMsg({ id: 'b', content: '消息2' }),
      makeMsg({ id: 'c', content: '消息3' }),
    ]
    const wrapper = mount(ChatPanel, {
      props: { messages, loading: false },
    })
    const bubbles = wrapper.findAllComponents({ name: 'MessageBubble' })
    expect(bubbles).toHaveLength(3)
  })
})
