# CLI 全链路信息密集 Dashboard 重构 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 将当前分散的 CLI 输出重构为统一 Dashboard 体验，覆盖输入、过程、结果、异常、历史、帮助六个环节，并保持命令语义兼容。  
**Architecture:** 保留现有引擎与事件协议，新增“Dashboard 渲染层 + 统一回合执行管线”，通过 `Live` 渲染顶部状态栏/中部时间线/底部状态条，在每轮结束时输出结构化结果与恢复建议；提供 `classic` 回退模式保证兼容。  
**Tech Stack:** Python 3.12, Rich, prompt_toolkit, pytest, hypothesis（现有）

---

## Summary

本方案采用你选定的“方案1（大改版）”，一次完成 CLI 显示架构升级，但不改 Agent/Tool 协议。  
核心策略是“**渲染重构，不动业务语义**”：  
1. 命令语义保持不变。  
2. 事件类型与 API 协议保持不变。  
3. 显示层重构为 Dashboard（默认），保留 classic 回退。  
4. 所有路径（自然语言、`/subagent`、审批、问题回答）统一走一个回合执行入口，消除重复打印逻辑。

---

## Public API / Interface Changes

1. 新增环境变量 `EXCELMANUS_CLI_LAYOUT_MODE`，取值 `dashboard|classic`，默认 `dashboard`。  
2. 新增 CLI 命令 `/ui [status|dashboard|classic]`，用于会话内查看/切换显示模式。  
3. `/help` 文案与结构升级为流程导向（命令语义不变）。  
4. `/history` 输出升级为“回合聚合视图”（命令语义不变）。  
5. `EventType` 与 `ToolCallEvent` 字段不新增、不改名，保持现有协议兼容。

---

## Architecture Design (Decision-Complete)

### 1) 新增显示域模型
在 `/Users/jiangwenxuan/Desktop/excelagent/excelmanus/cli_dashboard.py` 新增：
- `UiLayoutMode`（`dashboard`, `classic`）
- `DashboardTurnState`
- `DashboardTimelineEntry`
- `DashboardMetrics`
- `DashboardSessionBadges`

约束：
- 时间线最多 200 条，超出后显示 `... (+N 条已折叠)`。
- `thinking` / `thinking_delta` 合并显示，避免刷屏。
- `subagent` 事件保留你要求的信息密度（名称、权限、会话、轮次、工具累计/增量、摘要分段）。

### 2) 新增 Dashboard 渲染器
在 `/Users/jiangwenxuan/Desktop/excelagent/excelmanus/renderer_dashboard.py` 新增 `DashboardRenderer`：
- 输入：`ToolCallEvent`
- 输出：Rich `Live` 三段布局  
  - 顶部：会话/回合状态（模型、回合号、路由模式、子代理状态、plan/fullaccess/backup）  
  - 中部：事件时间线（工具、子代理、审批、问题、系统）  
  - 底部：动态状态条（思考中/工具执行/子代理轮次/汇总中）
- 提供 `start_turn(...)` / `handle_event(...)` / `finish_turn(...)` / `fail_turn(...)`。

### 3) 回合执行统一入口
在 `/Users/jiangwenxuan/Desktop/excelagent/excelmanus/cli.py` 抽出统一入口：
- `_run_chat_turn(...)`（统一 try/except、渲染、最终回复）
- 所有分支调用它：自然语言、`/subagent`、技能斜杠、待问题回答、审批动作。
- 兼容 `classic`：复用现有 `StreamRenderer`。
- `dashboard`：使用 `DashboardRenderer`，禁用旧 `_LiveStatusTicker` 的重复视觉输出。

### 4) 错误分级与恢复建议
在 `/Users/jiangwenxuan/Desktop/excelagent/excelmanus/cli_errors.py` 新增：
- `CliErrorCategory`（`input`, `unknown_command`, `config_io`, `tool_runtime`, `approval`, `network`, `unexpected`）
- `format_cli_error(...)` 返回：
  - 用户可读标题
  - 错误摘要
  - 1-3 条可执行恢复命令（如 `/help`, `/history`, `/save`, `/config list`）

`cli.py` 所有 `处理请求时发生错误` 统一走该格式化输出。

