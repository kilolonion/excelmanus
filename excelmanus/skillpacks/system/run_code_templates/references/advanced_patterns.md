# 高级模板：VBA 替代、恢复与复刻

## 读取文本文件

```python
from pathlib import Path
content = Path("data.csv").read_text(encoding="utf-8")
print(content[:2000])
```

## VBA 等价操作（用户要求 VBA/宏时的 Python 替代）

### 遍历单元格并条件写入（替代 VBA 的 For Each / Range.Cells 循环）

```python
from openpyxl import load_workbook
wb = load_workbook("file.xlsx")
ws = wb["Sheet1"]
for row in range(2, ws.max_row + 1):
    val = ws.cell(row, 3).value  # C 列
    if val and str(val).strip().upper() == "MATCH":
        ws.cell(row, 4).value = "Found"  # 写入 D 列
wb.save("file.xlsx")
```

### 跨 Sheet 查找填充（替代 VBA 的 Worksheets().Range 引用）

```python
from openpyxl import load_workbook
wb = load_workbook("file.xlsx")
# 从源 sheet 构建查找字典
src = wb["源表"]
lookup = {}
for r in range(2, src.max_row + 1):
    key = src.cell(r, 1).value
    lookup[key] = src.cell(r, 2).value
# 填充目标 sheet
tgt = wb["目标表"]
for r in range(2, tgt.max_row + 1):
    k = tgt.cell(r, 1).value
    if k in lookup:
        tgt.cell(r, 3).value = lookup[k]
wb.save("file.xlsx")
```

### 按区块重复填充值（替代 VBA 的动态范围 + INVOICE 块模式）

```python
from openpyxl import load_workbook
wb = load_workbook("file.xlsx")
ws = wb["Sheet1"]
# 扫描 INVOICE NO 块：header 行 → 值行 → 明细行直到 TOTAL
blocks = []  # [(invoice_value, detail_count), ...]
r = 1
while r <= ws.max_row:
    if str(ws.cell(r, 4).value or "").strip().upper() == "INVOICE NO":
        inv = ws.cell(r + 1, 4).value
        # 找 TOTAL 行确定明细行数
        rr = r + 3
        while rr <= ws.max_row and str(ws.cell(rr, 7).value or "").strip().upper() != "TOTAL":
            rr += 1
        blocks.append((inv, max(1, rr - r - 2)))
        r = rr + 1
    else:
        r += 1
# blocks 现在包含 [(invoice, count), ...] 可用于填充其他 sheet
```

### openpyxl 不支持的操作

以下操作无法通过 openpyxl 实现，需告知用户限制：
- **创建数据透视表**（Pivot Table）— openpyxl 不支持，建议用 pandas pivot_table() 计算后写入新 sheet
- **ActiveX 控件 / UserForm** — 无 Python 等价方案
- **事件驱动宏**（Workbook_Open 等）— 无法模拟 Excel 事件模型

## 写入后独立验证

`run_code` 写入完成后，**必须用 `read_excel` 对目标 sheet 做一次独立回读验证**，而非仅依赖代码自身的 `print` 输出。
验证要点：
- 新增列是否出现在预期位置（表头名称正确）
- 抽样前 3~5 行数据与预期一致
- 数据行数未意外增减

`print` 输出和 `read_excel` 回读不可互相替代：前者验证代码逻辑，后者验证文件实际写入结果。两者都通过才算写入成功。

## 文件损坏恢复

当 openpyxl 打开文件失败（如 `KeyError: '[Content_Types].xml'`、`BadZipFile` 等），说明文件损坏。**不要**花多轮迭代诊断损坏原因，按以下策略快速恢复：
1. 如果同目录有参考文件（如 golden/模板），用 `copy_file` 复制为工作副本到 `outputs/`
2. 在副本上用 `run_code` 清除答案列/目标区域，恢复到初始状态
3. 然后在副本上执行实际写入逻辑
注意：`run_code` 中的 `shutil.copy` 会被沙盒拦截，必须用 `copy_file` 工具复制文件。

## 图片表格复刻工作流

当用户提供图片并要求复刻表格时，**优先使用自动化流水线**：

### 推荐：自动化流水线

1. **提取结构**：用 `extract_table_spec` 从图片自动提取 ReplicaSpec JSON
   - 自动识别表格结构、数据、样式（两阶段 VLM 调用）
   - 支持多表格检测（每个表格生成独立 Sheet）
   - 输出到 `outputs/replica_spec.json`
2. **编译 Excel**：用 `rebuild_excel_from_spec` 从 spec 编译为 Excel 文件
3. **验证**：用 `verify_excel_replica` 验证一致性，生成差异报告
4. **人工修正**：根据验证报告用 `run_code` 修正差异
5. **交付**：将构建结果和已知差异汇总到最终回复中

### 降级：手动模式

当 `extract_table_spec` 失败（VLM 不可用等）时，回退到手动模式：

1. **读取图片**：用 `read_image` 加载图片。系统会自动处理：
   - 若主模型支持视觉（C 通道）：图片注入对话上下文，你可以直接看到图片
   - 若配置了 VLM 增强（B 通道）：小视觉模型自动生成 Markdown 表格描述，追加在 tool result 中
   - 两者可同时生效（B+C 模式）
2. **分析结构**：结合图片（C 通道）和/或 VLM 描述（B 通道），理解表格的行列数、合并区域、标签-值对布局、数据区内容、样式特征
3. **用 `run_code` 构建**：使用 openpyxl 直接编写 Python 代码构建 Excel 文件
   - 先构建基础框架（行列、数据填充）
   - 再添加样式（字体、颜色、边框、对齐）
   - 最后处理合并单元格和列宽
4. **验证**：用 `read_excel` 回读构建结果，对比图片/描述检查数据是否一致
5. **迭代修正**：发现问题后用 `run_code` 修正，重复验证直到满意
6. **交付**：将构建结果和已知差异汇总到 finish_task 的 summary 中
