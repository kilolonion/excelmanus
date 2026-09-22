# ExcelManus v1.8.0 Release Notes

**发布日期**: 2026-09-20
**对比基线**: `30c7fae4`（1.7.2 源码点）
**发布范围**: 63 个提交；含当前工作区待发布改动共 1325 个文件变化（+208,441 / -172,996）

ExcelManus 1.8.0 是 1.7.2 之后的完整产品换代：从“多用户 + 渠道机器人 + Docker/CLI”路线，收敛为单用户本地工作台 + 独立工作区运行时 + Web/桌面双形态。本说明覆盖这 63 个提交里的全部能力、架构与兼容性变化，不限于发布前几天的修复。

---

## 一、产品形态重构（破坏性）

1.7.2 是“CLI 双模式 + 多渠道机器人 + 多用户隔离 + Docker 沙盒”，1.8.0 已经完全不同：

| 维度 | 1.7.2 | 1.8.0 |
| --- | --- | --- |
| 产品形态 | CLI / API 双模式 | Web UI + REST API + Windows / macOS 桌面 |
| 用户模型 | 多用户、账号、管理后台 | 单用户，进程级凭证与记忆 |
| 数据模型 | 用户目录隔离（`users/`） | 工作区目录 + 主数据库 `model_profiles` / `config_kv` |
| 会话模型 | 会话独立 | 会话绑定工作区，同工作区多会话共享文件 |
| 执行边界 | Docker 沙盒 | 本机子进程 + 路径 / 权限 / 审批策略 |
| 交互渠道 | Telegram / QQ / 飞书机器人 | 移除渠道层，只保留 Web 工作台 |
| 文件写入 | 覆盖式保存、独立备份 | 内容版本 + 事务提交 + 修订历史 + 恢复 |

被移除或替换的旧入口：Windows GUI 安装器（`ExcelManus.exe`）、`excelmanus_tg_bot.py`、`excelmanus/channels/`、`excelmanus/cli/`、Docker 部署文件、`deploy/setup-ui/`、桌面快捷方式 API、`excelmanus/window_perception/`（被新的工作簿窗口层取代）。

---

## 二、统一工作台与桌面应用

### 2.1 独立工作区运行时

`4af05600` 起，会话直接绑定工作区规范化路径与版本历史，Agent / 工具 / 前端使用同一份文件身份（`workspaceKey + relative`）：

- 工作区登记、重命名、分组、成员管理，同一工作区多会话共享文件。
- 文件身份统一：`CanonicalPath`、overlay 映射、`outputs/` / `backups/` 不再当作交付物暴露。
- 工作区文件服务 `WorkspaceFileService` 统一负责打开、复制、写入、删除、修订与事务恢复。
- 默认工作区为 `EXCELMANUS_HOME/data`，不再自动登记源码目录；历史会话保持原位不搬移。

### 2.2 内容版本与事务写入

- `workbook_commit` / `commit_bytes`：所有写入走“锁 → 版本检查 → 原子替换 → 修订登记”。
- `expected_version` 冲突返回 `VERSION_CONFLICT`，多会话或人工编辑不会静默覆盖。
- 写入事务记录 `operation_id` / `tx_id`，支持 `failed_partial` 恢复、`committed_files`、`recovery_required`。
- 修订历史落在工作区 `.excelmanus/revisions/`，支持列出、预览、比较、恢复、删除、保留 checkpoint lineage。
- `/api/v1/sessions/{sid}/operations` 保留操作时间线；事务收据走 `/api/v1/sessions/{sid}/mutations/{operation_id}`，两者不再互相覆盖。

### 2.3 桌面 App（全新）

新增 `desktop/`：Electron 壳 + PyInstaller 后端 + Next standalone 前端 + 可搬移 Python / Node.js 运行时。

