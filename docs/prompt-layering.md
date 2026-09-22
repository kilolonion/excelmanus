# 提示词分层与维护

本页描述分层规则。

适用版本：1.8.0 源码 · 当前规则更新日期：2026-09-21。

[文档导航](README.md) · [Skillpack 协议](skillpack_protocol.md)

任务原则放在提示词中，参数语义放在工具 schema 中，权限与文件约束由运行时执行。Markdown 是 system 正文的维护来源；Python 保留工具描述等不重复的元数据。

## 内容归属

| 位置 | 负责 | 不要放进去的 |
|---|---|---|
| `prompts/core/00_identity.md`（order -100） | 一句话身份 | 角色手册 |
| `prompts/core/10_core_principles.md`（order 0） | 工作区、授权、必要澄清、简洁交付 | 参数教程、首轮剧本 |
| `prompts/core/20_spreadsheet_principles.md`（order 50，`spreadsheet:invariants`） | 证据覆盖、公式缓存、观察版本、写入串行 | 工具索引、字段百科 |
| `prompts/strategies/*.md`（order 100–199） | 何时用、跨调用注意、替代路径 | A1 百科、错误码表 |
| ToolDef / schema | 类型、枚举、参数语义 | 全局授权剧本 |
| Host（`error_payload`、守卫、提交） | 权限、路径、版本、原子提交 | 替模型决定业务策略 |

组装入口：`PromptComposer` / `PromptRegistry` → `build_stable_system_prompt`。直接工具与 `run_code` 共存；内部 `em.*` 绑定完整授权执行目录，字段、Python 签名与输出细节通过 `introspect_capability` 按需加载。默认常驻集合由 `tools/policy.py` 的 `DEFAULT_DISCLOSURE_CORE_TOOLS` 定义，其余内置/MCP 工具按需披露；无发现入口时保留全部授权 schema。

子代理也走此入口：`PromptComposer.fork()` 复用正文素材并创建独立注册表，工具和动态上下文回调绑定子会话。每次请求按子代理的固定权限、当前授权目录和工作簿状态重新组装，热重载不影响父会话的注册表。core 总是保留；非空 `inherit_strategies` 选择具名策略子集，但仍须满足运行时条件，且计划模式保留 `plan:policy`。空列表使用全部符合条件的策略，不授予额外工具权限。

`prompts/subagent/_base.md` 只负责委派边界和结果交付要求，具名文件负责内置角色。用户/项目配置的 `system_prompt` 替换角色正文（包括同名内置角色），保留共享 core 与委派要求。缺失/损坏的内置角色、共享基础、未定义变量或未知继承策略均明确失败；不得退回旧提示词或静默忽略。旧 `base_sections` 不再裁剪共享委派要求。

## 预算口径

- tokenizer：本地 `tiktoken` `o200k_base`。
- 段 token：剥离 frontmatter 后独立计数，读取各文件 `max_tokens`。
- 四栏：原则+策略正文、完整 system（含能力地图与简短代码指引，不常驻完整 SDK）、当前披露工具的 tools JSON、代表性 introspect 查询。
- 静态 token **不是**服务商计费量。
- 原则与策略有各自的静态预算。当前数值应由预算工具重新计算；下方 P0–P5 的数字属于历史测量，不能直接代表当前按需披露后的请求大小。不要为了达到 token 目标删除必需的参数信息。

在仓库根目录运行随源码维护的预算测试：

```bash
uv run pytest tests/test_prompt_budgets.py tests/test_prompt_composer.py
```

需要查看当前静态预算时，可直接使用包内测量入口：

```bash
uv run python -c "from excelmanus.prompt.budget import collect_report, format_report; print(format_report(collect_report()))"
```

历史记录中的 `scripts/check_prompt_budgets.py` 是本地包装脚本，当前未被 Git 跟踪，不作为公开文档的运行前提。CI 配置仍有对此脚本的引用，发布前应核对相应脚本是否随提交提供。更新 `tests/prompt_snapshots/` 时，应核对正文与场景变化，不能仅重写快照后据此判定正确。

## 维护方法

1. 改正文只改 `excelmanus/prompts/*.md`。不要在 `canonical.py` 再写一份。
2. 改参数说明改 Pydantic / `intent_tools` schema；字段查询走 `schema_walk`。
3. 改错误恢复改 `error_payload._REMEDIATION_BY_CODE` 与错误发生处的 extra 字段。
4. 组装快照：`tests/prompt_snapshots/{write,plan,read}.txt`。
5. 热重载仍走 `PromptComposer.reload_if_changed`；加载失败要暴露，不回退旧正文。

## 历史评测与阶段边界

