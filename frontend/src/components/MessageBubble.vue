<script setup lang="ts">
import { computed } from 'vue'
// @ts-ignore — markdown-it 无自带类型定义
import MarkdownIt from 'markdown-it'
import type { Message } from '../composables/useChat'

// 初始化 markdown-it 实例
const md = new MarkdownIt({ html: false, linkify: true, breaks: true })

const props = defineProps<{
  message: Message
}>()

/** 渲染 Markdown 内容（仅代理回复使用） */
const renderedHtml = computed(() => {
  if (props.message.role === 'assistant') {
    try {
      return md.render(props.message.content)
    } catch {
      // Markdown 渲染失败时降级为纯文本
      return props.message.content
    }
  }
  return ''
})

/** 格式化时间戳为 HH:MM */
const formattedTime = computed(() => {
  const date = new Date(props.message.timestamp)
  const h = date.getHours().toString().padStart(2, '0')
  const m = date.getMinutes().toString().padStart(2, '0')
  return `${h}:${m}`
})
</script>

<template>
  <div class="message-row" :class="`message-row--${message.role}`">
    <div class="bubble" :class="`bubble--${message.role}`">
      <!-- 代理回复：渲染 Markdown -->
      <div
        v-if="message.role === 'assistant'"
        class="bubble-content markdown-body"
        v-html="renderedHtml"
      />
      <!-- 用户消息 / 错误消息：纯文本 -->
      <div v-else class="bubble-content">{{ message.content }}</div>
    </div>
    <span class="timestamp">{{ formattedTime }}</span>
  </div>
</template>

<style scoped>
/* 消息行：控制对齐方向 */
.message-row {
  display: flex;
  flex-direction: column;
  margin-bottom: 16px;
  max-width: 80%;
}

.message-row--user {
  align-self: flex-end;
  align-items: flex-end;
}

.message-row--assistant,
.message-row--error {
  align-self: flex-start;
  align-items: flex-start;
}

/* 气泡基础样式 */
.bubble {
  padding: 10px 14px;
  border-radius: var(--radius);
  line-height: 1.6;
  word-break: break-word;
}

/* 用户消息：蓝色背景、白色文字、右对齐 */
.bubble--user {
  background-color: var(--color-user-bubble);
  color: var(--color-user-text);
}

/* 代理回复：白色背景、深色文字、左对齐 */
.bubble--assistant {
  background-color: var(--color-card);
  color: var(--color-text);
  box-shadow: var(--shadow);
}

/* 错误消息：红色调 */
.bubble--error {
  background-color: var(--color-error-bg);
  color: var(--color-error);
}

/* 时间戳 */
.timestamp {
  font-size: 11px;
  color: var(--color-text-secondary);
  margin-top: 4px;
}

/* Markdown 内容样式 */
.markdown-body :deep(p) {
  margin: 0 0 8px;
}

.markdown-body :deep(p:last-child) {
  margin-bottom: 0;
}

.markdown-body :deep(pre) {
  background-color: #f6f8fa;
  padding: 12px;
  border-radius: 6px;
  overflow-x: auto;
  margin: 8px 0;
}

.markdown-body :deep(code) {
  font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', Menlo, monospace;
  font-size: 13px;
}

.markdown-body :deep(ul),
.markdown-body :deep(ol) {
  padding-left: 20px;
  margin: 8px 0;
}

.markdown-body :deep(table) {
  border-collapse: collapse;
  margin: 8px 0;
  width: 100%;
}

.markdown-body :deep(th),
.markdown-body :deep(td) {
  border: 1px solid var(--color-border);
  padding: 6px 12px;
  text-align: left;
}

.markdown-body :deep(th) {
  background-color: #f6f8fa;
  font-weight: 600;
}
</style>
