# 技术栈与构建

## 语言与运行时

- Python 3.10+
- 使用 pyproject.toml 管理依赖，setuptools 构建

## 核心依赖

| 库 | 用途 |
|---|---|
| openai | OpenAI SDK，LLM 调用（Responses API / Chat Completions） |
| fastapi | REST API 服务框架 |
| uvicorn | ASGI 服务器 |
| pandas | 数据读取、分析、转换 |
| openpyxl | Excel 文件读写、格式化、图表 |
| matplotlib | 图表生成（柱状图、折线图、饼图、散点图、雷达图） |
| pydantic | 数据模型与参数校验 |
| rich | CLI 终端美化输出 |
| python-dotenv | .env 文件加载 |

## 开发依赖

| 库 | 用途 |
|---|---|
| pytest | 单元测试框架 |
| pytest-asyncio | 异步测试支持 |
| hypothesis | 属性测试（Property-Based Testing） |
| httpx | HTTP 测试客户端 |

## LLM 配置

默认使用阿里云 DashScope 兼容接口：
- `EXCELMANUS_BASE_URL`: `https://dashscope.aliyuncs.com/compatible-mode/v1`
- `EXCELMANUS_MODEL`: `qwen-max-latest`
- API Key 通过环境变量 `EXCELMANUS_API_KEY` 或 `.env` 文件设置

## 常用命令

```bash
# 安装依赖（含开发依赖）
pip install -e ".[dev]"

# 启动 CLI 交互模式
excelmanus
# 或
python -m excelmanus

# 启动 API 服务
excelmanus-api
# 或
python -m excelmanus.api

# 运行测试
pytest

# 运行属性测试（含 hypothesis）
pytest tests/test_pbt_*.py
```

## 代码规范

- 注释与文档字符串使用中文
- 变量名、函数名使用英文 snake_case
- 类名使用英文 PascalCase
- 所有用户可见的输出文本使用中文