以下条目及 P0–P5 记录保留原阶段的证据与限制，不表示本次文档更新重新执行了这些验证。当前默认执行面为直接工具与 `run_code` 共存；旧报告中的 Native / Code 对照应按报告日期理解。

- 真实模型对照（计划 8.2）部分执行：wave-r5u 同 10 用例已在 qwen-3.8-27b 上重跑（`bench/reports/09-prompt-layering-p5r-vs-r5u.md`）——error 断言 3→1（唯一 e1 为答复措辞 regex，业务动作正确）、工具失败 23→12、R30 code mode 显著提效；tokens 总体 +27% 集中于 R27 operator 试错与 R31 多轮累积。其余场景用例已备好——S01/S03/S05 复用 suite_realistic 的 R19/R11/R26，S04/S07/S10/S11/S14 在 `bench/cases/suite_prompt_contract.json`（`include_in_all: false`，期望值由 `bench/prompt_contract_checks.py` 运行时现算），S13 由 `tests/test_write_contract.py` 覆盖。R01 的 pandas 评审口径已同步为「结果与适用性」。
- pandas 工作区直读：本地 `run_code` GREEN 包装已能 `pd.read_excel` 读取工作区 xlsx，并拒绝 `to_excel` / 产品源码路径；复审后读取守卫与 Native 数据路径规则对齐——区外数据、`.excelmanus` 保留目录、敏感文件、越界符号链接、其他 run 的 pending 均被拒或对元数据隐藏。改回工作簿仍要求 SDK `content_version`，不把 pandas 读取当观察版本。
- 2026-09-19 起取消 Code Mode 外层 wire 坍缩；直接业务工具与 `run_code` 同时可用。策略段与能力地图仍按授权执行目录可达集门控；按需加载的工具不得被业务 profile 再隐藏。
- `write_new` 与 `write_existing` 的 tools JSON 相同：新建能力不靠 `new_workbook` 从目录里拿掉 `edit_spreadsheet`。

---

## 历史实施记录

本节保留各阶段原始结果，供追溯使用。

### P0 完成记录

- 修改文件与关键行为：新增 `excelmanus/prompt/budget.py`、`scripts/check_prompt_budgets.py`；记录 HEAD `4af05600`，工作树并行改动保留。
- 本阶段依赖是否满足：是。
- 已执行命令及结果：预算脚本可复现；P0 超限段为 inspect/analyze/edit/format。
- 场景证据：见 `bench/reports/prompt-layering-p0-budget.json`。
- 预算变化：Native write 原则+策略 2998 / system 3202 / tools JSON 12744 / 发现 2192。
- 兼容影响及精确回退范围：仅测量与脚本，无产品行为。
- 未验证或未交付项：真实模型对照。

### P1 完成记录

- 修改文件与关键行为：`canonical.py` 删除 identity/persona/TOOL_*/规格/run_code/plan 正文副本，只留 `TOOL_DESCRIPTIONS` / `TOOLS_CODE_ONLY` / `FORBIDDEN_MODEL_TERMS`。测试改为独立快照与段名断言。
- 本阶段依赖是否满足：是。
- 已执行命令及结果：`tests/test_prompt_registry.py`、`tests/test_strategy_mode_gating.py` 通过。
- 场景证据：`tests/prompt_snapshots/`。
- 预算变化：无独立产品行为变化（与 P4 正文重写同工作树交付）。
- 兼容影响及精确回退范围：回退时恢复 `canonical.py` 常量与测试 import。
- 未验证或未交付项：无。

### P2 完成记录

- 修改文件与关键行为：`workbook_spec_json_schema()` 嵌入 `edit_spreadsheet`；`$defs` 提升到工具 schema；`schema_walk` 穿过 properties/items/$ref/additionalProperties；introspection 不再截 240 字、不再指向系统规格段。join / 条件格式 rule / expected_version 补全。
- 本阶段依赖是否满足：是。
- 已执行命令及结果：`tests/test_schema_walk.py`、`tests/test_capability_discovery.py`、`tests/test_intent_tools.py` 通过；可用查询结果构造并编译最小 WorkbookSpec。
- 场景证据：S10 本地行为通过（`test_discovered_workbook_spec_compiles_via_edit`）。
- 预算变化：tools JSON 与发现消耗上升（嵌套 schema 完整）。
- 兼容影响及精确回退范围：schema 仍接受字符串字体、列宽字典、CSV 省略 dimensions。
- 未验证或未交付项：真实模型是否主动查询字段。

### P3 完成记录

