# 套件 JSON

适用版本：1.8.0 源码 · 更新日期：2026-09-21

本目录只放 suite 文件。目录约定和怎么跑见上一级 [README.md](../README.md)。
`python -m excelmanus.bench --all` 会运行本目录下 `include_in_all` 未设为 `false` 的 `*.json`。

## 字段

| 字段 | 说明 |
| --- | --- |
| `include_in_all` | 默认 true；体验向长套件设 false |
| `message` / `messages` | 单轮 / 多轮；多轮元素可为字符串或 `{text, attachments, images}` |
| `attachments` / `images` | 第一轮上传的本地文件（等价前端预上传） |
| `chat_mode` | `write` / `read` / `plan`，默认 `write` |
| `auto_approve` | `fullaccess`（默认）/ `accept` / `reject`；决定评测如何处理授权和审批 |
| `auto_replies` | `ask_user` 自动回答队列；选区确认问题收到文本回答时按「已澄清」记录并结束该问题，不等于确认具体单元格区域 |
| `turn_timeout` | 单轮硬超时秒数；优先级 case > suite > CLI `--turn-timeout`，0 = 不限制 |
| `assertions` | 声明式断言；suite 级会先与 case 级合并。体验套件不要写 |
| `expected` | 断言套件：`golden_file` + `answer_position`。体验套件：只放 `lens` / `review_focus` |

`assertions` 支持的键（详见 `excelmanus/bench_validator.py` 与 `excelmanus/bench_checks.py`）：

- 过程：`status`、`max_iterations`、`max_llm_calls`、`max_tool_calls`、
  `max_tool_failures`、`max_tokens`、`max_duration_seconds`、`expected_skill`、
  `required_tools`、`forbidden_tools`、`no_empty_promise`、`no_silent_first_turn`、
  `no_cheat_read`、`reply_contains` / `reply_not_contains`、`min_match_rate`
- 结果：`uploads_unchanged`（uploads 原件未被改）、`answers_file`（标准答案，
  供 `@answers:key` 引用，本身不是断言）、`output_checks`（产出文件/回复的
  业务检查，类型见 `bench_checks.py` 模块 docstring：file_exists / cell_value /
  formula / sheet_props / charts / docx / reply_number 等）
- 常开护栏：`no_cheat_read`（suite 显式声明，realistic 已开）——工具参数触碰
  `answers.json` / `suite_*.json` / `bench/fixtures` 即判作弊 fail

## 效率预算默认是发布门禁

`max_iterations` / `max_llm_calls` / `max_tool_calls` / `max_tool_failures` /
`max_tokens` / `max_duration_seconds` 超限默认判为失败并影响退出码，避免性能
回归被 warning 淹没。体验运行可显式使用 `--no-strict-efficiency` 降级为 warning。
正确性由 `output_checks` 与 error 级断言判定。调预算先看
`summarize_runs.py` 的 P90 分布，以实际分布为依据，并保留调整前后的结果。

`chat_mode` 使用 `write`、`read` 或 `plan`。直接工具与代码执行共存，新增用例不应依赖独立的 code 模式切换。

## 权限

- `auto_approve: "fullaccess"`（默认）预授权本会话完整权限，**不是**网页开箱默认。
- 要覆盖网页默认审批，用 `auto_approve: "accept"`（见 `suite_write_approval.json`）。
  这会模拟逐次接受已发出的审批请求。`run_shell`、`delete_file` 属于默认确认工具；
  普通工作区写入通常只记录审计，`run_code` 另受代码策略约束。只读和计划模式
  的执行限制不因自动回答审批而取消。

## 反绕过说明

- `forbidden_tools` 同时扫描 `run_code` / `write_text_file` / `run_shell` 参数内的
  禁用工具名与 `em.<动词>` 简写（如只读题用 `run_code` 调 `em.edit` 写盘同样判 fail）。
- `charts` 可加 `min_series`（每张图至少绑几个数据系列，防空图框）；
  `file_exists` 可加 `min_rows`（有 N 个数据行的文件才计入 `min`，防空文件凑数）。
- 双口径 `any_of`（如含/不含已取消）必须配一条口径声明检查（`reply_regex` 匹配
  口径/状态/取消/退货等词），以便检查结果与回复中声明的统计范围是否一致（R01/R26 已配置）。

## 模型配置与防卡死

- 模型凭据：`python -m excelmanus.bench --import-env test.env --model YOUR_MODEL_ID`
  把本地凭据清单导入当前主库并激活，然后退出（`test.env` 也可写 `model=`）。
  CLI 不会自动隔离日常配置：先设置独立的 `EXCELMANUS_HOME`，再执行导入与评测，
  具体示例见 [Bench README](../README.md)。
- 自动批准双保险：事件驱动（收到 `PENDING_APPROVAL`/`USER_QUESTION` 立即提交，
  同网页 `/approve` `/answer` 载荷）+ 回合内 50ms 兜底轮询，按 id 去重，
  用于处理交互注册时序；仍需设置超时，并检查审批链路是否按预期完成。
- `turn_timeout` 到点强制中止该轮并记为 TurnTimeoutError；用例工作区
  运行前自动清空，避免上次产物干扰断言。体验套件默认 suite 级 900s。
- 用例过滤：`python -m excelmanus.bench --suite X --case E15 [--case E20] [--wave 2]`，
  不再需要 filter_suite 中间产物。