### 5) 输入区与发现性增强
在 `/Users/jiangwenxuan/Desktop/excelagent/excelmanus/cli.py`：
- Prompt 升级为信息密集徽章：`[model] [#turn] [plan] [subagent] [layout] ❯`
- 未知命令提示改为“近似命令推荐 Top3”（`difflib.get_close_matches`）。
- `/help` 改为场景化示例结构：
  - 快速开始
  - 子代理协作
  - 审批与回滚
  - 故障排查

### 6) 历史视图升级
在 `/Users/jiangwenxuan/Desktop/excelagent/excelmanus/cli.py` 的 `_render_history`：
- 从消息流聚合“回合卡片”：
  - 用户输入摘要
  - 助手输出摘要
  - 工具调用数
  - 子代理触发标记
  - 审批/问题标记
- 默认显示最近 20 回合，附总计统计。

---

## Implementation Tasks (TDD, Bite-Sized)

### Task 1: 锁定兼容契约 + 新模式开关测试
**Files**
- Modify: `/Users/jiangwenxuan/Desktop/excelagent/tests/test_cli.py`
- Modify: `/Users/jiangwenxuan/Desktop/excelagent/tests/test_config.py`

1. 写失败测试：`EXCELMANUS_CLI_LAYOUT_MODE` 默认 `dashboard`，非法值回退 `dashboard`。  
2. 写失败测试：`/ui status|dashboard|classic` 路由行为。  
3. 运行：`uv run pytest tests/test_cli.py tests/test_config.py -q`，确认失败。  
4. 最小实现配置字段与命令分支。  
5. 复跑同命令，确认通过。  
6. Commit: `feat(cli): add layout mode config and /ui command`

### Task 2: 引入 Dashboard 状态模型
**Files**
- Create: `/Users/jiangwenxuan/Desktop/excelagent/excelmanus/cli_dashboard.py`
- Create: `/Users/jiangwenxuan/Desktop/excelagent/tests/test_cli_dashboard.py`

1. 写失败测试：状态模型默认值、时间线裁剪、subagent 统计累积/增量。  
2. 运行：`uv run pytest tests/test_cli_dashboard.py -q`。  
3. 最小实现 dataclass 与聚合逻辑。  
4. 复跑并通过。  
5. Commit: `feat(cli): add dashboard state model`

### Task 3: 实现 DashboardRenderer（三段布局）
**Files**
- Create: `/Users/jiangwenxuan/Desktop/excelagent/excelmanus/renderer_dashboard.py`
- Modify: `/Users/jiangwenxuan/Desktop/excelagent/tests/test_renderer.py`
- Create: `/Users/jiangwenxuan/Desktop/excelagent/tests/test_renderer_dashboard.py`

1. 写失败测试：header/body/footer 各自包含关键字段；窄终端退化显示。  
2. 写失败测试：subagent 生命周期信息完整显示。  
3. 运行：`uv run pytest tests/test_renderer.py tests/test_renderer_dashboard.py -q`。  
4. 实现 `start_turn/handle_event/finish_turn/fail_turn`。  
5. 复跑并通过。  
6. Commit: `feat(cli): add dashboard live renderer`

### Task 4: 统一回合执行入口，替换重复逻辑
**Files**
- Modify: `/Users/jiangwenxuan/Desktop/excelagent/excelmanus/cli.py`
- Modify: `/Users/jiangwenxuan/Desktop/excelagent/tests/test_cli.py`

1. 写失败测试：自然语言、`/subagent`、审批、问题回答均走统一入口。  
2. 写失败测试：`dashboard` 模式不重复显示旧 ticker 文案。  
3. 运行：`uv run pytest tests/test_cli.py -q`。  
4. 实现 `_run_chat_turn(...)` 并替换 4 处重复分支。  
5. 复跑并通过。  
6. Commit: `refactor(cli): unify turn execution pipeline`

### Task 5: 异常分级与恢复建议面板
**Files**
- Create: `/Users/jiangwenxuan/Desktop/excelagent/excelmanus/cli_errors.py`
- Modify: `/Users/jiangwenxuan/Desktop/excelagent/excelmanus/cli.py`
- Create: `/Users/jiangwenxuan/Desktop/excelagent/tests/test_cli_errors.py`