- 一键启动、单实例保护、端口复用、同一端口重启（退出码 75）。
- 正常关闭通过 stdin 管道排空 API；超时才强杀，失败保留进程引用并报错。
- macOS DMG（ad-hoc 签名）与 Windows NSIS x64 目标；Windows 具备 Job Object、DACL 密钥保护、无控制台启动、长路径 manifest。
- `prepare:python` 从 uv 托管 CPython 复制真实解释器，`run_code` 不再依赖系统 Python；Node 使用官方 22.23.2 发行包并校验 SHA-256。
- 桌面模式禁用源码 Git 更新 / 停机恢复 / 部署入口，改用新版安装包升级；profile 位于 Electron userData，不静默合并源码版数据。
- CI：`.github/workflows/desktop-build.yml` 在 tag 上构建 macOS / Windows，Windows 走“真实 `dist/win-unpacked` → NSIS 安装 → 设置重启 → 正常退出 → 覆盖安装 → 卸载保留 profile”链路，并在需要时校验 Authenticode。

### 2.4 Web 工作台

- 统一响应式外壳：顶栏、会话头、工作区标签、侧栏、次级滑出面板在桌面 / 平板 / 手机间共用同一列边界。
- 表格工作台：Univer 区域加载（A1:AX200 分页）、首屏壳层、格式异步补齐、未加载区域编辑拦截、冲突保留未提交编辑。
- 文件预览：Excel / Word / 文本 / 图片 / 对比视图统一进 `FilePreviewHost`；图片走鉴权请求而非公开 URL。
- 后台任务面板：子代理运行时列表、进度、补充指令、暂停 / 取消 / 继续。
- 文件历史工作区：修订时间线、diff、恢复入口。
- 模型管理：提供商卡片、模型档案、OAuth 订阅、能力探测、跨标签页同步、移动端 BottomSheet。
- 首次引导：向导、教练标记、实践步骤、设置引导、可跳过与持久化进度。
- 前端测试 74 个文件 / 533 项，生产构建、TypeScript、ESLint 全绿。

### 2.5 附件与图片

- 新增 `excelmanus/attachments/`：附件存储、类型识别、鉴权读取；聊天图片不再依赖公开 URL。
- 视觉输入路径收敛到当前模型：附件 → 主模型 + 结构化规格；`vision_capability.py` 按模型判断视觉能力，不再静默调用独立 VLM 描述。
- 图片转表格：截图 → `WorkbookSpec` → 校验 → 生成工作簿；视觉不足时显式提示，不伪装成功。
- 文件预览支持 Excel / Word / 文本 / 图片统一宿主，上传支持文件夹参数与 `.xls` / `.xlsb` 自动转换。

---

## 三、Agent 运行时与工具系统

### 3.1 循环收敛与恢复

- 主循环默认上限从 50 提升到 120，子代理上限同为 120；循环预算、连续失败熔断、并行工具计数统一。
- 工具调用生命周期事件：`TURN_START/END/FAILED`、`STEP_*`、`TOOL_CALL_STATE`、`execution_id` / `execution_state`。
- 主任务中断恢复：`/api/v1/sessions/{sid}/turn`、`/resume`、SSE `subscribe`、gap 检测、持久化 tool result；不自动重放结果不明的写入。
- 审批内联解决：pending approval 在同一轮等待决策，支持中止、超时、取消、重复 / 迟到决策；状态可跨进程恢复。
- `ask_user` 改为阻塞式多问题队列；前端分页与批次收尾对齐。
- 后台子代理：`delegate`、`list_subagents`、后台运行、补充指令、暂停 / 取消 / 继续、事件聚合。

### 3.2 Code Mode 与统一 SDK

- `run_code` 的 wire surface 保持不变，SDK 绑定来自同一份有效工具目录（`EffectiveToolCatalog`）。
- 原生调用与 SDK 调用共享权限、审批、结构化结果、版本与错误合同；不再有第二套参数 / 返回约定。
- `introspect_capability` 支持按需发现工具、schema、输出合同；目录摘要随实际定义变化。
- 大结果 `result_spill` / `selection_spill`：句柄绑定工作区，通过 `read_text_file` 取回完整 JSON / selection。
- 沙盒执行边界：拦截 `os` / `shutil` / `pathlib` 直写、`builtins.open`、`with open`、`os.open`、`remove` / `unlink`；`run_shell` 有白名单与审批。
- pending 写入改为不可伪造的 per-run 目录 + manifest + 真实 CAS，宿主不再信任 stderr 协议。
- 显式“跳过”模式自动放行 ToolRuntime / Hook 的 `ASK`，允许网络与子进程；普通模式保持拦截。

