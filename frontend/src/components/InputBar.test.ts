import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import InputBar from './InputBar.vue'

describe('InputBar', () => {
  it('渲染输入框和发送按钮', () => {
    const wrapper = mount(InputBar, { props: { disabled: false } })
    expect(wrapper.find('.input-textarea').exists()).toBe(true)
    expect(wrapper.find('.send-btn').text()).toBe('发送')
  })

  it('输入框显示正确的 placeholder', () => {
    const wrapper = mount(InputBar, { props: { disabled: false } })
    const textarea = wrapper.find('.input-textarea')
    expect(textarea.attributes('placeholder')).toBe('输入你的 Excel 任务...')
  })

  it('点击发送按钮 emit send 事件（内容已 trim）', async () => {
    const wrapper = mount(InputBar, { props: { disabled: false } })
    const textarea = wrapper.find('.input-textarea')
    await textarea.setValue('  分析销售数据  ')
    await wrapper.find('.send-btn').trigger('click')
    expect(wrapper.emitted('send')).toHaveLength(1)
    expect(wrapper.emitted('send')![0]).toEqual(['分析销售数据'])
  })

  it('发送后清空输入框', async () => {
    const wrapper = mount(InputBar, { props: { disabled: false } })
    const textarea = wrapper.find('.input-textarea')
    await textarea.setValue('测试消息')
    await wrapper.find('.send-btn').trigger('click')
    expect((textarea.element as HTMLTextAreaElement).value).toBe('')
  })

  it('空白消息不触发 send 事件', async () => {
    const wrapper = mount(InputBar, { props: { disabled: false } })
    await wrapper.find('.input-textarea').setValue('   ')
    await wrapper.find('.send-btn').trigger('click')
    expect(wrapper.emitted('send')).toBeUndefined()
  })

  it('Enter 键触发发送', async () => {
    const wrapper = mount(InputBar, { props: { disabled: false } })
    await wrapper.find('.input-textarea').setValue('Enter 发送')
    await wrapper.find('.input-textarea').trigger('keydown', { key: 'Enter', shiftKey: false })
    expect(wrapper.emitted('send')).toHaveLength(1)
    expect(wrapper.emitted('send')![0]).toEqual(['Enter 发送'])
  })

  it('Shift+Enter 不触发发送（允许换行）', async () => {
    const wrapper = mount(InputBar, { props: { disabled: false } })
    await wrapper.find('.input-textarea').setValue('第一行')
    await wrapper.find('.input-textarea').trigger('keydown', { key: 'Enter', shiftKey: true })
    expect(wrapper.emitted('send')).toBeUndefined()
  })

  it('disabled 状态下输入框和按钮被禁用', () => {
    const wrapper = mount(InputBar, { props: { disabled: true } })
    const textarea = wrapper.find('.input-textarea')
    const btn = wrapper.find('.send-btn')
    expect((textarea.element as HTMLTextAreaElement).disabled).toBe(true)
    expect((btn.element as HTMLButtonElement).disabled).toBe(true)
  })

  it('disabled 状态下点击发送不触发事件', async () => {
    const wrapper = mount(InputBar, { props: { disabled: true } })
    await wrapper.find('.input-textarea').setValue('测试')
    await wrapper.find('.send-btn').trigger('click')
    expect(wrapper.emitted('send')).toBeUndefined()
  })

  it('disabled 状态下 Enter 键不触发发送', async () => {
    const wrapper = mount(InputBar, { props: { disabled: true } })
    await wrapper.find('.input-textarea').setValue('测试')
    await wrapper.find('.input-textarea').trigger('keydown', { key: 'Enter', shiftKey: false })
    expect(wrapper.emitted('send')).toBeUndefined()
  })
})
