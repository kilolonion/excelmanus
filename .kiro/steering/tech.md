# 技术栈与构建

## 语言与运行时
- Python >= 3.10
- 构建系统：setuptools + wheel（`pyproject.toml`）
- 包管理：uv（`uv.lock`），兼容 pip

## 核心依赖
- `openai` — LLM API 调用
- `pandas` + `openpyxl` — Excel 数据处理
- `matplotlib` — 图表生成
- `fastapi` + `uvicorn` — API 服务
- `rich` — CLI 终端渲染
- `pydantic` — 数据模型与校验
- `prompt_toolkit` — CLI 交互与补全
- `tiktoken` — Token 计数
- `mcp` — Model Context Protocol 客户端
- `httpx` — HTTP 客户端
- `PyYAML` — YAML/frontmatter 解析
- `python-dotenv` — 环境变量加载

## 开发依赖
- `pytest` + `pytest-asyncio` — 测试框架（asyncio_mode = auto）
- `hypothesis` — 属性基测试（Property-Based Testing）

## 常用命令

```bash
# 安装（开发模式）
pip install -e ".[dev]"

# 运行测试
pytest

# 运行单个测试文件
pytest tests/test_engine.py -q

# 运行 CLI
excelmanus
# 或
python -m excelmanus

# 运行 API 服务
excelmanus-api

# 安全扫描（pre-commit）
scripts/security/scan_secrets.sh
```

## 配置
- 环境变量优先级：环境变量 > `.env` > 默认值
- 所有配置项以 `EXCELMANUS_` 为前缀
- MCP 配置：项目根目录 `mcp.json`
- pytest 配置在 `pyproject.toml` 的 `[tool.pytest.ini_options]` 中
