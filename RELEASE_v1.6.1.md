# ExcelManus v1.6.1

> **v1.6.0 → v1.6.1** · 150 files changed · +19,873 / −6,441 lines

## Highlights

- **Engine 架构重构** — 将 3,000+ 行的 engine.py 拆分为 7 个独立模块，提升可维护性和可测试性
- **验证器分级系统** — advisory → blocking 两级验证，自动探索子代理，并行执行
- **QQ OAuth 登录** — 新增 QQ 第三方登录 + OAuth 用户设置密码
- **Web UI 动效升级** — Framer Motion 动画、ChatInput 重构、侧边栏树形视图重写
- **部署脚本 v2.0** — 支持回滚、状态查询、Hooks、systemd、跨平台双密钥

---

## 新功能

### 后端

- **验证器分级** (`verifier tiers`)：advisory 建议级 → blocking 阻断级，根据任务复杂度自动选择验证深度
- **自动探索子代理** (`explorer subagent`)：verifier 发现问题时自动派出探索子代理收集上下文
- **子代理并行执行** (`subagent parallel`)：独立子任务可并行分发，减少总执行时间
- **记忆修复** (`memory repair`)：检测并自动修复损坏的记忆条目
- **QQ OAuth** (`auth/router.py`)：QQ 第三方登录 + OAuth 用户可设置本地密码
- **公式检测器** (`pipeline/formula_detector.py`)：VLM Pipeline 新增公式识别阶段
- **Pipeline 分区增强** (`pipeline/progressive.py`, `pipeline/phases.py`)：大表格分区提取优化
- **工具错误系统** (`engine_core/tool_errors.py`)：结构化工具错误分类和恢复策略
- **Inline Checkpoint** (`session_state.py`, `session_state_store.py`)：会话状态持久化与断点恢复
- **多用户隔离**：会话级资源隔离增强

### 前端

- **Motion 动画**：消息卡片、侧边栏、工具卡片等全面加入 Framer Motion 过渡动画
- **ChatInput 重构**：拆分为 `ChatInput` + `CommandPopover` + `FileAttachmentChips` + `ChatModeTabs` + 常量文件，代码量减少 60%
- **侧边栏树形视图** (`TreeNodeItem.tsx`, `FlatFileListView.tsx`)：文件浏览器重写为可折叠树形结构
- **验证卡片** (`VerificationCard.tsx`)：验证器结果在聊天中可视化展示
- **子代理区块增强** (`SubagentBlock.tsx`)：展示并行执行状态
- **管理员登录配置** (`LoginConfigTab.tsx`)：管理页新增 OAuth 配置面板
- **Thinking 区块增强** (`ThinkingBlock.tsx`)：展示推理过程的折叠/展开

### 部署

- **Deploy v2.0** (`deploy.sh`, `deploy.ps1`)：
  - `rollback` 命令：一键回滚到上一版本
  - `status` 命令：远程服务状态查询
  - `init-env` 命令：自动推送 .env 模板
  - `check` 命令：前后端互联检测
  - `history` / `logs` 命令：部署历史和日志查看
  - 支持 systemd 服务管理（自动创建 unit 文件）
  - 支持 pre-deploy / post-deploy hooks
  - 支持 双 SSH 密钥（前后端分离部署）
  - 跨平台兼容：macOS rsync 自动检测 `--append-verify`

---

## 重构

- **Engine 拆分**：`engine.py` (3167 行变更) 拆分为 7 个模块：
  - `engine_types.py` — 类型定义
  - `engine_utils.py` — 工具函数
  - `engine_core/llm_caller.py` — LLM 调用逻辑
  - `engine_core/skill_resolver.py` — 技能路由
  - `engine_core/meta_tools.py` — 元工具处理
  - `engine_core/interaction_handler.py` — 交互处理
  - `engine_core/tool_errors.py` — 工具错误处理
- **VisionExtractor 精简**：移除旧提取逻辑 (−414 行)，统一走 Pipeline
- **ExcelFilesBar 拆分**：899 行减为多个组件 (`ExcelFilesDialogs`, `FlatFileListView`, `TreeNodeItem`, `InlineInputs`)
- **许可证变更**：MIT → Apache-2.0 + NOTICE 文件

---

## 修复

- 部署脚本 standalone 静态资源未复制导致前端 JS 404
- PM2 restart 未更新 `--cwd` 导致工作目录错误
- 前端 `NEXT_PUBLIC_BACKEND_ORIGIN` 指向旧内网 IP 自动检测修复
- 部署互联检测 SSH grep 挂起（加 timeout）
- 前端构建被旧 `src.bak.*` 目录干扰（自动清理）
- PBT 测试 `property_8` 空路径过滤
- 图片工具参数处理优化

---

## 测试

新增 12 个测试文件：
- `test_inline_checkpoint.py` — 断点恢复
- `test_pipeline_integration.py` — Pipeline 集成 (740 行)
- `test_subagent_token.py` — 子代理 token 限制
- `test_tool_errors.py` — 工具错误分类
- `test_tool_pruning.py` — 工具裁剪
- `test_upload_from_url.py` — URL 上传
- `test_verification_playbook.py` — 验证剧本
- `test_verifier_blocking.py` — 阻断级验证
- `test_verifier_delta.py` — 增量验证
- `test_verifier_levels.py` — 验证分级
- `engine_core/test_subagent_orchestrator.py` — 子代理编排

---

## 升级指南

```bash
pip install .                    # 重新安装
cd web && npm install            # 前端依赖更新
```

数据库 migration 在首次启动时自动执行。从 v1.6.0 升级无破坏性变更。