### 3.3 工具目录（33 个内置；由运行时 registry 自动枚举）

表格意图（9）：`inspect_spreadsheet`、`analyze_spreadsheet`、`compare_spreadsheets`、`edit_spreadsheet`、`format_spreadsheet`、`split_spreadsheet`、`manage_spreadsheet_objects`、`trace_spreadsheet_formulas`、`manage_spreadsheet_versions`。

Word（4）：`read_word`、`inspect_word`、`search_word`、`write_word`。

文件与文本：`list_directory`、`read_text_file`、`write_text_file`、`edit_text_file`、`copy_file`、`rename_file`、`delete_file`、`offer_download`、`read_image`。

执行与协作：`run_code`、`run_shell`、`skill`、`manage_skills`、`delegate`、`list_subagents`、`ask_user`、`sleep`、`memory_read_topic`、`memory_save`、`introspect_capability`。

### 3.4 提示词分层与技能

- 统一提示词链：`prompts/*.md` → `PromptComposer` / `PromptRegistry` → `build_stable_system_prompt` → `RequestEnvelope`。
- Native 写入原则 / 策略压缩（历史测量 2998 → 954 token）；当前 write 场景完整 system 实测 1572 token，`tools` JSON 按需加载。目标不是无差别砍 token，schema 必需的参数信息保留。
- 提示词预算与合同脚本：`scripts/check_prompt_budgets.py`、`scripts/check_prompt_contracts.py`，CI 强制执行。
- 内置技能 9 个：`data_basic`、`chart_basic`、`format_basic`、`sheet_ops`、`file_ops`、`excel_code_runner`、`run_code_templates`、`word_basic`、`word_code_runner`。
- Skillpack 支持项目级 / 用户级隔离、版本缓存、并行安装、增量加载、Hook 依赖与 MCP 依赖。

### 3.5 上下文、压缩与记忆

- `context_budget` / `compaction` / `compaction_pruner`：按 token 预算自动压缩，压缩前保留结构化进度；`/compact` 与 `/handoff` API 可显式触发。
- 持久记忆：`memory_read_topic` / `memory_save`、分类与条目管理、会话内提取（`/memory/extract`）、维护与裁剪；
- 会话摘要按需开启（`SESSION_SUMMARY_ENABLED` / 最小轮数），不默认产生额外模型调用。
- `session_log` / `trace`：会话日志、有界 trace span、恢复快照，支撑中断恢复与问题定位。
- `settings_runtime` / `settings_persist`：产品设置统一走主库与运行期覆盖层，去除进程环境隐式覆盖。

---

## 四、工作簿能力

### 4.1 表格读取与分析

- 区域读写保留真实坐标：非 A 列、并集 selection、合并区域、标题行、分页、版本检查。
- `analyze_spreadsheet`：筛选投影、profile、quality、日期分组、透视、同簿只读 join；右表版本与 provenance 返回 `join.source`。
- `compare_spreadsheets`：position / key 比较、真实坐标、公式文本、表头交换、扫描统计与差异样本上限分离、`formula_status` 与 coverage。
- CSV / TSV 与 XLSX 对齐：header 检测、搜索、写回、窗口感知、编码 / 分隔符处理。

### 4.2 表格写入与格式化

