<script setup lang="ts">
import { ref } from 'vue'

// 输入栏组件 — 输入框 + 发送按钮
const props = defineProps<{
  disabled: boolean
}>()

const emit = defineEmits<{
  send: [content: string]
}>()

// 输入内容
const input = ref('')

/** 发送消息：trim 后非空才 emit */
function handleSend() {
  if (props.disabled) return
  const content = input.value.trim()
  if (!content) return
  emit('send', content)
  input.value = ''
}

/** 键盘事件：Enter 发送，Shift+Enter 换行 */
function handleKeydown(e: KeyboardEvent) {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault()
    handleSend()
  }
}
</script>

<template>
  <div class="input-bar">
    <textarea
      v-model="input"
      class="input-textarea"
      placeholder="输入你的 Excel 任务..."
      :disabled="disabled"
      rows="1"
      @keydown="handleKeydown"
    />
    <button
      class="send-btn"
      :disabled="disabled"
      @click="handleSend"
    >
      发送
    </button>
  </div>
</template>

<style scoped>
/* 输入栏容器 */
.input-bar {
  flex-shrink: 0;
  display: flex;
  align-items: flex-end;
  gap: 8px;
  padding: 12px 20px;
  background-color: var(--color-card);
  border-top: 1px solid var(--color-border);
}

/* 文本输入框 */
.input-textarea {
  flex: 1;
  min-height: 40px;
  max-height: 120px;
  padding: 8px 12px;
  font-size: 14px;
  font-family: inherit;
  line-height: 1.6;
  color: var(--color-text);
  background-color: var(--color-bg);
  border: 1px solid var(--color-border);
  border-radius: var(--radius);
  resize: none;
  outline: none;
  transition: border-color 0.2s;
}

.input-textarea:focus {
  border-color: var(--color-primary);
}

.input-textarea:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

/* 发送按钮 */
.send-btn {
  flex-shrink: 0;
  height: 40px;
  padding: 0 20px;
  font-size: 14px;
  color: var(--color-user-text);
  background-color: var(--color-primary);
  border: none;
  border-radius: var(--radius);
  cursor: pointer;
  transition: background-color 0.2s;
}

.send-btn:hover:not(:disabled) {
  background-color: var(--color-primary-hover);
}

.send-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
</style>
