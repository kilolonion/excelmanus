# Anthropic Skills — XLSX Skill 深度研究分析

> 源：https://github.com/anthropics/skills/tree/main/skills/xlsx
> 许可：Source-available (Proprietary)，非 Apache 2.0 开源
> 研究日期：2026-02-15

---

## 1. 仓库整体定位

`anthropics/skills` 是 Anthropic 官方的 **Agent Skills** 公开仓库，包含 Claude 在 Claude.ai / Claude Code / API 中使用的 skill 定义。

xlsx skill 与 docx/pdf/pptx 并列为 **Document Skills**——Claude 文档能力背后的生产级 skill，属于 source-available（可参考但不可自由分发）。

---

## 2. 目录结构

```
skills/xlsx/
├── SKILL.md                    # 核心指令文件（11.4 KB）
├── LICENSE.txt                 # Proprietary 许可
└── scripts/
    ├── recalc.py               # 公式重算（LibreOffice + openpyxl）
    └── office/
        ├── soffice.py          # LibreOffice 沙箱适配（AF_UNIX shim）
        ├── pack.py             # 目录 → .xlsx/.docx/.pptx 打包
        ├── unpack.py           # .xlsx → 目录解压 + XML 美化
        ├── validate.py         # XSD schema + redlining 校验
        ├── helpers/
        │   ├── merge_runs.py   # DOCX run 合并（xlsx 不用）
        │   └── simplify_redlines.py  # DOCX track changes 简化
        ├── validators/
        │   ├── base.py         # 通用校验基类（32 KB，最大文件）
        │   ├── docx.py         # DOCX schema 校验
        │   ├── pptx.py         # PPTX schema 校验
        │   └── redlining.py    # Track changes 校验
        └── schemas/
            ├── ISO-IEC29500-4_2016/  # OOXML 标准 XSD
            ├── ecma/
            ├── mce/
            └── microsoft/
```

---

## 3. SKILL.md 核心内容拆解

### 3.1 触发条件（description / frontmatter）

```yaml
name: xlsx
description: >
  任何以 .xlsx/.xlsm/.csv/.tsv 为主要输入或输出的任务均触发。
  包括：打开/读取/编辑/修复、从头创建、格式转换、清洗脏数据。
  不触发：Word/HTML/独立 Python 脚本/数据库/Google Sheets API。
```

**关键设计**：触发判断以"交付物是否为电子表格"为核心标准，非常精确。

### 3.2 输出质量要求

分两层：**所有 Excel 文件** 和 **金融模型**。

#### 3.2.1 通用要求

| 要求 | 细节 |
|------|------|
| **专业字体** | Arial / Times New Roman，除非用户另有指定 |
| **零公式错误** | 交付前必须 0 个 #REF! / #DIV/0! / #VALUE! / #N/A / #NAME? |
| **模板保持** | 修改已有文件时，严格匹配原有格式/风格/惯例 |

#### 3.2.2 金融模型专用

| 维度 | 规则 |
|------|------|
| **颜色编码** | 蓝=硬编码输入, 黑=公式, 绿=跨表链接, 红=外部链接, 黄底=关键假设 |
| **数字格式** | 年份→文本, 货币→$#,##0, 零→"-", 百分比→0.0%, 倍数→0.0x, 负数→(123) |
| **公式构造** | 假设必须单独放置, 公式用单元格引用而非硬编码, 防止 #DIV/0! 和 #REF! |
| **硬编码注释** | 格式："Source: [系统], [日期], [具体引用], [URL]" |

### 3.3 核心工作流（6 步）

```
1. 选工具 → pandas(数据分析) 或 openpyxl(公式/格式化)
2. 创建/加载 workbook
3. 修改数据/公式/格式
4. 保存文件
5. ★ 强制公式重算 → python scripts/recalc.py output.xlsx
6. ★ 验证并修复错误 → 解析 JSON 输出，循环修复
```

