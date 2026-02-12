# 需求文档

## 简介

为 ExcelManus（基于大语言模型的 Excel 智能代理框架）创建一个基于 Vue 框架的前端界面。用户通过该界面以自然语言描述 Excel 任务，系统调用后端 API 完成操作并实时展示执行过程与结果。前端需与现有的 FastAPI 后端（`/api/v1/chat`、`/api/v1/sessions/{session_id}`、`/api/v1/health`）集成。

## 术语表

- **Frontend**：基于 Vue 框架构建的浏览器端单页应用
- **ChatPanel**：对话面板组件，展示用户消息与代理回复的主区域
- **InputBar**：消息输入栏组件，用户在此输入自然语言指令
- **SessionManager**：前端会话管理模块，负责维护 session_id 与后端通信
- **Backend**：现有的 FastAPI REST API 服务（`/api/v1/*` 端点）
- **ToolEvent**：后端代理执行工具调用时产生的结构化事件（工具开始、结束、思考过程等）

## 需求

### 需求 1：项目初始化与构建配置

**用户故事：** 作为开发者，我希望使用 Vue 框架初始化前端项目并配置构建工具，以便能够高效开发和构建前端应用。

#### 验收标准

1. THE Frontend SHALL 使用 Vue 3 + Vite 进行项目初始化，项目目录位于仓库根目录下的 `frontend/` 文件夹
2. THE Frontend SHALL 使用 TypeScript 作为开发语言
3. THE Frontend SHALL 配置 API 代理，将 `/api` 请求转发到后端服务（默认 `http://localhost:8000`）
4. THE Frontend SHALL 提供 `npm run dev` 和 `npm run build` 命令分别用于开发和生产构建

### 需求 2：整体布局与视觉设计

**用户故事：** 作为用户，我希望看到一个美观、现代的界面，以便获得良好的使用体验。

#### 验收标准

1. THE Frontend SHALL 采用居中对话式布局，页面顶部展示应用标题和简要说明
2. THE Frontend SHALL 使用统一的配色方案，主色调为科技蓝（#1677ff），背景为浅灰色（#f5f5f5），卡片为白色
3. THE Frontend SHALL 支持响应式设计，在桌面端（≥768px）和移动端（<768px）均可正常使用
4. WHEN 页面宽度小于 768px 时，THE Frontend SHALL 自动调整布局使对话区域占满屏幕宽度

### 需求 3：对话交互

**用户故事：** 作为用户，我希望通过自然语言输入 Excel 任务指令并查看代理的回复，以便完成数据处理工作。

#### 验收标准

1. WHEN 用户在 InputBar 中输入消息并按下 Enter 键或点击发送按钮，THE ChatPanel SHALL 立即显示用户消息并向 Backend 发送请求
2. WHEN Backend 返回回复，THE ChatPanel SHALL 在对话区域展示代理回复，并以不同的视觉样式区分用户消息和代理回复
3. WHEN 用户尝试发送空白消息（仅包含空格、换行等空白字符），THE InputBar SHALL 阻止发送并保持当前状态
4. WHEN 新消息被添加到对话区域，THE ChatPanel SHALL 自动滚动到最新消息位置
5. WHILE Backend 正在处理请求，THE InputBar SHALL 禁用发送功能并显示加载指示器
6. WHEN 代理回复包含 Markdown 格式内容，THE ChatPanel SHALL 正确渲染 Markdown（包括代码块、表格、列表等）

### 需求 4：会话管理

**用户故事：** 作为用户，我希望管理对话会话，以便开始新的任务或清理历史记录。

#### 验收标准

1. THE SessionManager SHALL 在首次对话时自动从 Backend 获取 session_id 并在后续请求中复用
2. WHEN 用户点击"新建会话"按钮，THE SessionManager SHALL 调用 Backend 删除当前会话，清空对话历史，并在下次对话时创建新会话
3. THE SessionManager SHALL 将当前 session_id 持久化到浏览器 localStorage，以便页面刷新后恢复会话

### 需求 5：错误处理与状态反馈

**用户故事：** 作为用户，我希望在出现错误时获得清晰的提示信息，以便了解问题并采取行动。

#### 验收标准

1. IF Backend 返回 HTTP 错误（4xx/5xx），THEN THE Frontend SHALL 在对话区域显示友好的错误提示消息，包含错误类型描述
2. IF 网络连接失败或请求超时，THEN THE Frontend SHALL 显示网络错误提示并提供重试按钮
3. WHEN Backend 返回 429（会话超限），THE Frontend SHALL 提示用户稍后重试
4. WHEN Backend 返回 409（会话忙碌），THE Frontend SHALL 提示用户等待当前任务完成

### 需求 6：后端 CORS 配置

**用户故事：** 作为开发者，我希望后端支持跨域请求，以便前端在开发和生产环境中均可正常调用 API。

#### 验收标准

1. THE Backend SHALL 配置 CORS 中间件，允许来自前端开发服务器（默认 `http://localhost:5173`）的跨域请求
2. THE Backend SHALL 允许 `GET`、`POST`、`DELETE` 方法和 `Content-Type` 请求头的跨域访问
