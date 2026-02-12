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

// 监听消息变化，自动滚动到最新消息（深度监听以支持流式更新）
watch(
  () => props.messages,
  () => {
    nextTick(scrollToBottom)
  },
  { deep: true }
)

// loading 变化时也滚动
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
      <div class="welcome-card">
        <div class="welcome-icon">
          <svg width="48" height="48" viewBox="0 0 24 24" fill="none">
            <rect x="2" y="2" width="20" height="20" rx="4" stroke="#7c3aed" stroke-width="1.5" fill="#ede9fe"/>
            <path d="M7 7h4v4H7V7zm6 0h4v4h-4V7zm-6 6h4v4H7v-4zm6 0h4v4h-4v-4z" fill="#c4b5fd"/>
            <text x="12" y="15.5" text-anchor="middle" font-size="8" font-weight="800" fill="#7c3aed">E</text>
          </svg>
        </div>
        <h2 class="welcome-title">你好，欢迎使用 ExcelManus</h2>
        <p class="welcome-desc">用自然语言描述你的 Excel 任务，我来帮你完成</p>
        <div class="feature-cards">
          <div class="feature-card">
            <div class="feature-card-icon">
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#7c3aed" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/>
              </svg>
            </div>
            <span class="feature-card-text">数据分析</span>
          </div>
          <div class="feature-card">
            <div class="feature-card-icon">
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#6366f1" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <line x1="18" y1="20" x2="18" y2="10"/><line x1="12" y1="20" x2="12" y2="4"/><line x1="6" y1="20" x2="6" y2="14"/>
              </svg>
            </div>
            <span class="feature-card-text">图表生成</span>
          </div>
          <div class="feature-card">
            <div class="feature-card-icon">
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#8b5cf6" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <rect x="3" y="3" width="18" height="18" rx="2" ry="2"/><line x1="3" y1="9" x2="21" y2="9"/><line x1="9" y1="21" x2="9" y2="9"/>
              </svg>
            </div>
            <span class="feature-card-text">格式处理</span>
          </div>
        </div>
      </div>
    </div>

    <!-- 消息列表 -->
    <template v-else>
      <MessageBubble
        v-for="msg in messages"
        :key="msg.id"
        :message="msg"
      />
    </template>

  </div>
</template>

<style scoped>
/* 对话面板：可滚动区域 */
.chat-panel {
  flex: 1;
  overflow-y: auto;
  padding: 24px calc(max(20px, (100% - 860px) / 2));
  display: flex;
  flex-direction: column;
  background-color: var(--color-bg-chat, #f7f8fc);
}

/* 空状态 */
.empty-state {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  animation: fadeIn 0.5s ease;
}

.welcome-card {
  display: flex;
  flex-direction: column;
  align-items: center;
  text-align: center;
  max-width: 400px;
  padding: 32px 24px;
}

.welcome-icon {
  width: 72px;
  height: 72px;
  border-radius: 20px;
  background: linear-gradient(135deg, #ede9fe 0%, #e0e7ff 100%);
  display: flex;
  align-items: center;
  justify-content: center;
  margin-bottom: 20px;
  box-shadow: 0 4px 16px rgba(124, 58, 237, 0.1);
}

.welcome-title {
  font-size: 20px;
  font-weight: 700;
  color: #1f2937;
  margin-bottom: 8px;
}

.welcome-desc {
  font-size: 14px;
  color: #9ca3af;
  margin-bottom: 28px;
  line-height: 1.5;
}

.feature-cards {
  display: flex;
  gap: 12px;
  flex-wrap: wrap;
  justify-content: center;
}

.feature-card {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
  padding: 16px 20px;
  background: #ffffff;
  border: 1px solid #f0f1f3;
  border-radius: 14px;
  min-width: 100px;
  cursor: default;
  transition: all 0.2s ease;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.03);
}

.feature-card:hover {
  border-color: #e0e7ff;
  box-shadow: 0 4px 12px rgba(124, 58, 237, 0.08);
  transform: translateY(-2px);
}

.feature-card-icon {
  width: 40px;
  height: 40px;
  border-radius: 10px;
  background: #f5f3ff;
  display: flex;
  align-items: center;
  justify-content: center;
}

.feature-card-text {
  font-size: 12px;
  font-weight: 600;
  color: #4b5563;
}
</style>
