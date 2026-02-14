# 执行日志

### 任务 #1: 建立任务目录与执行基线 ✅
**状态**：已完成
**时间**：2026-02-14 00:00 - 2026-02-14 00:05
**执行者**：LD

#### 实现结果
- ✅ 建立任务目录与 index/plan/execution/review 文档骨架
- ✅ 明确实施顺序（M1-M4）

#### 相关文件
- `tasks/refactor_core_engine_hardening/index.md` (新增)
- `tasks/refactor_core_engine_hardening/P_plan.md` (新增)
- `tasks/refactor_core_engine_hardening/E_execution.md` (新增)
- `tasks/refactor_core_engine_hardening/R2_review.md` (新增)

### 任务 #2: 核心引擎层休整落地（阶段 A-F） ✅
**状态**：已完成
**时间**：2026-02-14 00:05 - 2026-02-14 01:20
**执行者**：LD

#### 实现结果
- ✅ 阶段 A：`/model` 切换与路由模型跟随/独立语义落地（`router_follow_active_model` + `switch_model` 同步）。
- ✅ 阶段 B：`system_message_mode` 对齐 `auto|merge|replace`，兼容 `multi->replace`，并补充文档说明。
- ✅ 阶段 C：新增 `ConversationMemory.trim_for_request()`，并在请求前对最终消息 token 预算做硬约束。
- ✅ 阶段 D：新增全局工具结果硬截断配置 `EXCELMANUS_TOOL_RESULT_HARD_CAP_CHARS`，工具级截断后再兜底。
- ✅ 阶段 E：保留 `TASK_ITEM_UPDATED` 枚举，补充 `task_update` SSE 稳定映射文档与契约测试。
- ✅ 阶段 F：补齐 engine/config/memory/api/events 回归测试。

#### 遇到的问题（已解决）
- **问题**：`_execute_tool_call` 中 `post_hook` 缩进错误导致 `IndentationError`。
- **解决**：修复缩进并补跑全量核心测试。
- **耗时**：约 10 分钟。

- **问题**：API 并发属性测试受本地 MCP 启动器不稳定影响（`CancelledError` 链式放大）。
- **解决**：在 `tests/test_api.py` 的 `_setup_api_globals` 内默认桩掉 `AgentEngine.initialize_mcp()`，隔离环境噪声。
- **耗时**：约 15 分钟。

#### 回归验证
- `uv run --with pytest --with hypothesis --with pytest-asyncio pytest -q tests/test_engine.py tests/test_engine_events.py tests/test_events.py tests/test_renderer.py tests/test_skillpacks.py tests/test_skill_context_budget.py tests/test_tool_registry.py tests/test_memory.py tests/test_config.py tests/test_api.py`
- 结果：`309 passed`

#### 相关文件
- `excelmanus/config.py`
- `excelmanus/engine.py`
- `excelmanus/memory.py`
- `excelmanus/session.py`
- `README.md`
- `tests/test_engine.py`
- `tests/test_config.py`
- `tests/test_memory.py`
- `tests/test_events.py`
- `tests/test_api.py`
