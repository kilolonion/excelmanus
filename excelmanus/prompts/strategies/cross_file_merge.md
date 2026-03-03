---
name: cross_file_merge
version: "1.0.0"
priority: 48
layer: strategy
max_tokens: 500
conditions:
  chat_mode: "write"
  task_tags:
    - multi_file
---
## 跨文件合并与匹配策略

当任务涉及两个或多个 Excel 文件之间的数据合并、匹配、导入时：

> **提示**：系统在首轮 chat 时会自动预扫描工作区 Excel 文件并检测跨文件列关联（见"数据探索概况"）。如果已有关联信息，可直接利用，无需重复探查。
>
> **文件组**：如果用户已将文件分组（见"工作区文件全景 → 文件组"），同组文件通常有逻辑关联。跨文件操作时优先在同组内匹配键列，避免混淆不同项目的文件。

### 标准流程（Explore → Identify → Merge → Verify）

1. **全量探查**：对所有相关文件调用 `inspect_excel_files` 或 `scan_excel_snapshot`，获取每个文件的 sheet 列表、列名、行数、数据类型。不要跳过任何文件。如需深度关联分析，可调用 `discover_file_relationships` 自动检测跨文件共享列和值重叠度。

2. **识别关联键**：对比各文件的列名和样本值，确定匹配键列（如"客户ID"、"订单号"）。注意：
   - 列名可能不同但含义相同（如"客户ID" vs "CustomerID" vs "cust_id"）
   - 用 `read_excel` 各取 5 行样本值，确认键列值域有交集

3. **使用 `run_code` 执行合并**：跨文件操作统一使用 `run_code` + pandas 完成，一次性读取多个文件并合并。
   - **写入前先小批量验证**：取前 5-10 行做 merge 预览，确认匹配逻辑正确后再全量执行

4. **回读验证**：合并完成后用 `read_excel` 回读结果文件的首尾各 3 行，确认数据正确。汇报匹配率和未匹配行数。

### 常见场景模板

- **VLOOKUP 式查找**：目标文件通过键列在源文件中查找对应值。用 `pd.merge(left, right, on=key, how='left')` 实现。
- **左连接补充**：将源文件的额外列补充到目标文件。保留目标文件全部行，源文件无匹配时填 NaN。
- **交叉汇总**：从多个文件汇总数据到新文件。先 `pd.concat` 合并再 `groupby` 聚合。
- **条件导入**：从源文件筛选符合条件的行导入目标文件。先 filter 再 append/concat。

### 键列不一致处理

跨文件匹配前必须做键列预处理：
- **空格/换行**：双方 `.str.strip()` 去除前后空白
- **类型不一致**：统一转为字符串 `.astype(str)` 再匹配，或统一转为数值
- **大小写**：英文键列统一 `.str.lower()` 或 `.str.upper()`
- **模糊匹配**：键列值不完全一致时（如"北京市" vs "北京"），用 `.str.contains()` 或 fuzzywuzzy 做近似匹配，并向用户报告匹配策略

### 结果输出规范

- **默认写入新文件**：合并结果优先写入新文件（如 `merged_result.xlsx`），避免覆盖源数据
- **用户指定时追加**：若用户明确要求写入某个源文件，在目标 sheet 末尾追加或覆盖指定列
- **汇报模板**：完成后汇报「从 A.xlsx（N 行）和 B.xlsx（M 行）合并，匹配 X 行（匹配率 Y%），未匹配 Z 行，结果写入 C.xlsx」

### 示例流程
用户：「把 customers.xlsx 的客户等级根据客户ID匹配到 orders.xlsx」
1. `inspect_excel_files` → customers.xlsx(Sheet1: 50行, 列=[客户ID,客户名称,等级])、orders.xlsx(Sheet1: 500行, 列=[订单号,客户ID,金额])
2. `run_code`：pandas 读取两文件 → `df.客户ID.str.strip()` 预处理 → merge on 客户ID → 预览前5行确认匹配 → 写入 orders.xlsx 新列"等级" → stdout: `"匹配480行, 匹配率96%, 未匹配20行"`
3. `read_excel` orders.xlsx 首尾各3行 → 确认"等级"列已正确填入
4. `finish_task`：汇报"已从 customers.xlsx（50行）匹配客户等级到 orders.xlsx（500行），匹配480行（96%），20行未匹配（客户ID不在客户表中）"
