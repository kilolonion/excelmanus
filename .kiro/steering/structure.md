# 项目结构

```
excelmanus/                  # 主包
├── __main__.py              # python -m excelmanus 入口
├── cli.py                   # CLI 模式入口与命令处理
├── api.py                   # FastAPI REST/SSE 服务
├── engine.py                # 核心 Agent 引擎（LLM 循环、工具调度）
├── session.py               # 会话管理（API 多会话、TTL 清理）
├── config.py                # 配置加载（环境变量 + .env）
├── events.py                # 事件总线
├── renderer.py              # CLI 输出渲染（Rich）
├── logger.py                # 日志
├── approval.py              # Accept 门禁与审计
├── plan_mode.py             # Plan 模式（草案生成与审批）
├── task_list.py             # 任务列表管理
├── question_flow.py         # 用户交互问答流
├── output_guard.py          # 输出安全过滤
├── memory.py                # 持久记忆核心
├── memory_models.py         # 记忆数据模型
├── memory_extractor.py      # 记忆提取
├── persistent_memory.py     # 记忆持久化
│
├── tools/                   # Tools 层 — 基础能力
│   ├── registry.py          # 工具注册中心
│   ├── policy.py            # 工具策略（Tier 分层、权限）
│   ├── cell_tools.py        # 单元格读写
│   ├── sheet_tools.py       # 工作表操作
│   ├── worksheet_tools.py   # 工作表高级操作
│   ├── file_tools.py        # 文件操作
│   ├── data_tools.py        # 数据分析/转换
│   ├── chart_tools.py       # 图表
│   ├── format_tools.py      # 基础格式
│   ├── advanced_format_tools.py  # 高级格式（条件格式等）
│   ├── code_tools.py        # 代码执行（run_code）
│   ├── shell_tools.py       # Shell 命令
│   ├── memory_tools.py      # 记忆工具
│   ├── skill_tools.py       # Skillpack 管理工具
│   └── task_tools.py        # 任务工具
│
├── skillpacks/              # Skillpacks 层 — 策略编排
│   ├── system/              # 内置 Skillpacks（SKILL.md 驱动）
│   │   ├── general_excel/
│   │   ├── data_basic/
│   │   ├── chart_basic/
│   │   ├── format_basic/
│   │   ├── file_ops/
│   │   ├── sheet_ops/
│   │   └── excel_code_runner/
│   ├── models.py            # Skillpack 数据模型
│   ├── loader.py            # Skillpack 加载与发现
│   ├── router.py            # 路由（slash_direct / fallback）
│   ├── manager.py           # Skillpack 生命周期管理
│   ├── frontmatter.py       # SKILL.md frontmatter 解析
│   ├── arguments.py         # Skillpack 参数处理
│   └── context_builder.py   # 上下文构建
│
├── hooks/                   # Hook 系统
│   ├── models.py            # Hook 数据模型
│   ├── runner.py            # Hook 执行器
│   ├── handlers.py          # Hook 处理器
│   └── matcher.py           # 事件匹配
│
├── mcp/                     # MCP 客户端集成
│   ├── client.py            # MCP 客户端
│   ├── config.py            # MCP 配置解析
│   ├── manager.py           # MCP 连接管理
│   └── processes.py         # MCP 进程管理
│
├── providers/               # LLM Provider 适配
│   ├── claude.py
│   ├── gemini.py
│   └── openai_responses.py
│
├── security/                # 安全模块
│   ├── guard.py             # 路径穿越/符号链接防护
│   └── sanitizer.py         # 输出脱敏
│
├── subagent/                # Subagent 子代理
│   ├── executor.py          # Subagent 执行器
│   ├── registry.py          # Subagent 注册
│   ├── models.py            # Subagent 数据模型
│   ├── builtin.py           # 内置 Subagent
│   └── tool_filter.py       # 工具过滤
│
└── window_perception/       # 窗口感知层
    ├── manager.py           # 窗口生命周期管理
    ├── models.py            # 窗口数据模型
    ├── renderer.py          # 窗口渲染
    ├── budget.py            # Token 预算分配
    ├── rules.py             # 确定性规则引擎
    ├── advisor.py           # 生命周期顾问
    ├── advisor_context.py   # 顾问上下文
    ├── small_model.py       # 小模型异步调用
    ├── extractor.py         # 数据提取
    └── perception_details.py

tests/                       # 测试目录
├── conftest.py              # pytest fixtures
├── fixtures/                # 测试数据
├── test_pbt_*.py            # 属性基测试（Hypothesis）
└── test_*.py                # 单元/集成测试

docs/                        # 文档
├── skillpack_protocol.md    # Skillpack 协议 SSOT
└── review_checklist.md      # 代码审查清单

scripts/                     # 脚本
├── mcp/                     # MCP 启动脚本
├── security/                # 安全扫描脚本
└── migrate_skills_to_standard.py

tasks/                       # 任务管理目录（RIPER-5 工作流）
```

## 关键约定
- 测试文件与源文件同名，前缀 `test_`，放在 `tests/` 目录
- 属性基测试文件命名：`test_pbt_*.py`
- Skillpack 以目录形式存在，每个包含 `SKILL.md`
- 工具注册通过 `tools/registry.py` 统一管理
- 安全边界：所有文件操作受 `WORKSPACE_ROOT` 限制