1. 写失败测试：不同异常映射到不同 category 与建议命令。  
2. 写失败测试：最终输出为结构化错误面板而非裸字符串。  
3. 运行：`uv run pytest tests/test_cli_errors.py tests/test_cli.py -q`。  
4. 实现错误分类与统一渲染。  
5. 复跑并通过。  
6. Commit: `feat(cli): structured error panels with recovery hints`

### Task 6: 输入与命令发现优化
**Files**
- Modify: `/Users/jiangwenxuan/Desktop/excelagent/excelmanus/cli.py`
- Modify: `/Users/jiangwenxuan/Desktop/excelagent/tests/test_cli.py`

1. 写失败测试：prompt 含 `[model][turn][layout][subagent][plan]` 信息。  
2. 写失败测试：未知命令返回近似推荐 Top3。  
3. 运行：`uv run pytest tests/test_cli.py -q`。  
4. 实现 prompt 与推荐逻辑。  
5. 复跑并通过。  
6. Commit: `feat(cli): dense prompt badges and smart command suggestions`

### Task 7: `/history` 与 `/help` 重构
**Files**
- Modify: `/Users/jiangwenxuan/Desktop/excelagent/excelmanus/cli.py`
- Modify: `/Users/jiangwenxuan/Desktop/excelagent/tests/test_cli.py`
- Modify: `/Users/jiangwenxuan/Desktop/excelagent/README.md`

1. 写失败测试：`/history` 输出回合聚合与标记统计。  
2. 写失败测试：`/help` 新结构包含 `/ui` 和流程示例。  
3. 运行：`uv run pytest tests/test_cli.py -q`。  
4. 实现 history/help 重构与 README 同步。  
5. 复跑并通过。  
6. Commit: `feat(cli): redesign history/help for dense discoverability`

### Task 8: 回归、文档、交付验证
**Files**
- Modify: `/Users/jiangwenxuan/Desktop/excelagent/docs/plans/2026-02-20-cli-dashboard-redesign-design.md`（实现时落盘）
- Modify: `/Users/jiangwenxuan/Desktop/excelagent/README.md`

1. 运行定向回归：`uv run pytest tests/test_cli.py tests/test_renderer.py tests/test_events.py tests/test_config.py -q`。  
2. 运行全量关键回归：`uv run pytest -q`（若耗时过长至少跑 `tests/test_cli.py tests/test_renderer.py` 全集）。  
3. 手工验收场景（见下节）逐条过一遍。  
4. 记录结果与已知限制到文档。  
5. Commit: `chore(cli): finalize dashboard redesign docs and regression`

---

## Test Cases and Scenarios (Acceptance)

1. 自然语言回合：应显示三段 Dashboard，工具与路由信息完整。  
2. `/subagent run ...`：应显示子代理启动/轮次/摘要/结束全链路。  
3. 待审批流程：应显示审批卡片与后续执行状态，不出现重复提示。  
4. 待问题流程：应显示问题卡片、队列信息、提交后的回合结果。  
5. 未知命令：应给出近似命令建议，不只提示“未知”。  
6. 错误注入：网络错误、配置 IO 错误、工具运行错误分别映射到不同恢复建议。  
7. 窄终端：布局退化为紧凑文本但信息不丢失。  
8. 非交互终端（CI）：自动降级 `classic`，避免 Live 交互问题。  
9. `/history`：显示回合聚合与统计，不再仅仅按消息平铺。  
10. `/help`：包含分场景示例和 `/ui` 命令。

---

## Assumptions and Defaults

1. 默认语言继续使用中文。  
2. 命令语义保持兼容，不删除现有命令。  
3. `EventType` 与 `ToolCallEvent` 协议不变。  
4. 默认显示模式为 `dashboard`，可通过环境变量或 `/ui` 回退 `classic`。  
5. Dashboard 仅作用于 CLI 层，不影响 API/SSE 返回结构。  
6. 回合时间线上限默认 200 条，超过后折叠。  
7. 任何渲染异常都必须回退为可读纯文本，不能中断会话。