- 普通写入与 selection 共用合并冲突检查；`values=[[None]]` 能真正清空单元格；显式字符串不被 `_coerce_value` 改写。
- 区域 copy 平移相对公式并复制样式、批注、链接；无法维护引用 / 对象时显式拒绝。
- `format_spreadsheet`：fill / border / alignment / 尺寸可自省；局部 font / alignment 保留未指定属性；主题色 / indexed color 解析。
- 合并非锚点有值默认拒绝，`allow_data_loss=true` 才允许舍弃。
- 拆分 `split_spreadsheet`：分组身份与文件名分离、TSV 编码、公式 / 样式保留、批次事务、失败恢复。
- 对象与图表：批次一次提交，尺寸单位为厘米，非法尺寸拒绝，结果逐对象返回。
- 公式追踪：绝对引用规范化、target 限单格、depth 1–5、impact 明确为静态直接依赖。

### 4.3 版本管理

- `manage_spreadsheet_versions`：checkpoint 校验 `expected_version`、restore / delete / list、lineage、`exists_after`。
- 只读会话可以 `list`，但 `restore` / `checkpoint` 被策略拒绝（`write_effect_for_call`）。
- 文件历史 API：`/revisions`、`/revisions/preview`、`/revisions/restore`、`/revisions/delete`。

---

## 五、Word 文档（1.7.2 无此能力）

- 后端 `word_tools.py`：读取、检查、搜索、写入 `.docx`；标题、段落、表格、书签、页眉页脚识别。
- `write_word` 走 `commit_bytes` 全有或全无，支持 source version、输出文件、填充书签与公式未缓存提示。
- API：`/files/word`、`/files/word/snapshot`、`/files/word/write`。
- 前端：`UniverDoc` 编辑器、`WordSidePanel`、`WordFullView`、`word-store`、文件类型图标与路由。
- 技能：`word_basic`、`word_code_runner`；调度提示见 `docs/word-agent-dispatch-prompts.md`。

---

## 六、引用图与跨文件关系

- 新增 `excelmanus/reference_graph/`：公式引用提取、工作表级 Tier 1 扫描、单元格级 Tier 2 深度解析（BFS，上限 5 层）。
- 两级缓存 + `contextvars` 会话隔离，支持并发的多会话场景。
- 跨文件关系扫描：上传触发、关系 API `/api/v1/files/relationships`。
- 引用提示注入窗口感知与系统提示 panorama；模型入口是 `edit_spreadsheet` 的 `trace` 模式（`map` / `trace` / `impact`），实现为 `get_reference_map`、`trace_references`、`get_impact_analysis`。

---

## 七、模型、凭证与决策服务

### 7.1 模型档案

- 产品设置只走主数据库：模型档案 `model_profiles`，其他设置 `config_kv`；`.env` / `config.env` 不再是产品配置源。
- 模型切换、默认模型、能力探测、上下文窗口推断、跨标签页同步。
- 当前旗舰默认：`gpt-6-astra`、`claude-sonnet-5`、`gemini-3.8-flash`、`deepseek-flash`、`qwen3.8-max`、`glm-5.3`、`kimi-k3`、`MiniMax-M3`、`grok-4.6`、`doubao-seed-2.1-pro`。
- 未识别模型默认上下文预算 256,000 token。

### 7.2 认证与号池

- Codex OAuth（浏览器 / 设备码）、Google Gemini OAuth PKCE、Anthropic / OpenAI 兼容协议。
- 凭证池：账号、订阅、健康检查、自动轮换、熔断、失败信号回写。
- 能力探测：异步 job、批量探测、SSE 事件；API Key 使用 Fernet 加密，密钥位于 `$EXCELMANUS_HOME/.secret_key`。
- 工具 Schema 适配层：不同提供商 tool schema 差异统一。

### 7.3 Jev 决策层（可选）

- `excelmanus/system_one/`：Budget、Circuit Breaker、Host、Policy、Trace、Packs、Runtime。
- 决策面：回合暴露、模式建议、审批建议、结果整理、UI 面建议、失败恢复。
- 标定与门禁：shadow / inert enforce / 人工标定签字；决策只作为 `DecisionEnvelope` 输入，确定性执行器二次检查。
- 可选 extra `system-one = ["typesafe-sdk"]`；未启用时不改变主流程。

