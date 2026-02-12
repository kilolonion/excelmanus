<script setup lang="ts">
// 顶部标题栏组件 — 展示应用名称、模型名和新建会话按钮
import { ref, onMounted } from 'vue'
import { checkHealth } from '../api'

defineEmits<{
  'new-session': []
}>()

const modelName = ref('')

onMounted(async () => {
  try {
    const health = await checkHealth()
    modelName.value = health.model
  } catch {
    // 后端未启动时静默忽略
  }
})
</script>

<template>
  <header class="app-header">
    <div class="header-content">
      <div class="header-brand">
        <div class="header-logo">
          <svg width="28" height="28" viewBox="0 0 24 24" fill="none">
            <rect x="2" y="2" width="20" height="20" rx="4" fill="rgba(255,255,255,0.2)"/>
            <path d="M7 7h4v4H7V7zm6 0h4v4h-4V7zm-6 6h4v4H7v-4zm6 0h4v4h-4v-4z" fill="rgba(255,255,255,0.5)"/>
            <text x="12" y="15.5" text-anchor="middle" font-size="8" font-weight="800" fill="white">E</text>
          </svg>
        </div>
        <div class="header-info">
          <div class="title-row">
            <h1 class="app-title">ExcelManus</h1>
            <span v-if="modelName" class="model-badge">{{ modelName }}</span>
          </div>
          <p class="app-desc">智能 Excel 代理 · 自然语言驱动</p>
        </div>
      </div>
      <button class="new-session-btn" @click="$emit('new-session')">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round">
          <line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/>
        </svg>
        新建会话
      </button>
    </div>
  </header>
</template>

<style scoped>
.app-header {
  flex-shrink: 0;
  padding: 14px calc(max(24px, (100% - 860px) / 2));
  background: linear-gradient(135deg, #7c3aed 0%, #6366f1 60%, #818cf8 100%);
  border-bottom: none;
  position: relative;
  overflow: hidden;
}

/* 微妙的背景装饰光斑 */
.app-header::before {
  content: '';
  position: absolute;
  top: -50%;
  right: -20%;
  width: 200px;
  height: 200px;
  border-radius: 50%;
  background: rgba(255, 255, 255, 0.06);
  pointer-events: none;
}

.header-content {
  display: flex;
  align-items: center;
  justify-content: space-between;
  position: relative;
  z-index: 1;
}

.header-brand {
  display: flex;
  align-items: center;
  gap: 12px;
  min-width: 0;
}

.header-logo {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 40px;
  height: 40px;
  border-radius: 10px;
  background: rgba(255, 255, 255, 0.15);
  backdrop-filter: blur(8px);
  -webkit-backdrop-filter: blur(8px);
  flex-shrink: 0;
}

.header-info {
  min-width: 0;
}

.title-row {
  display: flex;
  align-items: center;
  gap: 8px;
}

.app-title {
  font-size: 18px;
  font-weight: 700;
  color: #ffffff;
  letter-spacing: 0.3px;
  margin-bottom: 1px;
}

.model-badge {
  display: inline-flex;
  align-items: center;
  padding: 2px 9px;
  font-size: 11px;
  font-weight: 600;
  color: rgba(255, 255, 255, 0.85);
  background: rgba(255, 255, 255, 0.15);
  border: 1px solid rgba(255, 255, 255, 0.2);
  border-radius: 6px;
  letter-spacing: 0.2px;
  white-space: nowrap;
  backdrop-filter: blur(4px);
  -webkit-backdrop-filter: blur(4px);
}

.app-desc {
  font-size: 12px;
  color: rgba(255, 255, 255, 0.7);
}

.new-session-btn {
  flex-shrink: 0;
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 7px 16px;
  font-size: 13px;
  font-weight: 500;
  color: rgba(255, 255, 255, 0.9);
  background: rgba(255, 255, 255, 0.15);
  backdrop-filter: blur(8px);
  -webkit-backdrop-filter: blur(8px);
  border: 1px solid rgba(255, 255, 255, 0.2);
  border-radius: 10px;
  cursor: pointer;
  transition: all 0.2s ease;
}

.new-session-btn:hover {
  color: #ffffff;
  background: rgba(255, 255, 255, 0.25);
  border-color: rgba(255, 255, 255, 0.35);
  transform: translateY(-1px);
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15);
}

.new-session-btn:active {
  transform: translateY(0);
}
</style>
