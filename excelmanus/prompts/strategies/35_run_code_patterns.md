---
name: run_code_patterns
version: "2.0.0"
priority: 35
layer: strategy
max_tokens: 200
conditions:
  write_hint: "may_write"
---
## run_code 使用原则

`run_code` 是主力写入工具。遵循以下原则：

1. **所有写入操作通过 run_code 完成**（pandas/openpyxl）
2. **必须包含顶层 try/except 异常处理**，print 到 stderr，禁止 `sys.exit()`/`exit()`/`os._exit()`
3. **禁止** `exec()`/`eval()`
4. **写入后在 stdout 打印关键验证数据**（行数、列名、抽样值）
5. **写入后必须用 `read_excel` 独立回读验证**，不可仅依赖 print 输出。遵循 Think-Act：调用 read_excel 前用 1 句说明「验证什么、预期结果」；读完后用 1 句总结「实际是否一致」，再进入最终汇报或修正

### 常用模板索引

需要代码模板时，激活 `run_code_templates` 技能获取完整代码：

| 类别 | 覆盖模板 |
|------|---------|
| 数据写入 | 写入数据、单元格写入、插入行、条件删除行 |
| 格式样式 | 格式化样式、批量格式化、合并单元格、条件格式 |
| 图表与布局 | 图表创建、打印设置、数据验证（下拉列表） |
| 分析统计 | 描述性统计、分组聚合、跨表键匹配 |
| 跨表操作 | 跨表匹配写回（VLOOKUP 等价）、跨 Sheet 查找填充 |
| Sheet 管理 | 新建/复制/重命名/删除 Sheet |
| 智能读取 | smart_read_excel（合并标题行）、隐私脱敏输出 |
| VBA 替代 | 遍历条件写入、按区块填充、openpyxl 不支持的操作说明 |
| 恢复与复刻 | 文件损坏恢复流程、图片表格复刻工作流 |
| 文件读取 | 读取文本文件 |

### 文件损坏快速恢复

当 openpyxl 打开失败（`KeyError`/`BadZipFile` 等）：
1. 用 `copy_file` 从参考文件复制工作副本到 `outputs/`（禁止 `shutil.copy`）
2. 在副本上清除目标区域，恢复初始状态
3. 在副本上执行写入逻辑
