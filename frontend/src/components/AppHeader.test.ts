import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import AppHeader from './AppHeader.vue'

describe('AppHeader', () => {
  it('渲染应用标题和说明', () => {
    const wrapper = mount(AppHeader)
    expect(wrapper.find('.app-title').text()).toBe('ExcelManus')
    expect(wrapper.find('.app-desc').text()).toBe('智能 Excel 代理 — 用自然语言处理 Excel 任务')
  })

  it('包含新建会话按钮', () => {
    const wrapper = mount(AppHeader)
    const btn = wrapper.find('.new-session-btn')
    expect(btn.exists()).toBe(true)
    expect(btn.text()).toBe('新建会话')
  })

  it('点击按钮时 emit new-session 事件', async () => {
    const wrapper = mount(AppHeader)
    await wrapper.find('.new-session-btn').trigger('click')
    expect(wrapper.emitted('new-session')).toHaveLength(1)
  })
})