### 3.4 关键原则：公式优先，禁止硬编码

这是 SKILL.md 中标记为 **CRITICAL** 的唯一规则：

```python
# ❌ 错误：在 Python 中计算后写入静态值
total = df['Sales'].sum()
sheet['B10'] = total  # 硬编码 5000

# ✅ 正确：写入 Excel 公式
sheet['B10'] = '=SUM(B2:B9)'
```

**设计意图**：确保电子表格保持动态可更新，源数据变化时自动重算。

### 3.5 库选择指南

| 场景 | 推荐库 | 注意事项 |
|------|--------|---------|
| 数据分析/批量操作 | **pandas** | `dtype=` 避免推断问题, `usecols=` 优化大文件 |
| 公式/格式/Excel 特性 | **openpyxl** | 索引 1-based, `data_only=True` 读值但保存会丢公式 |
| 大文件 | openpyxl | `read_only=True` / `write_only=True` |

### 3.6 代码风格

- 最小化代码，不加多余注释
- 避免冗长变量名和冗余操作
- 避免不必要的 print 语句
- Excel 文件本身：复杂公式加 cell comment，硬编码加数据来源注释

---

## 4. 脚本工具链分析

### 4.1 `recalc.py` — 公式重算引擎

**核心流程**：
1. 检测 LibreOffice 宏是否已安装（`RecalculateAndSave`）
2. 自动写入 LibreOffice Basic 宏到用户目录
3. 调用 `soffice --headless` 执行宏重算
4. 用 `openpyxl data_only=True` 扫描所有 cell 检测公式错误
5. 返回结构化 JSON（status / total_errors / total_formulas / error_summary）

**平台适配**：
- macOS：宏目录 `~/Library/Application Support/LibreOffice/4/user/basic/Standard`
- Linux：宏目录 `~/.config/libreoffice/4/user/basic/Standard`
- 超时控制：Linux 用 `timeout`, macOS 用 `gtimeout`

**输出格式**：
```json
{
  "status": "success | errors_found",
  "total_errors": 0,
  "total_formulas": 42,
  "error_summary": {
    "#REF!": { "count": 2, "locations": ["Sheet1!B5", "Sheet1!C10"] }
  }
}
```

### 4.2 `soffice.py` — 沙箱环境 LibreOffice 适配

**问题**：某些沙箱 VM 中 `AF_UNIX` socket 被禁用，LibreOffice 无法启动。

**解决方案**：
1. 运行时检测 `socket(AF_UNIX)` 是否可用
2. 如被禁，动态编译一个 C 语言 `LD_PRELOAD` shim（`lo_socket_shim.so`）
3. shim 拦截 `socket()` / `listen()` / `accept()` / `close()` 系统调用
4. 将 AF_UNIX socket 操作替换为 `socketpair()` 管道模拟

**工程亮点**：在 Python 中嵌入 C 源码，运行时 gcc 编译——非常极致的兼容性处理。

### 4.3 `pack.py` / `unpack.py` — Office XML 操作

- **unpack**：解压 .xlsx → 目录，XML 美化（pretty-print），DOCX 支持 run 合并和 track changes 简化
- **pack**：目录 → .xlsx，XML 压缩（condense），可选校验 + 自动修复

**注意**：这两个工具主要为 DOCX/PPTX 编辑设计（直接操作 OOXML），xlsx 场景下 Claude 主要通过 openpyxl 操作而非直接编辑 XML。

### 4.4 `validate.py` — 文档校验

- 支持 XSD schema 校验（ECMA-376 / ISO-IEC 29500）
- 支持 DOCX redlining（track changes）校验
- 支持自动修复（paraId/durableId 溢出、缺少 xml:space="preserve"）
- **xlsx 支持有限**：当前 validators 目录只有 docx.py 和 pptx.py，无 xlsx.py

---

## 5. 架构设计模式总结

### 5.1 Skill 定义模式

