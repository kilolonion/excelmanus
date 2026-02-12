# 项目结构

```
excelagent/                    # 项目根目录
├── run.py                     # 入口脚本，启动示例应用
├── requirements.txt           # Python 依赖清单
├── README.md                  # 项目说明文档
├── 销售数据.xlsx               # 示例数据文件
└── excelagent/                # Python 包（源代码）
    ├── __init__.py            # 包初始化，定义版本号
    ├── core/
    │   └── agent.py           # ExcelAgent 类 — ReAct 代理核心逻辑
    ├── tools/
    │   └── excel_tools.py     # Excel 操作工具集（读写、分析、图表、格式化等）
    ├── utils/
    │   ├── config.py          # API 密钥与模型配置管理
    │   └── tool_wrapper.py    # LangChain 工具参数 JSON 解析包装器
    └── examples/
        └── app.py             # 交互式示例应用
```

## 架构要点

- `ExcelAgent`（core/agent.py）是唯一的代理类，内部创建 LangChain `AgentExecutor`
- `excel_tools.py` 导出 `excel_tools` 列表，包含所有 LangChain `StructuredTool` 实例
- 工具函数直接操作 pandas DataFrame 和 openpyxl Workbook，无中间抽象层
- `tool_wrapper.py` 处理 Agent 传入的 JSON 字符串参数到函数关键字参数的转换
- 配置模块在导入时自动调用 `set_env_config()` 设置环境变量
