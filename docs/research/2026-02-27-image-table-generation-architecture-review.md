# 图像增强式表格生成功能 — 架构与行业范式研究报告

> 日期：2026-02-27 · 版本：v1.0

---

## 一、当前架构全景

### 1.1 核心数据流

```
用户上传图片
    ↓
┌─────────────────────────────────────────────────────────────┐
│ B+C 双通道图片处理                                            │
│  B 通道：小 VLM → Markdown 文字描述 → 注入主模型文本上下文        │
│  C 通道：read_image → base64 直接注入主模型视觉上下文             │
└─────────────────────────────────────────────────────────────┘
    ↓ Agent 路由（由 image_replica.md 策略指导）
    ├── 快速模式：主模型直接 run_code + openpyxl 手写 Excel
    └── 标准模式（流水线）↓
    ┌──────────────────────────────────────────────┐
    │ extract_table_spec (ExtractTableSpecHandler)  │
    │  → _run_vlm_extract_spec（两阶段 VLM 提取）    │
    │                                              │
    │  Phase 1 (data mode): 结构+数据+合并+对齐      │
    │    └ 图片预处理 → VLM JSON → _parse_vlm_json  │
    │  Phase 2 (style mode, 可选): 样式提取          │
    │    └ 保留原始颜色 → VLM JSON                   │
    │  后处理: postprocess_extraction_to_spec        │
    │    └ → ReplicaSpec（Pydantic IR）              │
    └──────────────────────────────────────────────┘
    ↓
    ┌──────────────────────────────────────────────┐
    │ rebuild_excel_from_spec                       │
    │  确定性编译: ReplicaSpec → .xlsx (openpyxl)   │
    │  值类型转换、样式应用、合并、列宽/行高、auto_fit │
    └──────────────────────────────────────────────┘
    ↓
    ┌──────────────────────────────────────────────┐
    │ verify_excel_replica                          │
    │  Spec 与 Excel 逐 cell 比对 + 样式偏差报告     │
    └──────────────────────────────────────────────┘
    ↓
    精修（run_code 按验证报告修正）→ 交付
```

### 1.2 涉及文件清单

| 模块 | 文件 | 职责 |
|------|------|------|
| **IR 协议** | `replica_spec.py` | ReplicaSpec Pydantic 数据模型（126 行） |
| **VLM 提示词** | `vision_extractor.py` | B 通道描述 prompt + Phase 1/2 提取 prompt + 后处理 + 语义颜色映射 |
| **图片工具** | `tools/image_tools.py` | `read_image` / `rebuild_excel_from_spec` / `verify_excel_replica` / `extract_table_spec` ToolDef |
| **工具处理器** | `engine_core/tool_handlers.py` | `ExtractTableSpecHandler`（文件校验 → 委托 dispatcher） |
| **VLM 调用核心** | `engine_core/tool_dispatcher.py` | `_run_vlm_extract_spec` / `_prepare_image_for_vlm` / `_call_vlm_with_retry` / `_run_vlm_describe` |
| **4 阶段管线** | `pipeline/progressive.py` | `ProgressivePipeline`（4 阶段 + 断点 + 暂停） |
| **管线辅助** | `pipeline/phases.py` / `models.py` / `patch.py` | Phase prompt、数据模型、补丁应用 |
| **策略提示词** | `prompts/strategies/image_replica.md` | Agent 行为指导（快速/标准/降级） |
| **配置** | `config.py` | `vlm_*` 系列配置项（~15 个） |

### 1.3 图片预处理管线详解

`_prepare_image_for_vlm()` 实现了 8 步自适应预处理：

1. **长边缩放**：超过 `max_long_edge`（默认 2048px）等比缩放
2. **RGB 转换**：RGBA/P/LA → RGB（白色背景填充）
3. **style 模式短路**：仅缩放+RGB，保留原始颜色
4. **灰度统计分析**：计算 mean_brightness / stddev / histogram
5. **灰色背景白化**：mean 在 180-230 且 stddev < 60 时，用灰度 mask 白化
6. **自适应对比度增强**：stddev < 40 强力增强，< 70 适度增强
7. **扫描件二值化**：直方图双峰检测 + 类 Otsu 阈值化
8. **智能锐化**：边缘检测评估清晰度，模糊图片应用锐化

