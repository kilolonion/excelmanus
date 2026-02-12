<script setup lang="ts">
// 根组件 — 组装所有子组件，连接 composables 与 UI
import { deleteSession } from './api'
import { useChat } from './composables/useChat'
import { useSession } from './composables/useSession'
import AppHeader from './components/AppHeader.vue'
import ChatPanel from './components/ChatPanel.vue'
import InputBar from './components/InputBar.vue'

const { messages, loading, sendMessage, clearMessages } = useChat()
const { sessionId, clearSession } = useSession()

/** 新建会话：删除后端会话 → 清空消息 → 清除本地 session */
async function handleNewSession() {
  if (sessionId.value) {
    try {
      await deleteSession(sessionId.value)
    } catch {
      // 会话可能已不存在，忽略错误
    }
  }
  clearMessages()
  clearSession()
}
</script>

<template>
  <div class="app-layout">
    <AppHeader @new-session="handleNewSession" />
    <ChatPanel :messages="messages" :loading="loading" />
    <InputBar :disabled="loading" @send="sendMessage" />
  </div>
</template>

<style scoped>
/* 整体布局：flex 纵向排列，撑满容器 */
.app-layout {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
}
</style>
