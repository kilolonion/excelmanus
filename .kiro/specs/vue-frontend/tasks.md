# 实现计划：Vue 前端界面

## 概述

基于设计文档，将 ExcelManus 的 Vue 3 前端拆分为增量式编码任务。每个任务构建在前一个任务之上，最终完成完整的前端应用并与后端集成。

## 任务

- [x] 1. 初始化 Vue 3 + Vite + TypeScript 项目
  - [x] 1.1 在 `frontend/` 目录下初始化 Vue 3 + Vite + TypeScript 项目
    - 创建 `package.json`、`vite.config.ts`、`tsconfig.json` 等配置文件
    - 配置 Vite API 代理，将 `/api` 请求转发到 `http://localhost:8000`
    - 安装依赖：`vue`、`markdown-it`、`fast-check`（dev）、`vitest`（dev）、`@vue/test-utils`（dev）、`jsdom`（dev）
    - _Requirements: 1.1, 1.2, 1.3, 1.4_

  - [x] 1.2 创建基础入口文件和全局样式
    - 创建 `src/main.ts`、`src/App.vue`、`index.html`
    - 定义 CSS 变量（主色 #1677ff、背景 #f5f5f5、白色卡片）和全局样式
    - 实现响应式基础布局（居中对话式）
    - _Requirements: 2.1, 2.2, 2.3, 2.4_

- [x] 2. 实现 API 客户端和核心 Composables
  - [x] 2.1 创建 API 客户端 (`src/api.ts`)
    - 实现 `sendMessage()`、`deleteSession()`、`checkHealth()` 函数
    - 定义 `ChatRequest`、`ChatResponse`、`HealthResponse`、`ApiError` 类型
    - 实现 HTTP 错误处理逻辑（区分 429、409 和通用错误）
    - _Requirements: 3.1, 5.1, 5.2, 5.3, 5.4_

  - [x] 2.2 创建会话管理 Composable (`src/composables/useSession.ts`)
    - 实现 `useSession()` composable，管理 session_id 状态
    - 实现 localStorage 持久化和恢复逻辑
    - _Requirements: 4.1, 4.3_

  - [x] 2.3 创建对话管理 Composable (`src/composables/useChat.ts`)
    - 实现 `useChat()` composable，管理消息列表和 loading 状态
    - 实现 `sendMessage()`：验证输入、添加用户消息、调用 API、添加回复
    - 实现 `clearMessages()` 和 `retryLast()` 方法
    - 空白消息验证（trim 后为空则阻止发送）
    - _Requirements: 3.1, 3.2, 3.3, 3.5, 4.1, 4.2, 5.1_

  - [x] 2.4 编写 useSession 属性测试
    - **Property 7: Session ID 持久化 round-trip**
    - **Validates: Requirements 4.3**

  - [x] 2.5 编写 useChat 属性测试
    - **Property 2: 空白消息拒绝**
    - **Validates: Requirements 3.3**

  - [x] 2.6 编写 useChat 对话完整性属性测试
    - **Property 1: 对话完整性**
    - **Validates: Requirements 3.1, 3.2**

  - [x] 2.7 编写 useChat loading 状态属性测试
    - **Property 3: Loading 状态禁用发送**
    - **Validates: Requirements 3.5**

  - [x] 2.8 编写 API 错误处理属性测试
    - **Property 8: HTTP 错误消息生成**
    - **Validates: Requirements 5.1**

- [x] 3. 检查点 - 确保核心逻辑测试通过
  - 确保所有测试通过，如有问题请向用户确认。

- [x] 4. 实现 UI 组件
  - [x] 4.1 创建 AppHeader 组件 (`src/components/AppHeader.vue`)
    - 展示应用标题"ExcelManus"和简要说明
    - 包含"新建会话"按钮，emit `new-session` 事件
    - _Requirements: 2.1, 4.2_

  - [x] 4.2 创建 MessageBubble 组件 (`src/components/MessageBubble.vue`)
    - 根据 message.role 渲染不同样式（用户消息右对齐蓝色、代理回复左对齐白色、错误消息红色）
    - 使用 markdown-it 渲染代理回复中的 Markdown 内容
    - 显示时间戳
    - _Requirements: 3.2, 3.6_

  - [x] 4.3 编写 Markdown 渲染属性测试
    - **Property 4: Markdown 渲染正确性**
    - **Validates: Requirements 3.6**

  - [x] 4.4 创建 ChatPanel 组件 (`src/components/ChatPanel.vue`)
    - 渲染消息列表，使用 MessageBubble 组件
    - 实现自动滚动到最新消息（watch messages 变化）
    - 显示 loading 指示器（打字动画）
    - 空状态展示欢迎提示
    - _Requirements: 3.2, 3.4, 3.5_

  - [x] 4.5 创建 InputBar 组件 (`src/components/InputBar.vue`)
    - 输入框 + 发送按钮布局
    - Enter 键发送、Shift+Enter 换行
    - disabled 状态下禁用输入和按钮
    - _Requirements: 3.1, 3.3, 3.5_

- [x] 5. 组装与集成
  - [x] 5.1 在 App.vue 中组装所有组件
    - 引入 useChat、useSession composables
    - 连接 AppHeader、ChatPanel、InputBar 组件
    - 实现新建会话流程（调用 deleteSession → clearMessages → clearSession）
    - _Requirements: 3.1, 4.1, 4.2_

  - [x] 5.2 编写 Session ID 复用属性测试
    - **Property 5: Session ID 复用**
    - **Validates: Requirements 4.1**

  - [x] 5.3 编写新建会话属性测试
    - **Property 6: 新建会话清空状态**
    - **Validates: Requirements 4.2**

- [x] 6. 后端 CORS 配置
  - [x] 6.1 在 FastAPI 后端添加 CORS 中间件
    - 在 `excelmanus/api.py` 中添加 `CORSMiddleware`
    - 允许来源 `http://localhost:5173`，允许方法 GET/POST/DELETE，允许 Content-Type 头
    - _Requirements: 6.1, 6.2_

- [x] 7. 最终检查点 - 确保所有测试通过
  - 确保所有测试通过，如有问题请向用户确认。

## 说明

- 标记 `*` 的任务为可选测试任务，可跳过以加速 MVP 开发
- 每个任务引用了具体的需求编号以确保可追溯性
- 属性测试验证通用正确性规则，单元测试验证具体例子和边界情况
- 检查点确保增量验证
