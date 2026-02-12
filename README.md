# ExcelManus v2

基于大语言模型的 Excel 智能代理框架。通过自然语言描述 Excel 任务，系统自动拆解意图并调用工具完成操作。

支持三种运行模式：

- **CLI 模式** — 终端对话式交互
- **API 模式** — REST API 服务，供程序化调用
- **MCP 模式** — MCP Server，供 Kiro、Claude Desktop 等 AI 客户端调用

## 安装

```bash
# 从源码安装
pip install .

# 开发模式（含测试依赖）
pip install -e ".[dev]"
```

要求 Python >= 3.10。

## 配置

通过环境变量或项目根目录的 `.env` 文件配置。优先级：环境变量 > `.env` > 默认值。

| 环境变量 | 说明 | 默认值 |
|---|---|---|
| `EXCELMANUS_API_KEY` | LLM API Key（**必填**） | — |
| `EXCELMANUS_BASE_URL` | LLM API 地址 | `https://dashscope.aliyuncs.com/compatible-mode/v1` |
| `EXCELMANUS_MODEL` | 模型名称 | `qwen-max-latest` |
| `EXCELMANUS_MAX_ITERATIONS` | Agent 最大迭代轮数 | `20` |
| `EXCELMANUS_MAX_CONSECUTIVE_FAILURES` | 连续失败熔断阈值 | `3` |
| `EXCELMANUS_SESSION_TTL_SECONDS` | API 会话空闲超时（秒） | `1800` |
| `EXCELMANUS_MAX_SESSIONS` | API 最大并发会话数 | `1000` |
| `EXCELMANUS_WORKSPACE_ROOT` | 文件访问白名单根目录 | `.`（当前工作目录） |
| `EXCELMANUS_LOG_LEVEL` | 日志级别（DEBUG/INFO/WARNING/ERROR） | `INFO` |

`.env` 文件示例：

```env
EXCELMANUS_API_KEY=sk-your-api-key
EXCELMANUS_MODEL=qwen-max-latest
EXCELMANUS_WORKSPACE_ROOT=/data/excel-files
```

## 使用方式

### CLI 模式

```bash
excelmanus
# 或
python -m excelmanus
```

启动后进入交互式对话，支持以下命令：

| 命令 | 说明 |
|---|---|
| `/help` | 显示帮助信息 |
| `/history` | 查看对话历史 |
| `/clear` | 清除对话历史 |
| `exit` / `quit` / `Ctrl+C` | 退出 |

### API 模式

```bash
excelmanus-api
```

容器化部署：

```bash
docker build -t excelmanus .
docker run -p 8000:8000 -e EXCELMANUS_API_KEY=sk-your-key excelmanus
```

启动后访问 `http://localhost:8000/docs` 查看 OpenAPI 文档。

API 端点：

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/v1/chat` | 发送消息，返回 `session_id` 和 `reply` |
| DELETE | `/api/v1/sessions/{session_id}` | 删除会话 |
| GET | `/api/v1/health` | 健康检查，返回版本和已加载 Skill |

请求示例：

```bash
# 新建会话
curl -X POST http://localhost:8000/api/v1/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "读取 sales.xlsx 的前 10 行"}'

# 复用会话
curl -X POST http://localhost:8000/api/v1/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "按销售额降序排列", "session_id": "your-session-id"}'
```

### MCP 模式

```bash
excelmanus-mcp
```

通过 stdio 传输与 MCP 客户端通信。在 MCP 客户端配置中添加：

```json
{
  "mcpServers": {
    "excelmanus": {
      "command": "excelmanus-mcp"
    }
  }
}
```

MCP 模式直接暴露 Skill 工具，不经过 Agent Engine，由外部 AI 客户端自行编排调用。

## Skill 扩展指南

Skill 是按职责分组的工具集合。系统启动时自动扫描 `excelmanus/skills/` 下所有符合约定的模块。

### 创建自定义 Skill

在 `excelmanus/skills/` 下创建 Python 模块，导出以下三个成员：

```python
# excelmanus/skills/my_skill.py

from excelmanus.skills import ToolDef

SKILL_NAME = "my_skill"
SKILL_DESCRIPTION = "自定义工具集描述"


def get_tools() -> list[ToolDef]:
    """返回该 Skill 包含的工具列表。"""
    return [
        ToolDef(
            name="my_tool",
            description="工具功能描述",
            input_schema={
                "type": "object",
                "properties": {
                    "param1": {"type": "string", "description": "参数说明"},
                },
                "required": ["param1"],
            },
            func=my_tool_func,
        ),
    ]


def my_tool_func(param1: str) -> str:
    """工具实现。"""
    return f"处理结果: {param1}"
```

模块约定：

- `SKILL_NAME`：Skill 名称，全局唯一
- `SKILL_DESCRIPTION`：Skill 描述
- `get_tools()`：返回 `list[ToolDef]`，每个 `ToolDef` 包含 `name`、`description`、`input_schema`（JSON Schema）和 `func`

系统会在启动时通过 `SkillRegistry.auto_discover()` 自动加载，无需手动注册。

## 安全边界

### WORKSPACE_ROOT

所有文件读写操作被限制在 `WORKSPACE_ROOT` 目录内。通过环境变量 `EXCELMANUS_WORKSPACE_ROOT` 配置，默认为当前工作目录。

### 路径穿越防护

系统对所有文件路径执行以下校验：

1. 拒绝包含 `..` 的路径
2. 规范化路径后校验是否位于 `WORKSPACE_ROOT` 之下
3. 检测符号链接目标是否越界

违规操作会抛出 `SecurityViolationError`，不执行任何文件操作。

### 日志脱敏

日志系统对以下敏感信息自动脱敏：API Key、Authorization Token、Cookie、绝对本地路径。

## 开发

```bash
# 安装开发依赖
pip install -e ".[dev]"

# 运行测试
pytest

# 运行测试（带覆盖率）
pytest --cov=excelmanus
```

### 项目结构

```
excelmanus/
├── __init__.py          # 版本定义
├── __main__.py          # python -m excelmanus 入口
├── config.py            # 配置管理
├── logger.py            # 日志与脱敏
├── security.py          # 文件访问守卫
├── memory.py            # 对话记忆
├── engine.py            # Agent 引擎（Tool Calling 循环）
├── session.py           # 会话管理
├── cli.py               # CLI 交互界面
├── api.py               # FastAPI REST API
├── mcp_server.py        # MCP Server
└── skills/              # Skill 模块
    ├── __init__.py      # ToolDef、SkillRegistry
    ├── data_skill.py    # 数据操作（读写、分析、过滤、转换）
    ├── chart_skill.py   # 可视化（柱状图、折线图、饼图等）
    └── format_skill.py  # 格式化（单元格样式、列宽）
```

## 许可证

MIT
