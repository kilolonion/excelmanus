---
name: image_replica
version: "2.0.0"
priority: 32
layer: strategy
max_tokens: 500
conditions:
  task_tags:
    - image_replica
---
## 图片表格复刻策略

当用户提供图片并要求复刻/仿照/照着做表格时，**必须优先使用自动化流水线**，禁止直接 `run_code` 从零构建。

### 推荐流程（自动化流水线）

1. **提取结构**：调用 `extract_table_spec`，从图片自动提取表格结构、数据和样式 → 输出 ReplicaSpec JSON
2. **编译 Excel**：调用 `rebuild_excel_from_spec`，从 spec 确定性编译为 Excel 文件
3. **验证一致性**：调用 `verify_excel_replica`，对比 spec 与 Excel 生成差异报告（含值/合并/对齐/列宽/行高）
4. **精修差异**：按下方精修清单逐项修正
5. **收尾适配**：调用 `adjust_column_width(auto_fit=True)` + `adjust_row_height(auto_fit=True)`
6. **交付**：将构建结果和已知差异汇总到最终回复中

### 降级条件

仅当 `extract_table_spec` 失败（VLM 不可用、返回错误）时，回退到手动模式：
1. 用 `read_image` 加载图片到视觉上下文
2. 分析表格结构（行列、合并、标签-值对、数据区、样式）
3. 用 `run_code` + openpyxl 分步构建（先数据 → 再样式 → 再合并/对齐/列宽）
4. 用 `read_excel(include=["styles"])` 回读验证
5. 执行收尾适配（同上）

### 精修清单（按优先级）

验证报告中出现偏差时，按以下顺序用 `run_code` 修正：

1. **对齐修正**：按"对齐推断规则"设置每列/区域的对齐方式
2. **列宽微调**：auto_fit 后若仍不匹配原图，手动指定关键列宽度
3. **行高分档**：标题行 32–40pt、表头行 24–28pt、数据行 18–22pt、汇总行 22–26pt
4. **合并单元格内居中**：所有合并区域设置 horizontal="center", vertical="center"
5. **边框完整性**：确认四边框线、表头下方加粗线等与原图一致

### 对齐推断规则

当 extract_table_spec 返回的对齐信息为空或不完整时，根据数据类型自动推断：

| 区域/类型 | 水平对齐 | 垂直对齐 |
|-----------|---------|---------|
| 数字/金额/百分比列 | **right** | center |
| 纯文本/名称列 | **left** | center |
| 日期列 | center | center |
| 表头行（所有列） | **center** | center |
| 合并单元格 | **center** | center |
| 汇总行标签（如"合计"） | right 或 center | center |
| 汇总行数值 | **right** | center |

**关键原则**：如果原图中能看到明确的对齐方式，以原图为准；推断规则仅用于原图不清晰或 spec 缺失的情况。
