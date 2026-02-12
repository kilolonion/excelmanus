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
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
        <line x1="12" y1="19" x2="12" y2="5"/><polyline points="5 12 12 5 19 12"/>
      </svg>
    </button>
  </div>
</template>

<style scoped>
/* 输入栏容器 */
.input-bar {
  flex-shrink: 0;
  display: flex;
  align-items: flex-end;
  gap: 10px;
  padding: 14px calc(max(20px, (100% - 860px) / 2)) 18px;
  background: rgba(255, 255, 255, 0.75);
  backdrop-filter: blur(16px);
  -webkit-backdrop-filter: blur(16px);
  border-top: 1px solid rgba(243, 244, 246, 0.6);
}

/* 文本输入框 */
.input-textarea {
  flex: 1;
  min-height: 44px;
  max-height: 120px;
  padding: 11px 16px;
  font-size: 14px;
  font-family: inherit;
  line-height: 1.5;
  color: #1f2937;
  background-color: #ffffff;
  border: 1.5px solid #e5e7eb;
  border-radius: 14px;
  resize: none;
  outline: none;
  transition: border-color 0.2s, box-shadow 0.2s, background-color 0.2s;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.03);
}

.input-textarea:focus {
  border-color: #a78bfa;
  box-shadow: 0 0 0 3px rgba(124, 58, 237, 0.08), 0 2px 6px rgba(124, 58, 237, 0.06);
  background-color: #fefeff;
}

.input-textarea::placeholder {
  color: #b0b5bf;
}

.input-textarea:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

/* 发送按钮 */
.send-btn {
  flex-shrink: 0;
  height: 44px;
  width: 44px;
  padding: 0;
  font-size: 14px;
  color: #ffffff;
  background: linear-gradient(135deg, #7c3aed 0%, #6366f1 100%);
  border: none;
  border-radius: 14px;
  cursor: pointer;
  transition: all 0.2s ease;
  display: flex;
  align-items: center;
  justify-content: center;
  box-shadow: 0 2px 8px rgba(124, 58, 237, 0.3);
}

.send-btn:hover:not(:disabled) {
  background: linear-gradient(135deg, #6d28d9 0%, #4f46e5 100%);
  transform: scale(1.05);
  box-shadow: 0 4px 14px rgba(124, 58, 237, 0.4);
}

.send-btn:active:not(:disabled) {
  transform: scale(0.97);
}

.send-btn:disabled {
  opacity: 0.35;
  cursor: not-allowed;
  box-shadow: none;
}
</style>