### 7.4 内置搜索

- 内置 MCP 搜索引擎 Exa / Tavily / Brave：总开关 `exa_search_enabled`，默认引擎 `search_default_provider`（exa / tavily / brave）。
- 缺少所选引擎密钥时降级回 Exa 并给出警告；MCP 工具按 `always` / `search` / `dev_docs` 作用域路由，搜索类工具进入只读集合。
- 搜索配置在 Web「设置 → 插件 → 内置搜索引擎」维护，密钥走主数据库。

---

## 八、安全与访问边界

- 默认绑定 `127.0.0.1`；非 loopback 监听必须设置 `EXCELMANUS_MANAGE_TOKEN`（至少 16 字符），否则拒绝启动。
- 源码启动脚本（sh / ps1 / bat）统一走 `excelmanus.api:main`，不再绕过令牌校验；前端默认只监听本机。
- 前端代理会透传 API，远程部署须同时保护前端与后端，详见运维手册。
- `FileAccessGuard` 统一路径：上传名、spec / reveal / download、Excel MCP 绝对路径、工作区文件服务。
- SSRF 防护：拒绝私网 / loopback / link-local / 元数据地址，每个重定向 hop 复检，最多 5 跳，流式大小上限。
- 上传：文件名净化、100 MB 大小上限、空文件拒绝、`.xls` / `.xlsb` 自动转换并登记原路径别名。
- 敏感文件命名空间（`.env`、密钥、`.excelmanus` 内部工件）拒绝读取；工作区外仅放行解释器与依赖库。
- 工具调用审批：AST 代码策略、GREEN / YELLOW / RED 分级、Hook ASK、审批状态机、死亡 / 超时 / 取消收尾。
- MCP：stdio 命令、环境、作用域、`autoApprove` 与只读标记分离；密钥字段不返回列表接口。

---

## 九、API 与数据层

- `api.py` 拆分为路由模块：chat / config / files / mcp / rules / sessions / skills / system / version / workspace / workspaces。
- 运行时状态统一 `api_app_state`，移除 `from excelmanus.api import _config` 反向依赖。
- SSE：`/chat/stream`、`/chat/subscribe`、turn、subagent、tool-call、approval、question、diff、preview、download、failure guidance 等事件。
- 会话：创建 / 复用 / 标题 / 导出 / 压缩 / 消息分页 / 状态 / 中断恢复 / 后台任务。
- 工具调用取消：`/tool-calls/{execution_id}/cancel`，按会话鉴权，不启动模型。
- SQLite：WAL + backup API；`config.env` 文件锁；聊天消息按 id 去重。
- 数据库迁移、用户数据搬迁、版本清单、备份 / 安装 / 升级 API。
- 部署 API：canary / blue-green / 回滚 / 锁状态 / 历史日志（保留自 1.7.x，桌面模式禁用）。

---

## 十、工程、CI 与打包

- `uv.lock` 入库；`uv sync --frozen` 复现依赖。
- Python CI：完整后端回归、提示词预算 / 合同、pyright、前端 typecheck / lint / vitest / build、生产依赖审计。
- 新增 `.github/workflows/security-secrets.yml`（gitleaks）。
- 桌面 CI：macOS / Windows 构建、Resources smoke、安装 / 升级 / 卸载、签名校验。
- 前端依赖升级：Next.js 16.3.5、React 19、Univer 0.15.5、Tailwind 4、ESLint 9。
- 安全修复：升级 Next.js 补丁、Univer 内 nanoid、brace-expansion、Vitest / Vite / Hono 等开发依赖。
- Python 打包：wheel / sdist 不再包含 `__pycache__` / `.pyc` / `.DS_Store`。
- 仓库忽略规则更新：运行产物、本机 profile、构建输出、`.em-lock` 不入库；发布说明与提示词检查脚本显式入库。
- 渲染：`theme.py` 提供终端配色与符号常量，`render_utils.py` 收敛工作簿/表格渲染工具；图表与图片导出由内置 `chart_basic` 技能提供模板。
- Benchmark：`bench/` 新增用例套件、契约检查、评分与报告（realistic / prompt_contract / experiential / Jev 标定），生成夹具不入库。
- 统一版本源：`pyproject.toml` 为唯一版本来源，`excelmanus.__version__`、Web、Desktop 三处同步读取。

