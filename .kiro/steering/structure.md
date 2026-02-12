# 项目结构

```
excelmanus/                    # 项目根目录
├── pyproject.toml             # 项目配置与依赖清单
├── README.md                  # 项目说明文档
├── Dockerfile                 # Docker 构建文件
└── excelmanus/                # Python 包（源代码）
    ├── __init__.py            # 包初始化，定义版本号（3.0.0）
    ├── __main__.py            # python -m excelmanus 入口
    ├── engine.py              # AgentEngine — Tool Calling 循环核心引擎
    ├── config.py              # ExcelManusConfig 全局配置（环境变量 + .env）
    ├── api.py                 # FastAPI REST API 服务（含 SSE 流式）
    ├── cli.py                 # Rich CLI 交互界面
    ├── session.py             # 会话管理器（多会话、TTL、并发控制）
    ├── memory.py              # 对话记忆管理
    ├── events.py              # 事件类型与回调定义
    ├── logger.py              # 日志配置
    ├── renderer.py            # CLI 输出渲染
    ├── security.py            # 安全相关
    ├── tools/                 # 工具层
    │   ├── __init__.py        # ToolRegistry 导出
    │   ├── registry.py        # ToolRegistry — 工具注册、schema 输出、调用执行
    │   ├── file_tools.py      # 文件操作工具
    │   ├── data_tools.py      # 数据分析工具
    │   ├── chart_tools.py     # 图表生成工具
    │   └── format_tools.py    # 格式化工具
    ├── skillpacks/            # Skillpack 层
    │   ├── __init__.py        # SkillpackLoader/SkillRouter 导出
    │   ├── loader.py          # SkillpackLoader — SKILL.md 解析与加载
    │   ├── router.py          # SkillRouter — 预筛选、快速路径、LLM 确认
    │   ├── models.py          # Skillpack/SkillMatchResult 数据模型
    │   └── system/            # 内置 Skillpack（SKILL.md 文件）
    └── skills/                # 旧版兼容层（已废弃，仅保留迁移提示）
        └── __init__.py
```

## 架构要点

- `AgentEngine`（engine.py）是核心引擎，驱动 LLM 与工具之间的 Tool Calling 循环
- Tools + Skillpacks 双层架构：ToolRegistry 管理工具定义与执行，SkillpackLoader/Router 管理技能包路由
- `ToolRegistry`（tools/registry.py）负责工具注册、OpenAI schema 输出、调用执行与授权检查
- `SkillRouter`（skillpacks/router.py）负责根据用户消息匹配合适的 Skillpack
- 配置通过环境变量和 .env 文件加载，`ExcelManusConfig` 为 frozen dataclass
