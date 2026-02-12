<script setup lang="ts">
// 顶部标题栏组件 — 展示应用名称和新建会话按钮
defineEmits<{
  'new-session': []
}>()
</script>

<template>
  <header class="app-header">
    <div class="header-content">
      <div class="header-info">
        <h1 class="app-title">ExcelManus</h1>
        <p class="app-desc">智能 Excel 代理 — 用自然语言处理 Excel 任务</p>
      </div>
      <button class="new-session-btn" @click="$emit('new-session')">
        新建会话
      </button>
    </div>
  </header>
</template>

<style scoped>
.app-header {
  flex-shrink: 0;
  padding: 12px 20px;
  background-color: var(--color-card);
  border-bottom: 1px solid var(--color-border);
  box-shadow: var(--shadow);
}

.header-content {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.header-info {
  min-width: 0;
}

.app-title {
  font-size: 20px;
  font-weight: 600;
  color: var(--color-primary);
  margin-bottom: 2px;
}

.app-desc {
  font-size: 13px;
  color: var(--color-text-secondary);
}

.new-session-btn {
  flex-shrink: 0;
  padding: 6px 16px;
  font-size: 13px;
  color: var(--color-primary);
  background-color: transparent;
  border: 1px solid var(--color-primary);
  border-radius: var(--radius);
  cursor: pointer;
  transition: all 0.2s;
}

.new-session-btn:hover {
  color: var(--color-user-text);
  background-color: var(--color-primary);
}
</style>