---

## 十一、验证结果（本机 macOS arm64）

| 项目 | 结果 |
| --- | --- |
| Python 全量回归 | Windows 基线需由当前依赖环境重新生成；历史 macOS 数字不作为发布证据 |
| 前端单元测试 | 74 files / 533 tests passed |
| 前端生产构建 | `next build --webpack` 通过 |
| TypeScript | `tsc --noEmit` 无错误 |
| ESLint | 0 error（34 warning） |
| Pyright | 0 error |
| Python 依赖审计 | `pip-audit` 无已知漏洞 |
| 前端依赖审计 | `npm audit` 0 vulnerabilities |
| Python 打包 | wheel / sdist 构建通过，无缓存残留 |
| wheel 干净环境 | 安装通过，版本、提示词、技能、32 工具、`create_app` 可用 |
| 工作簿浏览器验收 | 16 个场景通过（加载壳、首屏、编辑、冲突、切表、暗刷新、删除、迟到响应） |
| 对比基线 | `30c7fae4`（1.7.2 源码点） |

以上数字来自本机 macOS arm64 的一次完整验收运行；后端与前端测试数会随并行改动继续增长，发布前应重新跑一遍并同步本表。

---

## 十二、升级与兼容性

1. **先备份**：主数据库、与之配套的 `.secret_key`、所有工作区文件与 `.excelmanus/revisions/`。不要让新旧服务同时打开同一 profile。
2. **配置迁移**：产品设置改为主库 `config_kv` / `model_profiles`；旧 `.env` / `config.env` 不自动导入，请在设置页重新确认模型与 API Key。
3. **用户数据**：旧 `users/` 目录不会自动合并，需手工迁移数据库、聊天记录与记忆；默认工作区改为 `EXCELMANUS_HOME/data`。
4. **渠道与 CLI**：Telegram / QQ / 飞书渠道和旧 CLI 入口在 1.8.0 不再提供；如仍需要，请留在 1.7.2。
5. **Docker**：Docker / 沙盒部署文件移除；1.8.0 以本机进程 + 路径 / 权限 / 审批为执行边界，远程部署须配置管理令牌并保护反向代理。
6. **桌面升级**：安装新版 DMG / NSIS，保留 profile 即可；桌面不使用源码 Git 更新入口，也不会自动合并源码版 `~/.excelmanus`。
7. **API 兼容**：新增 `/mutations/{operation_id}`；旧的 `/operations/{approval_id}` 保持为操作历史详情。使用新事务收据的调用方需改用 mutations 路径。
8. **只读行为**：只读 / 计划模式拒绝写入类工具；恢复修订必须携带 `expected_version`。

---

## 十三、已知边界

- 没有 Microsoft Excel 重算引擎；公式缓存、复杂对象、跨表依赖、图表更新 / 删除、命名范围、自动筛选、打印设置、图片写入仍不等价于 Excel 桌面端。
- `.xls` / `.xlsb` 上传会转换为 `.xlsx`；格式承载能力有损，重要文件保留原件。
- 真实模型质量、OAuth 服务可用性、远程 MCP 生命周期未纳入自动化验收。
- Windows 安装器、Job Object、ACL、Authenticode 需要 Windows CI / 实机验收；本机验证的是 macOS arm64。
- macOS 使用 ad-hoc 签名，正式分发仍需 Developer ID + 公证。
- 源码检查全绿不代表所有平台安装包可直接发行；发布资产以实际构建与签名结果为准。

更细的部署、配置与桌面说明见 [配置参考](docs/configuration.md)、[运维手册](docs/ops-manual.md)、[桌面版文档](desktop/README.md)。
