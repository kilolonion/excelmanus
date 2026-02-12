<script setup lang="ts">
import { ref } from 'vue'

defineProps<{
  content: string
  streaming?: boolean
}>()

const expanded = ref(false)
</script>

<template>
  <div class="thinking-block" @click="expanded = !expanded">
    <div class="thinking-header">
      <span class="thinking-arrow" :class="{ 'thinking-arrow--open': expanded }">
        <svg class="thinking-arrow-svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
          <polyline points="9 18 15 12 9 6"/>
        </svg>
      </span>
      <span v-if="streaming" class="thinking-label thinking-label-streaming">思考中...</span>
      <span v-else class="thinking-label">思考过程</span>
    </div>
    <div v-if="expanded" class="thinking-content">
      {{ content }}
    </div>
  </div>
</template>

<style scoped>
.thinking-block {
  margin-bottom: 8px;
  cursor: pointer;
  background: #ffffff;
  border: 1px solid #f0f1f3;
  border-radius: 12px;
  padding: 2px;
  transition: all 0.2s ease;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.02);
}

.thinking-block:hover {
  border-color: #e0e7ff;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.04);
}

.thinking-header {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
}

.thinking-arrow {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 20px;
  height: 20px;
  border-radius: 5px;
  background: #f5f3ff;
  transition: all 0.2s ease;
  color: #7c3aed;
}

.thinking-arrow--open {
  transform: rotate(90deg);
  background: #ede9fe;
}

.thinking-arrow-svg {
  width: 14px;
  height: 14px;
}

.thinking-label {
  font-size: 13px;
  font-weight: 500;
  color: #6b7280;
}

/* 流式思考时的脉冲动画 */
.thinking-label-streaming {
  background: linear-gradient(90deg, #7c3aed, #6366f1, #a78bfa, #7c3aed);
  background-size: 200% auto;
  -webkit-background-clip: text;
  background-clip: text;
  -webkit-text-fill-color: transparent;
  animation: shimmer 2s linear infinite;
}

@keyframes shimmer {
  from { background-position: 0% center; }
  to { background-position: 200% center; }
}

.thinking-content {
  margin: 0 12px 10px 40px;
  padding: 10px 14px;
  font-size: 13px;
  line-height: 1.6;
  color: #6b7280;
  white-space: pre-wrap;
  word-break: break-word;
  background: linear-gradient(135deg, #f9f8fc 0%, #f5f3ff 100%);
  border-radius: 8px;
  border-left: 3px solid #c4b5fd;
  animation: fadeIn 0.25s ease;
}
</style>
