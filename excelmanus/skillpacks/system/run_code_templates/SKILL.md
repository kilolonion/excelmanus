---
name: run_code_templates
description: run_code 常用代码模板库，覆盖数据写入、格式化、图表、跨表操作、VBA 替代、文件恢复等场景。直接复制修改参数即可。
file_patterns:
  - "*.xlsx"
  - "*.xlsm"
  - "*.xls"
resources:
  - references/write_patterns.md
  - references/format_patterns.md
  - references/analysis_patterns.md
  - references/advanced_patterns.md
version: "1.0.0"
---
run_code 代码模板库。按场景分为四个参考文档，激活后自动加载。

使用流程：
1. 从参考文档中找到最匹配的模板
2. 复制模板，替换文件名、列名、参数
3. 添加顶层 try/except 异常处理
4. 写入后用 read_excel 独立回读验证

关键约束：
- 禁止 sys.exit()/exit()/os._exit()
- 禁止 exec()/eval()
- 写入后必须 print 关键验证数据到 stdout
- 错误信息 print 到 stderr，脚本正常结束