- 修改文件与关键行为：PATH_INVALID / VERSION_CONFLICT / PRODUCT_SOURCE_FORBIDDEN / SPEC_VALIDATION_FAILED 等 remediation 按当前事实给下一步；缺文件列出拼写候选且禁止擅自换表。
- 本阶段依赖是否满足：是。
- 已执行命令及结果：`tests/test_tool_error_payload.py`、`tests/test_sandbox_hook.py`、`tests/test_file_path_guard.py` 通过。
- 场景证据：S04/S13/S15 本地行为通过（缺文件候选、版本冲突既有契约、源码隔离）。
- 预算变化：无。
- 兼容影响及精确回退范围：错误外形仍是 `status/error_code/message/failure_class/remediation`。
- 未验证或未交付项：Code Mode 桥上的人工改盘对照未新跑（沿用既有 write contract）。

### P4 完成记录

- 修改文件与关键行为：persona / 领域原则 / 各 tool strategy 按草案缩短；去掉无条件禁扫描、禁 ask_user、禁 pandas；list_directory 与缺文件错误允许任务范围内查找。
- 本阶段依赖是否满足：P2 字段可发现后再删规格百科。
- 已执行命令及结果：段预算 `--check` 通过；Native write 原则+策略 954 token。
- 场景证据：见 P5 矩阵。
- 预算变化：见下表。
- 兼容影响及精确回退范围：回退 P4 须先恢复策略正文，不能只删 schema。
- 未验证或未交付项：真实模型对照。

### P5 完成记录

- 修改文件与关键行为：快照、CI `--check`、本文档、`bench/reports/prompt-layering-p5-budget.json`、`bench/reports/08-prompt-layering-p5.md`；新增 `bench/cases/suite_prompt_contract.json` + `bench/prompt_contract_checks.py`，`bench_validator` 作弊围栏补套件名。
- 本阶段依赖是否满足：是。
- 已执行命令及结果：见该报告中的 pytest 组。
- 场景证据：S01–S16、S18 静态可达或本地行为通过；S17 本地沙箱直读通过；全部场景均无真实模型评测。
- 预算变化（Native write-existing）：原则+策略 2998→954（-68%）；完整 system 3202→1175（-63%）；tools JSON 12744→15150（+19%，嵌套 WorkbookSpec + 复审按实现补齐的 schema）；发现 2192→5223（按需查询变完整且绑执行目录）。system+tools JSON 合计 15946→16325（+2%），未达 -25% 目标，差额来自必要 schema，不删。复审后测量见 `bench/reports/prompt-layering-p5r-budget.json`。
- 兼容影响及精确回退范围：工具名、别名、read/plan 权限保持。
- 未验证或未交付项：计划 8.2 真实模型对照；效率观察项无线上样本。

### 复审修复记录（评审后第二轮）

针对评审提出的 5 个缺口：

- **沙盒读取边界（原 P1 缺口）**：`sandbox_hook` 读取守卫统一数据路径规则——工作区外用户数据、`.excelmanus`/`.versions` 保留目录、敏感文件（`.env`/`config.env`/`excelmanus.db`/`installations.json`）、产品源码、越界符号链接、其他 run pending 均拒读；元数据探查（stat/exists/listdir/scandir）将被拒路径表现为不存在，`open` 内容读取抛 `PermissionError`；tmpdir 写后探查保持可见。`test_sandbox_hook.py`/`test_pandas_workspace_read.py`/`test_write_contract.py` 覆盖正负向用例。
- **数据验证发现与 schema（原 P1 缺口）**：`can_i_do` 意图路由补「下拉框/数据验证/下拉/validation」→ `format_spreadsheet`；能力描述如实报告 `operations.kind=data_validation`；format rule schema 合并条件格式与数据验证（`type=list` 等可执行）；`TestDataValidationWrite` 验证落盘。
- **Schema↔实现对齐（原 P2 缺口）**：join 如实声明单键 + `header_row` + 别名；`workbook_spec`/`operations`/`values`/`selection`/`conditions`/`aggregations` 接受 JSON 字符串与 `spill:` 句柄；edit/format op 别名与字段补齐（`additionalProperties:false` 仍生效）；`tests/test_tool_schema_validation.py` 正负向回归。
- **预算口径（原 P2 缺口）**：`prompt/budget.py` 统计真实 wire envelope，含 task/plan 等会话级工具；2026-09-19 起旧 native/code 输入采用统一披露。发现查询绑定授权执行目录，wire 与 SDK 工具数仍分列。
- **场景证据（原 P2 缺口）**：S05 阻塞式 ask_user 挂起/恢复有引擎级测试；S08 新增 `inspect_spreadsheet(expected_version=)` 跨页版本漂移检测（STALE_SNAPSHOT + 新版本字段）；S13 新增真实 SDK bridge + registry 的冲突恢复链路测试。S10 明确为「Spec 建簿 + format 加验证」两段路径。

仍未交付：计划 8.2 真实模型对照（需授权与凭据）；模型是否主动使用新发现面无线上样本。
