<script setup lang="ts">
import { ref, computed } from 'vue'
import type { ToolCallInfo } from '../composables/useChat'

const props = defineProps<{
  toolCall: ToolCallInfo
}>()

const expanded = ref(false)

/** 工具显示名称（将 snake_case 转为可读文本） */
const displayName = computed(() => {
  const name = props.toolCall.tool_name
  return name.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())
})

/** 提取参数中的关键值作为 badge 展示 */
const paramBadges = computed(() => {
  const args = props.toolCall.arguments
  if (!args || Object.keys(args).length === 0) return []
  return Object.entries(args).map(([k, v]) => {
    const val = typeof v === 'string' ? v : JSON.stringify(v)
    // 截断过长的值
    const display = val.length > 40 ? val.slice(0, 37) + '...' : val
    return { key: k, value: display }
  })
})

/** 结果摘要（截断到 200 字符） */
const resultSummary = computed(() => {
  const text = props.toolCall.error || props.toolCall.result || ''
  if (text.length <= 200) return text
  return text.slice(0, 200) + '...'
})
</script>

<template>
  <div class="tool-card" :class="`tool-card--${toolCall.status}`" @click="expanded = !expanded">
    <!-- 主行：图标 + 工具名 + 参数 badges -->
    <div class="tool-main">
      <span class="tool-icon" :class="`tool-icon--${toolCall.status}`">
        <svg v-if="toolCall.status === 'running'" class="tool-icon-svg spin" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <path d="M12 2v4M12 18v4M4.93 4.93l2.83 2.83M16.24 16.24l2.83 2.83M2 12h4M18 12h4M4.93 19.07l2.83-2.83M16.24 7.76l2.83-2.83"/>
        </svg>
        <svg v-else-if="toolCall.status === 'error'" class="tool-icon-svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/>
        </svg>
        <svg v-else class="tool-icon-svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <polyline points="20 6 9 17 4 12"/>
        </svg>
      </span>
      <span class="tool-name">{{ displayName }}</span>
      <span
        v-for="(badge, i) in paramBadges"
        :key="i"
        class="tool-badge"
      >
        <span class="tool-badge-dot">&#x2756;</span>
        {{ badge.value }}
      </span>
    </div>
    <!-- 展开详情：结果/错误 -->
    <div v-if="expanded && toolCall.status !== 'running' && resultSummary" class="tool-detail">
      {{ resultSummary }}
    </div>
  </div>
</template>

<style scoped>
.tool-card {
  margin-bottom: 8px;
  border-radius: 12px;
  background-color: #ffffff;
  overflow: hidden;
  font-size: 13px;
  cursor: pointer;
  transition: all 0.2s ease;
  border: 1px solid #f0f1f3;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.03);
  position: relative;
}

/* 左侧彩色竖条 */
.tool-card::before {
  content: '';
  position: absolute;
  left: 0;
  top: 0;
  bottom: 0;
  width: 3px;
  border-radius: 3px 0 0 3px;
  background: #a78bfa;
  transition: background 0.2s;
}

.tool-card--success::before {
  background: linear-gradient(180deg, #22c55e, #16a34a);
}

.tool-card--running::before {
  background: linear-gradient(180deg, #60a5fa, #3b82f6);
}

.tool-card--error::before {
  background: linear-gradient(180deg, #f87171, #dc2626);
}

.tool-card:hover {
  border-color: #e0e7ff;
  box-shadow: 0 3px 10px rgba(0, 0, 0, 0.06);
  transform: translateY(-1px);
}

.tool-card--error {
  background-color: #fffbfb;
  border-color: #fde8e8;
}

.tool-card--error:hover {
  border-color: #fecaca;
  box-shadow: 0 3px 10px rgba(220, 38, 38, 0.06);
}

.tool-main {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 14px 10px 16px;
  flex-wrap: wrap;
}

/* 圆形图标容器 */
.tool-icon {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 26px;
  height: 26px;
  border-radius: 8px;
  flex-shrink: 0;
}

.tool-icon--success {
  background-color: #dcfce7;
  color: #16a34a;
}

.tool-icon--running {
  background-color: #dbeafe;
  color: #2563eb;
}

.tool-icon--error {
  background-color: #fee2e2;
  color: #dc2626;
}

.tool-icon-svg {
  width: 14px;
  height: 14px;
}

/* 工具名称 */
.tool-name {
  font-weight: 600;
  color: #1f2937;
  white-space: nowrap;
  font-size: 13px;
}

/* 参数 pill badge */
.tool-badge {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  padding: 2px 10px;
  background-color: #f5f3ff;
  border: 1px solid #ede9fe;
  border-radius: 12px;
  font-size: 12px;
  color: #5b21b6;
  white-space: nowrap;
  max-width: 200px;
  overflow: hidden;
  text-overflow: ellipsis;
}

.tool-badge-dot {
  color: #8b5cf6;
  font-size: 10px;
  flex-shrink: 0;
}

/* 展开详情区域 */
.tool-detail {
  padding: 0 14px 12px 50px;
  font-size: 12px;
  line-height: 1.6;
  color: #6b7280;
  word-break: break-word;
  border-top: 1px solid #f5f5f6;
  margin-top: 2px;
  padding-top: 8px;
}

/* loading 旋转动画 */
.spin {
  animation: spin 1.2s linear infinite;
}

@keyframes spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}
</style>