### 1.4 ReplicaSpec IR 设计

```
ReplicaSpec
├── version: str
├── provenance: Provenance（source_image_hash, model, timestamp）
├── workbook: WorkbookSpec（default_font, locale, theme_hint）
├── sheets: list[SheetSpec]
│   ├── name, dimensions, freeze_panes
│   ├── cells: list[CellSpec]
│   │   └── address, value, value_type, display_text, number_format,
│   │       formula_candidate, style_id, confidence
│   ├── merged_ranges: list[MergedRange]
│   ├── styles: dict[str, StyleClass]
│   │   └── font(FontSpec), fill(FillSpec), border(BorderSpec), alignment(AlignmentSpec)
│   ├── column_widths, row_heights
│   ├── objects: ObjectsSpec（charts, images, shapes — 占位）
│   └── semantic_hints: SemanticHints（header_rows, total_rows, formula_patterns）
└── uncertainties: list[Uncertainty]
    └── location, reason, candidate_values, confidence
```

---

## 二、行业范式调研

### 2.1 主流技术路线对比

| 路线 | 代表方案 | 优势 | 劣势 | 适用场景 |
|------|---------|------|------|---------|
| **A. 端到端 VLM** | GPT-4o, Gemini 2.5 Pro, Claude Sonnet 4, Qwen-VL | 零额外模型、理解语义、可输出 JSON | 幻觉风险、token 上限、样式不精准 | 中小表格、内容理解为主 |
| **B. 传统 TSR 管线** | Microsoft Table Transformer (DETR), img2table, Camelot, Tabula | 精确 cell 边界、无幻觉、可离线 | 不提取样式、复杂布局差、需 OCR | 规整表格、批量处理 |
| **C. 专用 Table VLM** | Table-LLaVA, TableVLM, TabPedia | 表格理解精度高 | 可用性有限、不通用 | 学术/垂直场景 |
| **D. 混合管线** | CV 检测 cell 边界 + VLM 填充内容 + 像素采样样式 | 最佳精度 | 管线复杂 | 高精度复刻需求 |

### 2.2 最新 Benchmark（2025-2026）

根据 16x Eval 的 Image Table Data Extraction 评测：

| 模型 | 得分 | 备注 |
|------|------|------|
| Gemini 2.5 Pro | 9.5/10 | 两次尝试均满分 |
| Claude Sonnet 4 | 9.5/10 | 两次尝试均满分 |
| Gemini 2.5 Flash | 8/10 & 6/10 | 不稳定 |
| GPT-5 (High) | 8/10 & 6/10 | 需高推理模式 |
| Claude Opus 4.1 | 3/10 | 意外低分 |
| Grok 4 | 1/10 | 完全失败 |

**关键洞察**：同一厂家不同型号差异巨大（Claude Sonnet 4 vs Opus 4.1），VLM 表格提取能力与模型大小不成正比，与训练数据和对齐方式更相关。

### 2.3 学术前沿

根据 2024-2025 Survey（*Large Language Model for Table Processing*）：

- **表格序列化**：HTML 和 Markdown 是 LLM 最有效的表格表示格式
- **视觉线索重要**：图片形式的表格可以促进 VLM 的复杂推理能力
- **训练成本高**：微调 7B 表格 VLM 需要 8×80GB GPU，且不一定优于 prompting 闭源模型
- **锚点编码**：对于电子表格中的合并单元格和层次结构，基于锚点的 JSON 编码比行列线性化更有效（与 excelmanus 的 CellSpec.address 设计一致）

### 2.4 行业最佳实践总结

1. **中间表示（IR）是关键** — 将提取和生成解耦，允许独立优化和验证
2. **多阶段提取优于单次调用** — 将结构/数据/样式分离可降低每次调用的输出复杂度
3. **自校验循环** — 生成后回看原图做 diff 是提升精度的有效手段
4. **降级路径必须存在** — VLM 服务不可用时的优雅回退
5. **置信度追踪** — 对低置信区域标注并提示人工确认

