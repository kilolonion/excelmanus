---
name: image_replica
version: "1.0.0"
priority: 32
layer: strategy
max_tokens: 400
conditions:
  task_tags:
    - image_replica
---
## 图片表格复刻策略

当用户提供图片并要求复刻/仿照/照着做表格时，**必须优先使用自动化流水线**，禁止直接 `run_code` 从零构建。

### 推荐流程（自动化流水线）

1. **提取结构**：调用 `extract_table_spec`，从图片自动提取表格结构、数据和样式 → 输出 ReplicaSpec JSON
2. **编译 Excel**：调用 `rebuild_excel_from_spec`，从 spec 确定性编译为 Excel 文件
3. **验证一致性**：调用 `verify_excel_replica`，对比 spec 与 Excel 生成差异报告
4. **精修差异**：根据验证报告，用 `run_code` 修正未覆盖的差异（对齐、列宽、特殊格式）
5. **交付**：将构建结果和已知差异汇总到最终回复中

### 降级条件

仅当 `extract_table_spec` 失败（VLM 不可用、返回错误）时，才回退到手动模式：
1. 用 `read_image` 加载图片到视觉上下文
2. 分析表格结构（行列、合并、标签-值对、数据区、样式）
3. 用 `run_code` + openpyxl 分步构建（先数据 → 再样式 → 再合并/列宽）
4. 用 `read_excel(include=["styles"])` 回读验证

### 对齐注意事项

- **数字列默认右对齐**，文本列默认左对齐，标题行居中——这是 Excel 的标准惯例
- 合并单元格中的内容通常水平居中 + 垂直居中
- 注意区分不同区域的对齐方式：表头 vs 数据区 vs 汇总行
- 验证时特别关注对齐是否与原图一致