```
SKILL.md = frontmatter(触发条件) + 输出标准 + 工作流 + 代码示例 + 最佳实践
```

- **声明式触发**：通过 description 字段让 AI 判断何时加载 skill
- **层次化约束**：通用规则 → 领域规则（金融模型）→ 模板覆盖
- **工作流驱动**：明确的 6 步流程，强制公式重算 + 错误验证循环

### 5.2 工具链架构

```
SKILL.md (指令层)
    ↓ 指导
Agent (执行层: Claude)
    ↓ 调用
scripts/ (工具层)
    ├── recalc.py      → LibreOffice (公式引擎)
    ├── pack/unpack.py → OOXML 操作
    └── validate.py    → XSD 校验
```

**关键洞察**：
- Claude 不自己算公式，而是写入 Excel 公式 + 调用 LibreOffice 重算
- 验证是强制的闭环：recalc → 检查错误 → 修复 → 再 recalc
- 工具和指令分离，skill 只是 prompt 层的知识注入

### 5.3 与 ExcelManus 的对比

| 维度 | Anthropic xlsx Skill | ExcelManus |
|------|---------------------|------------|
| **架构** | Skill 指令 + Python 脚本 + LibreOffice | MCP Server + Agent Engine |
| **公式处理** | 写入 Excel 公式 → LibreOffice 重算 | MCP Server 直接操作（实时） |
| **读取方式** | pandas / openpyxl 直接读文件 | MCP Server 提供结构化 API |
| **格式化** | openpyxl 直接操作 | MCP tool 调用 |
| **验证** | recalc.py JSON 反馈循环 | 无对应的强制验证步骤 |
| **金融模型** | 内置详细的颜色/格式/公式规范 | 无此层面规范 |
| **触发机制** | Skill description 自动匹配 | Skillpack route / manual slash |
| **沙箱兼容** | LD_PRELOAD C shim 极致兼容 | 依赖 MCP Server 环境 |

---

## 6. 对 ExcelManus 的借鉴价值

### 6.1 可直接借鉴

1. **公式优先原则**：在 system prompt 中强调"写 Excel 公式而非硬编码计算值"
2. **强制验证闭环**：每次写入公式后应有校验步骤，检测 #REF! 等错误
3. **金融模型规范**：颜色编码 + 数字格式标准可作为 skill 或 prompt 模板
4. **库选择指南**：pandas vs openpyxl 的场景划分清晰，可纳入 prompt
5. **触发条件设计**：以"交付物类型"判断是否触发，而非简单关键词匹配

### 6.2 架构差异需注意

1. Anthropic 方案是 **"写脚本 → 执行 → 验证"** 的批处理模式
2. ExcelManus 是 **"MCP tool 调用 → 实时操作"** 的交互式模式
3. 两者互补：ExcelManus 的 MCP 方式更适合读取/分析，Anthropic 的公式写入 + 重算方式更适合复杂电子表格创建

### 6.3 暂不适用

- `pack.py` / `unpack.py`：OOXML 直接编辑对 xlsx 场景意义不大
- `validate.py`：当前无 xlsx XSD validator 实现
- `soffice.py` 的 LD_PRELOAD shim：ExcelManus 不依赖 LibreOffice

---

## 7. 关键 Takeaway

1. **Anthropic 把 xlsx 操作定位为"skill + 脚本"组合**，而非独立服务或 MCP Server
2. **公式是第一等公民**——所有计算值必须用 Excel 公式，不允许 Python 算出来硬写
3. **LibreOffice 是公式引擎**——openpyxl 只写入公式字符串，不执行计算
4. **验证是强制闭环**——recalc.py 返回结构化错误信息，Agent 必须修复到零错误
5. **金融模型有专门规范**——投行级的颜色编码/数字格式/假设文档化标准
6. **office/ 是跨文档类型的共享工具链**——xlsx/docx/pptx 共用 pack/unpack/validate/soffice