---

## 三、架构合理性评估

### 3.1 ✅ 设计亮点（与行业范式一致或领先）

| 编号 | 亮点 | 说明 |
|------|------|------|
| S1 | **ReplicaSpec IR** | 严谨的 Pydantic 数据协议，解耦提取与生成，是编译器 IR 的最佳实践在表格领域的应用。支持溯源（provenance）和不确定性追踪 |
| S2 | **确定性编译** | `rebuild_excel_from_spec` 是纯函数：相同 Spec → 相同 Excel，可测试、可回放 |
| S3 | **自适应图片预处理** | 8 步预处理管线（灰底检测、扫描件二值化、智能锐化）在行业中属于较完整的实现 |
| S4 | **双速策略** | 快速模式 vs 标准模式，用户意图驱动，避免过度工程 |
| S5 | **验证闭环** | `verify_excel_replica` 生成结构化 diff 报告，Agent 可据此精修 |
| S6 | **语义颜色映射** | VLM 可以输出 `dark_blue` 而非精确 hex，降低 VLM 格式遵循压力 |
| S7 | **优雅降级** | VLM 失败 → read_image + run_code 手动构建；Phase 2 失败 → 仅数据无样式 |
| S8 | **数据-样式分离的两阶段设计** | Phase 1 用 data 模式（增强文字可读性），Phase 2 用 style 模式（保留原始颜色），针对性优化 |

### 3.2 ⚠️ 问题与风险

| 编号 | 问题 | 严重度 | 说明 |
|------|------|--------|------|
| **W1** | **4 阶段管线是死代码** | 中 | `pipeline/progressive.py` 的 `ProgressivePipeline`（含 Phase 4 自校验、断点续跑、uncertainty 暂停）未被任何生产路径引用。`extract_table_spec` 走的是 `_run_vlm_extract_spec`（2 阶段），**丢失了 Phase 4 自校验能力** |
| **W2** | **大表格 token 溢出** | 高 | 当表格超过 ~200 个 cell 时，VLM 单次 JSON 输出可能超过 `vlm_max_tokens`（16384）。当前仅有截断检测 + JSON 修复（被动），缺少主动分片提取策略 |
| **W3** | **置信度全部为 1.0** | 中 | `postprocess_extraction_to_spec` 中所有 cell 的 confidence 硬编码为 1.0，仅 uncertainty 项为 0.5。无法区分高/低置信度的正常 cell |
| **W4** | **无 CV 辅助 cell 边界** | 中 | 纯 VLM 提取 cell 地址（如 A1, B2）依赖模型理解，无 ground-truth 锚定。对于密集/小字表格易出错 |
| **W5** | **样式提取精度低** | 中 | VLM 对颜色、字号、边框的识别精度有限。语义颜色表仅 ~20 项，无法覆盖实际调色板 |
| **W6** | **Phase 1+2 职责重叠** | 低 | 2 阶段系统的 Phase 1 同时提取结构和数据，4 阶段系统将其分为 Phase 1（结构）+ Phase 2（数据），但生产路径只用 2 阶段版本，等于每次都要求 VLM 一次性输出所有内容 |
| **W7** | **无多次采样/投票机制** | 中 | 关键数字（金额、百分比）仅提取一次，无法通过多次采样投票减少幻觉 |
| **W8** | **代码重复** | 低 | `_build_style_class` 在 `vision_extractor.py` 和 `pipeline/phases.py` 中各实现一份；`_expand_range`、`_infer_number_format` 同理 |

### 3.3 架构路线图建议

#### 短期（低成本，高收益）

