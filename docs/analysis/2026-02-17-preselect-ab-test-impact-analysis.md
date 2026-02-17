# "首轮预选 + 缺失时自动补充"方案影响分析报告

> 分析日期：2026-02-17
> 基于：Phase 2 A/B 测试 15 Case × 4 方案 × 3 轮数据
> 分析目标：评估新方案对 LLM 行为、用户体验和系统可靠性的影响

---

## 方案概述

**"首轮预选 + 缺失时自动补充"**（以下简称 E 方案）：

1. **首轮预选**：小模型预选 1-2 个 skillpack，注入其 allowed_tools 的完整 schema + instructions
2. **自动补充**：主模型调用不在当前 scope 中的工具时，自动加载对应 skillpack（而非抛出 `ToolNotAllowedError`）
3. **倾向加载**：对不确定的工具倾向于加载而非拒绝

与现有方案的关键差异：

| 维度 | B meta_only | C/D 精准路由 | E 预选+自动补充 |
|------|------------|-------------|----------------|
| 首轮工具集 | 22（DISCOVERY_TOOLS） | 16-21（单 skill） | 25-35（1-2 skill） |
| 缺失工具处理 | 报错 → LLM 需 select_skill | 报错 → LLM 需 select_skill | **自动加载** → 同轮重试 |
| instructions 注入 | 无（仅工具索引文本） | 有（预选 skill 的） | 有（预选 skill 的） |
| 扩展路径 | LLM 主动 select_skill | LLM 主动 select_skill | 自动 + LLM 主动 |

---

## 一、对 LLM 工具选择行为的影响

### 1.1 工具 schema 可见但 instructions 未注入时的调用质量

当前 B 方案的工具索引（`_build_tool_index_notice`）仅提供工具名称和简短描述文本：

```
未激活（需 select_skill 激活对应技能后可用）：
  · 数据写入：write_excel(将行数据批量写入), write_cells(向指定单元格写入值/公式)...
  · 格式化：format_cells(对单元格范围应用格式化样式)...
```

这种"只有名字没有 schema"的引导方式存在明确的质量差异：

**A/B 测试证据**：
- B 方案在 `conditional_format` 任务中 0/3 完成率，核心原因是 `add_conditional_rule` 的参数格式错误（`formula=1000` vs `values=[1000]`）。B 方案通过 `select_skill` 激活 format_basic 后获得了工具 schema，但**没有获得 SKILL.md 中的参数使用指引**，导致连续 3 轮犯同样的参数错误。
- 对比 C 方案（预路由注入 format_basic 的 instructions），C 在 run_2/run_3 都正确使用了 `values=[1000]` 列表格式，2/3 完成率。

**结论**：schema 可见但 instructions 未注入时，LLM 对复杂参数的使用质量显著下降。E 方案的预选注入 instructions 能有效缓解此问题。自动补充时也应同时注入 instructions，否则会退化为 B 方案的参数错误模式。

### 1.2 自动补充是否导致"试探性"调用

当前系统中，LLM 调用 scope 外工具会收到明确的 `ToolNotAllowedError`。如果改为自动补充，LLM 将不再收到这个错误信号。

**A/B 测试数据不支持这个担忧**：
- A 方案（全量 49 工具可见）的平均工具调用次数为 2.87/case，与 B（2.73）、C（2.56）、D（2.60）差异不大
- A 方案并未因为"工具都可用"而产生更多试探性调用
- LLM 的工具调用行为主要受 system prompt 指引和任务复杂度驱动，而非工具可用性边界

**真正的风险不是"试探性调用"，而是"参数质量下降"**——当 LLM 调用一个自动补充的工具时，如果该工具的 instructions 未注入，LLM 只能依赖 schema 中的参数描述来构造调用。

**结论**：自动补充不太可能导致试探性调用增加，但必须确保补充时同步注入 instructions。

### 1.3 预选工具集大小对"选择困难"的影响

| 方案 | 首轮工具数 | 完成率 | 平均 Token |
|------|-----------|--------|-----------|
| A（49 工具） | 49 | 4/15 (27%) | 43K |
| B（22 工具） | 22 | 6/15 (40%) | 27K |
| C（16-21 工具） | 16-21 | 5/15 (33%) | 25K |
| D（16-21 工具） | 16-21 | 5/15 (33%) | 27K |

A 方案的 49 工具并未带来更高完成率，反而最低。原因不完全是"选择困难"，更多是：
1. Token 膨胀导致 prompt 过长（43K vs 25-27K），LLM 注意力分散
2. 工具 schema 占据大量 context window，压缩了实际数据和推理空间

E 方案预选 1-2 个 skill 后，工具数约 25-35 个。基于数据推断：
- 25-35 个工具的 prompt 开销约 6-8K token（vs A 的 11K），可接受
- 不会触发 A 方案的"prompt 膨胀"问题
- 比 C/D 的 16-21 个工具多出的部分提供了跨领域能力

**结论**：E 方案的工具集大小（25-35）处于甜蜜区间——足够覆盖大多数任务需求，又不至于造成 prompt 膨胀。

---

## 二、复合任务场景分析

### 2.1 场景一："帮我画一个柱状图，然后美化表头"

**需要**：chart_basic + format_basic

**小模型预选**：高置信度选中 chart_basic（"画图"），中置信度选中 format_basic（"美化表头"）。两个 skill 都注入。

**对比现有方案**：
- A 方案（49 工具）：run_2/run_3 均 2/2 完成，但 Token 59K
- B 方案（select_skill）：run_2/run_3 均 2/2 完成，但需额外 1 轮 select_skill，Token 56-70K
- C 方案（单 skill）：0/3 完成——chart_basic 不含 format_cells，切换后又遇 header_row 问题
- D 方案（单 skill）：run_2/run_3 2/2 完成，但 run_2 图表仅用前 10 条数据

**E 方案预期**：两个 skill 都预选注入，无需 select_skill 切换，节省 1 轮迭代。Token 约 35-40K。

### 2.2 场景二："分析数据并生成报告"

**需要**：data_basic + general_excel

**自动补充触发时机**：如果小模型只选中 data_basic，当 LLM 尝试调用 `write_text_file` 时触发自动补充加载 general_excel。

**E 方案优势**：
- data_basic 的 instructions 包含数据分析最佳实践（header_row 处理、聚合参数使用）
- 自动补充时 LLM 已完成数据分析阶段，上下文充足
- 对比 B 方案：B 需要 LLM 主动 select_skill，至少多 1 轮迭代

### 2.3 场景三："扫描目录下所有 Excel 文件并汇总"

**需要**：file_ops + data_basic

**A/B 测试参考**：file_and_analyze 中，C/D 路由偏差导致缺少 analyze_data，仅 ⚠️ 部分完成。E 方案两个 skill 都预选，不会出现此问题。

### 2.4 复合任务总结

| 场景 | C/D 单 skill 路由 | B select_skill | E 预选+自动补充 |
|------|-------------------|----------------|----------------|
| 画图+美化 | ❌ 工具集不足 | ✅ 但多 1 轮 | ✅ 无需切换 |
| 分析+报告 | ⚠️ 路由偏差风险 | ✅ 但多 1 轮 | ✅ 自动补充 |
| 扫描+汇总 | ⚠️ 缺 analyze_data | ✅ 但多 1 轮 | ✅ 两 skill 预选 |

**核心优势**：E 方案消除了 C/D 的"工具集不足"问题，同时避免了 B 的"多 1 轮 select_skill"开销。
