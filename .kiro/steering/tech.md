# 技术栈与构建

## 语言与运行时

- Python 3.8+
- 无构建系统，直接 `python run.py` 启动

## 核心依赖

| 库 | 用途 |
|---|---|
| langchain / langchain-openai | Agent 框架、ReAct 推理、LLM 调用 |
| pandas | 数据读取、分析、转换 |
| openpyxl | Excel 文件读写、格式化、图表 |
| matplotlib | 图表生成（柱状图、折线图、饼图、散点图、雷达图） |
| pydantic | 工具输入参数校验（Schema） |
| numpy | 数值计算辅助 |

## LLM 配置

默认使用阿里云 DashScope 兼容接口：
- `BASE_URL`: `https://dashscope.aliyuncs.com/compatible-mode/v1`
- `MODEL`: `qwen-max-latest`
- API Key 通过环境变量 `API_KEY` 或代码中 `set_env_config()` 设置

## 常用命令

```bash
# 安装依赖
pip install -r excelagent/requirements.txt

# 启动示例应用
python excelagent/run.py
```

## 代码规范

- 注释与文档字符串使用中文
- 变量名、函数名使用英文 snake_case
- 类名使用英文 PascalCase
- 所有用户可见的输出文本使用中文