| 编号 | 建议 | 预期收益 |
|------|------|---------|
| R1 | **激活 4 阶段管线**：将 `ExtractTableSpecHandler` 改为调用 `ProgressivePipeline`，获得 Phase 4 自校验和断点恢复能力 | 显著提升精度，已有代码只需接线 |
| R2 | **大表格分区提取**：当 Phase 1 检测到 rows × cols > 阈值（如 500 cells）时，自动按行区间分片提取 Phase 2 数据，然后合并 | 解决 token 溢出瓶颈 |
| R3 | **清理代码重复**：将 `_build_style_class`、`_expand_range` 等抽到共享模块 | 降低维护成本 |

#### 中期（需要一定开发量）

| 编号 | 建议 | 预期收益 |
|------|------|---------|
| R4 | **像素级颜色采样**：在 Phase 2 前，用 CV 检测 cell 区域并采样背景色/文字色像素，作为 Phase 3 样式提取的 ground-truth 锚定 | 样式精度大幅提升 |
| R5 | **关键 cell 多次采样投票**：对 Phase 2 提取的 number/date 类型 cell，跑 2-3 次 VLM 调用，取多数投票值 | 减少数值幻觉 |
| R6 | **置信度校准**：根据 Phase 4 校验结果反向更新 cell 置信度；低置信度 cell 在验证报告中高亮 | 提供更好的人机协作信号 |

#### 长期（架构演进方向）

| 编号 | 建议 | 预期收益 |
|------|------|---------|
| R7 | **混合管线（CV + VLM）**：引入 Microsoft Table Transformer 或 img2table 做 Phase 0 cell boundary detection，VLM 仅负责内容填充和语义理解 | 消除 cell 地址幻觉 |
| R8 | **表格专用 VLM 微调**：基于 Qwen-VL 或 LLaVA 对表格提取任务做 LoRA 微调，替代通用 VLM | 精度 + 成本双优化 |
| R9 | **ReplicaSpec v2**：扩展 IR 支持条件格式、数据验证、图表对象等高级 Excel 特性 | 覆盖更复杂的复刻需求 |

---

## 四、与行业范式的差距分析

| 维度 | excelmanus 现状 | 行业最佳实践 | 差距 |
|------|----------------|-------------|------|
| **中间表示** | ✅ ReplicaSpec（Pydantic） | IR 解耦是共识 | 领先，少有开源项目设计如此完整的 IR |
| **多阶段提取** | ⚠️ 4 阶段已实现但未激活 | 分步提取降低单步复杂度 | 需激活 ProgressivePipeline |
| **自校验** | ⚠️ verify_excel_replica 在编译后验证，但缺少 VLM 回看原图对比 | Phase 4 VLM 自校验 + 后编译验证 | 需激活 Phase 4 |
| **图片预处理** | ✅ 8 步自适应管线 | 基本预处理（缩放+增强） | 领先行业平均水平 |
| **降级机制** | ✅ VLM → 主模型手动构建 | 必须有降级路径 | 已满足 |
| **大表格处理** | ❌ 单次调用，依赖 max_tokens | 分片/分区/流式提取 | 明显短板 |
| **CV 辅助** | ❌ 纯 VLM | 混合 CV + VLM 是趋势 | 未来方向 |
| **多次采样** | ❌ 单次提取 | 关键字段多次投票 | 可选优化 |
| **样式精度** | ⚠️ 语义颜色映射（20 色） | 像素采样 + VLM 辅助 | 可优化空间大 |

---

## 五、结论

excelmanus 的图像增强式表格生成功能在**架构设计层面处于行业前沿**：

- **ReplicaSpec IR** 是核心亮点，将"理解"与"生成"完全解耦，这在同类开源项目中极少见
- **确定性编译 + 验证闭环**的设计哲学正确
- **B+C 双通道 + 两速策略**兼顾灵活性和效率

最大的问题是**已实现的 4 阶段管线未接入生产路径**（W1），这意味着 Phase 4 自校验、断点续跑、uncertainty 暂停等投入没有产生价值。**第一优先级是激活 ProgressivePipeline**。

第二大问题是**大表格 token 溢出**（W2），需要引入分区提取策略。

长期方向应向**混合管线（CV + VLM）**演进，用确定性 CV 提供 cell 边界的 ground truth，VLM 专注内容理解和语义层面的工作。
