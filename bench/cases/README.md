# 套件 JSON

本目录只放 suite 文件。目录约定和怎么跑见上一级 [README.md](../README.md)。
`python -m excelmanus.bench --all` 会运行本目录下 `include_in_all` 未设为 `false` 的 `*.json`。

## 字段

| 字段 | 说明 |
| --- | --- |
| `include_in_all` | 默认 true；体验向长套件设 false |
| `message` / `messages` | 单轮 / 多轮；多轮元素可为字符串或 `{text, attachments, images}` |
| `attachments` / `images` | 第一轮上传的本地文件（等价前端预上传） |
| `chat_mode` | `write` / `read` / `plan`，默认 `write` |
| `present_as` | `native` / `code` |
| `auto_approve` | `fullaccess`（默认）/ `accept` / `reject` |
| `auto_replies` | `ask_user` 自动回答队列 |
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

## 效率预算为 warn-only

`max_iterations` / `max_llm_calls` / `max_tool_calls` / `max_tool_failures` /
`max_tokens` / `max_duration_seconds` 超限只记 `severity: warning`：复盘可见，
不判 fail、不影响退出码（见 `bench_validator.EFFICIENCY_RULES`）。
正确性由 `output_checks` 与 error 级断言判定。调预算先看
`summarize_runs.py` 的 P90 分布，不要按理想链路拍脑袋。

## 权限

- `auto_approve: "fullaccess"`（默认）预授权本会话完整权限，**不是**网页开箱默认。
- 要覆盖网页默认审批，用 `auto_approve: "accept"`（见 `suite_write_approval.json`）。
  审批门只拦 `run_shell`、`delete_file` 这类 Tier A 确认工具，写文件不弹审批。

## 反绕过说明

- `forbidden_tools` 同时扫描 `run_code` / `write_text_file` / `run_shell` 参数内的
  禁用工具名与 `em.<动词>` 简写（如只读题用 `run_code` 调 `em.edit` 写盘同样判 fail）。
- `charts` 可加 `min_series`（每张图至少绑几个数据系列，防空图框）；
  `file_exists` 可加 `min_rows`（有 N 个数据行的文件才计入 `min`，防空文件凑数）。
- 双口径 `any_of`（如含/不含已取消）必须配一条口径声明检查（`reply_regex` 匹配
  口径/状态/取消/退货等词），否则"沉默选错口径"也能过（R01/R26 已配）。

## 模型配置与防卡死

- 模型凭据：`python -m excelmanus.bench --import-env test.env --model mimo-v2.5-pro` 把本地凭据清单导入主库并激活（`test.env` 也可写 `model=`）。
  bench 从激活档案读取模型；`--import-env` 只是把清单写入本次隔离主库。
- 自动批准双保险：事件驱动（收到 `PENDING_APPROVAL`/`USER_QUESTION` 立即提交，
  同网页 `/approve` `/answer` 载荷）+ 回合内 50ms 兜底轮询，按 id 去重，
  Future 未注册等竞态也不会无人卡死。
- `turn_timeout` 到点强制中止该轮并记为 TurnTimeoutError；用例工作区
  运行前自动清空，避免上次产物干扰断言。体验套件默认 suite 级 900s。
- 用例过滤：`python -m excelmanus.bench --suite X --case E15 [--case E20] [--wave 2]`，
  不再需要 filter_suite 中间产物。
