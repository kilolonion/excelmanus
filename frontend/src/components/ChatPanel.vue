<script setup lang="ts">
import { ref, watch, nextTick } from 'vue'
import MessageBubble from './MessageBubble.vue'
import type { Message } from '../composables/useChat'

// 组件 Props
const props = defineProps<{
  messages: Message[]
  loading: boolean
}>()

// 滚动容器引用
const scrollRef = ref<HTMLElement | null>(null)

/** 自动滚动到底部 */
function scrollToBottom() {
  if (scrollRef.value) {
    scrollRef.value.scrollTop = scrollRef.value.scrollHeight
  }
}

// 监听消息变化，自动滚动到最新消息
watch(
  () => props.messages.length,
  () => {
    nextTick(scrollToBottom)
  }
)

// loading 变化时也滚动（显示打字指示器）
watch(
  () => props.loading,
  (val) => {
    if (val) nextTick(scrollToBottom)
  }
)
</script>

<template>
  <div class="chat-panel" ref="scrollRef">
    <!-- 空状态欢迎提示 -->
    <div v-if="messages.length === 0 && !loading" class="empty-state">
      <p class="welcome-text">👋 你好！请在下方输入框中描述你的 Excel 任务。</p>
    </div>

    <!-- 消息列表 -->
    <template v-else>
      <MessageBubble
        v-for="msg in messages"
        :key="msg.id"
        :message="msg"
      />
    </template>

    <!-- Loading 打字指示器 -->
    <div v-if="loading" class="typing-indicator">
      <span class="dot"></span>
      <span class="dot"></span>
      <span class="dot"></span>
    </div>
  </div>
</template>

<style scoped>
/* 对话面板：可滚动区域 */
.chat-panel {
  flex: 1;
  overflow-y: auto;
  padding: 16px;
  display: flex;
  flex-direction: column;
}

/* 空状态 */
.empty-state {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
}

.welcome-text {
  color: var(--color-text-secondary);
  font-size: 16px;
  text-align: center;
}

/* 打字指示器 */
.typing-indicator {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 10px 14px;
  background-color: var(--color-card);
  border-radius: var(--radius);
  box-shadow: var(--shadow);
  align-self: flex-start;
  margin-bottom: 16px;
}

.dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background-color: var(--color-text-secondary);
  animation: typing 1.4s infinite ease-in-out both;
}

.dot:nth-child(2) {
  animation-delay: 0.2s;
}

.dot:nth-child(3) {
  animation-delay: 0.4s;
}

@keyframes typing {
  0%, 80%, 100% {
    transform: scale(0.6);
    opacity: 0.4;
  }
  40% {
    transform: scale(1);
    opacity: 1;
  }
}
</style>
