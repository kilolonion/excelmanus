<script setup lang="ts">
import { computed } from 'vue'
// @ts-ignore — markdown-it 无自带类型定义
import MarkdownIt from 'markdown-it'
import type { Message } from '../composables/useChat'
import ThinkingBlock from './ThinkingBlock.vue'
import ToolCallCard from './ToolCallCard.vue'

// 初始化 markdown-it 实例
const md = new MarkdownIt({ html: false, linkify: true, breaks: true })

const props = defineProps<{
  message: Message
}>()

/** 是否有思考内容 */
const hasThinking = computed(() => !!props.message.thinking)

/** 是否有工具调用 */
const hasToolCalls = computed(() => !!props.message.toolCalls?.length)

/** 渲染 Markdown 内容（仅代理回复使用） */
const renderedHtml = computed(() => {
  if (props.message.role === 'assistant' && props.message.content) {
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
  <!-- 用户消息：右对齐气泡 -->
  <div v-if="message.role === 'user'" class="message-row message-row--user">
    <div class="bubble bubble--user">
      <div class="bubble-content">{{ message.content }}</div>
    </div>
    <span class="timestamp">{{ formattedTime }}</span>
  </div>

  <!-- 错误消息：左对齐红色气泡 -->
  <div v-else-if="message.role === 'error'" class="message-row message-row--error">
    <div class="bubble bubble--error">
      <div class="bubble-content">{{ message.content }}</div>
    </div>
    <span class="timestamp">{{ formattedTime }}</span>
  </div>

  <!-- 代理消息：自然流式布局，无气泡包装 -->
  <div v-else class="message-row message-row--assistant">
    <!-- 思考过程折叠块 -->
    <ThinkingBlock
      v-if="hasThinking"
      :content="message.thinking!"
      :streaming="message.streaming && !message.content"
    />
    <!-- 工具调用卡片列表 -->
    <div v-if="hasToolCalls" class="tool-calls-list">
      <ToolCallCard
        v-for="(tc, idx) in message.toolCalls"
        :key="idx"
        :tool-call="tc"
      />
    </div>
    <!-- 最终回复：渲染 Markdown -->
    <div
      v-if="renderedHtml"
      class="assistant-content markdown-body"
      v-html="renderedHtml"
    />
    <!-- 流式接收中但尚无最终回复时的等待提示 -->
    <div v-else-if="message.streaming && !hasToolCalls && !hasThinking" class="streaming-hint">
      <span class="streaming-dot"></span>
      <span class="streaming-dot"></span>
      <span class="streaming-dot"></span>
    </div>
  </div>
</template>

<style scoped>
/* ========== 消息行 ========== */
.message-row {
  display: flex;
  flex-direction: column;
  margin-bottom: 18px;
  animation: fadeInUp 0.3s ease both;
}

.message-row--user {
  align-self: flex-end;
  align-items: flex-end;
  max-width: 75%;
}

.message-row--assistant {
  align-self: flex-start;
  align-items: flex-start;
  width: 100%;
}

.message-row--error {
  align-self: flex-start;
  align-items: flex-start;
  max-width: 75%;
}

/* ========== 用户 / 错误气泡 ========== */
.bubble {
  padding: 10px 16px;
  border-radius: 18px;
  line-height: 1.6;
  word-break: break-word;
}

.bubble--user {
  background: linear-gradient(135deg, #7c3aed 0%, #6366f1 100%);
  color: #ffffff;
  border-bottom-right-radius: 6px;
  box-shadow: 0 2px 8px rgba(124, 58, 237, 0.25);
}

.bubble--error {
  background-color: #fef2f2;
  color: #dc2626;
  border: 1px solid #fecaca;
  border-bottom-left-radius: 6px;
  box-shadow: 0 1px 4px rgba(220, 38, 38, 0.08);
}

/* ========== 时间戳 ========== */
.timestamp {
  font-size: 11px;
  color: #b0b5bf;
  margin-top: 4px;
  padding: 0 4px;
}

/* ========== 工具卡片列表 ========== */
.tool-calls-list {
  width: 100%;
  margin-bottom: 6px;
}

/* ========== 代理回复文本 ========== */
.assistant-content {
  color: #1f2937;
  line-height: 1.7;
  font-size: 14px;
  padding: 8px 14px;
  background: #ffffff;
  border-radius: 14px;
  border-bottom-left-radius: 6px;
  border-left: 3px solid #7c3aed;
  box-shadow: 0 1px 4px rgba(0, 0, 0, 0.04);
}

/* ========== 流式等待动画 ========== */
.streaming-hint {
  display: flex;
  align-items: center;
  gap: 5px;
  padding: 10px 14px;
  background: #ffffff;
  border-radius: 14px;
  border-left: 3px solid #a78bfa;
  box-shadow: 0 1px 4px rgba(0, 0, 0, 0.04);
  width: fit-content;
}

.streaming-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: linear-gradient(135deg, #7c3aed, #6366f1);
  animation: bounce 1.4s infinite ease-in-out both;
}

.streaming-dot:nth-child(2) {
  animation-delay: 0.16s;
}

.streaming-dot:nth-child(3) {
  animation-delay: 0.32s;
}

@keyframes bounce {
  0%, 80%, 100% {
    transform: scale(0.5);
    opacity: 0.3;
  }
  40% {
    transform: scale(1);
    opacity: 1;
  }
}

/* ========== Markdown 内容样式 ========== */
.markdown-body :deep(p) {
  margin: 0 0 8px;
}

.markdown-body :deep(p:last-child) {
  margin-bottom: 0;
}

.markdown-body :deep(pre) {
  background-color: #f8f7fa;
  padding: 14px 16px;
  border-radius: 10px;
  overflow-x: auto;
  margin: 10px 0;
  font-size: 13px;
  border: 1px solid #f0f0f2;
}

.markdown-body :deep(code) {
  font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', Menlo, monospace;
  font-size: 13px;
}

.markdown-body :deep(p code) {
  background-color: #f3f0ff;
  color: #6d28d9;
  padding: 2px 7px;
  border-radius: 5px;
  font-size: 12.5px;
}

.markdown-body :deep(ul),
.markdown-body :deep(ol) {
  padding-left: 20px;
  margin: 8px 0;
}

.markdown-body :deep(table) {
  border-collapse: collapse;
  margin: 10px 0;
  width: 100%;
  border-radius: 8px;
  overflow: hidden;
}

.markdown-body :deep(th),
.markdown-body :deep(td) {
  border: 1px solid #e5e7eb;
  padding: 8px 12px;
  text-align: left;
}

.markdown-body :deep(th) {
  background: linear-gradient(135deg, #f5f3ff 0%, #eef2ff 100%);
  font-weight: 600;
  color: #4b5563;
}

.markdown-body :deep(blockquote) {
  border-left: 3px solid #a78bfa;
  padding-left: 14px;
  color: #6b7280;
  margin: 10px 0;
  background: #faf8ff;
  padding: 8px 14px;
  border-radius: 0 8px 8px 0;
}
</style>
