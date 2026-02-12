// Feature: vue-frontend, Property 4: Markdown 渲染正确性
// **Validates: Requirements 3.6**

import { describe, it, expect } from 'vitest'
import fc from 'fast-check'
// @ts-ignore — markdown-it 无自带类型定义
import MarkdownIt from 'markdown-it'

// 使用与组件相同的配置初始化 markdown-it
const md = new MarkdownIt({ html: false, linkify: true, breaks: true })

describe('Property 4: Markdown 渲染正确性', () => {
  // 对于任意包含 Markdown 代码块（```）的字符串，
  // 渲染函数的输出应包含 <pre> 和 <code> HTML 标签
  it('包含代码块的字符串渲染后应含 <pre> 和 <code> 标签', () => {
    fc.assert(
      fc.property(fc.string(), (content) => {
        // 将任意字符串包裹在代码块围栏中
        const markdown = '```\n' + content + '\n```'
        const html = md.render(markdown)
        expect(html).toContain('<pre>')
        expect(html).toContain('<code>')
      }),
      { numRuns: 100 },
    )
  })
})
