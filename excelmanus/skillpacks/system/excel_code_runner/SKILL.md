---
name: excel_code_runner
description: 通过生成并运行 Python 脚本处理大体量 Excel 文件，适用于 read_excel 全量读取成本高、需要分批处理或复杂计算的任务。
allowed_tools:
  - write_text_file
  - run_code
  - read_excel
  - analyze_data
  - filter_data
  - transform_data
  - write_excel
  - read_text_file
  - search_files
  - get_file_info
  - list_directory
triggers:
  - 代码
  - 脚本
  - 运行
  - python
  - 大文件
  - 分块
  - 批处理
  - profile
file_patterns:
  - "*.xlsx"
  - "*.xlsm"
  - "*.xls"
resources:
  - references/largefile_code_workflow.md
priority: 9
version: "1.0.0"
---
优先采用“探查 -> 写脚本 -> 执行 -> 验证”的四步流程：

1. 探查阶段
- 先确认目标文件路径和 sheet 信息，避免盲目全量读取。
- 先用 `get_file_info` 查看 `size_bytes`，超过阈值时保持“子上下文只读探索 + 摘要返回”。
- 必要时用 `read_excel(max_rows=200)` 获取列名与样本数据。

2. 写脚本阶段
- 使用 `write_text_file` 生成 `scripts/temp/*.py` 脚本。
- 脚本内优先使用 `pandas.read_excel(usecols=..., nrows=...)` 做范围控制。
- 大量数据处理结果落盘到 `outputs/`，不要把全量数据直接回传。

3. 执行阶段
- 使用 `run_python_script` 执行脚本，默认 `python_command=auto`。
- 失败时优先查看 `stderr_tail`，修复后再次写入并执行。

4. 验证阶段
- 核对输出文件是否存在、行数是否符合预期。
- 向用户返回结论、产物路径、下一步建议。

关键约束：
- 禁止覆盖用户关键文件，默认输出到新文件。
- 处理超大文件时，先小样本验证再放量执行。
