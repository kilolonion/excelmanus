import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// Vite 配置：Vue 3 + API 代理
export default defineConfig({
  plugins: [vue()],
  server: {
    // 将 /api 请求代理到后端服务
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
})
