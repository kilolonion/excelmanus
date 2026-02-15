# ExcelManus 测试报告：SpreadsheetBench Verified-400 抽样套件（20题）

> **执行时间**: 2026-02-15T10:57 ~ 11:00 UTC  
> **模型**: gpt-5.3-codex  
> **Suite**: `bench/cases/suite_spreadsheetbench_verified20_seed20260215.json`  
> **数据源**: SpreadsheetBench (NeurIPS 2024) verified-400, 分层抽样 seed=20260215  
> **题目构成**: Cell-Level 10 + Sheet-Level 10 = 20 题  

---

## 1. 总览

| 指标 | 值 |
|---|---|
| 总用例 | 20 |
| 通过 | 19 (95%) |
| 失败 | 1 (5%) |
| 总耗时 | 200.21s |
| 平均耗时/题 | 10.01s |
| 总 Token | 176,938 |
| 平均 Token/题 | 8,847 |
| 工具调用失败 | 0 |

> ⚠️ **"通过"仅表示无运行时错误返回了回复，不代表回答正确。** 当前 bench 框架尚未集成 golden answer 自动比对，下文将从对话质量角度深入分析。

---

## 2. 唯一失败用例深度分析：sb_42216

### 2.1 故障链路

```
用户消息 → LLM 第1轮回复（纯文本提问，未调用工具）
         → _needs_user_decision() 检测到"text_question"
         → 触发 ask_user 强制修正
         → 构造 tool_choice={"type":"function","function":{"name":"ask_user"}}
         → 发送至 gpt-5.3-codex API
         → 400 BadRequestError: Unknown parameter: 'tool_choice.function'
         → 异常未被捕获 → case 失败
```

### 2.2 根因

**`engine.py:2867-2870`** 在 ask_user 强制修正逻辑中，构造了 OpenAI Chat Completions 格式的 `tool_choice`：

```python
forced_kwargs["tool_choice"] = {
    "type": "function",
    "function": {"name": "ask_user"},
}
```

此格式是 **OpenAI Chat Completions API** 的标准格式。但当前使用的 `gpt-5.3-codex` 走的是**原生 OpenAI SDK 直连**（非 Responses/Claude/Gemini 适配层），SDK 版本或 API 端点不支持 `tool_choice.function` 嵌套参数。

**关键**: `_create_chat_completion_with_system_fallback()` 仅捕获 **system message 兼容性错误**（多条 system 消息），不捕获 tool_choice 格式错误，导致异常直接上抛。

### 2.3 触发条件

sb_42216 的 prompt 包含**自相矛盾的指令**：
- "leave NA as blanks in Group B"
- "for blank values make them 0 while adding them in Group B"

模型第1轮合理地识别了矛盾并用纯文本向用户提问。`_needs_user_decision()` 检测到该文本提问后触发了强制修正流程，但 API 参数格式不兼容导致崩溃。

### 2.4 修复建议

1. **短期**: 在 `_create_chat_completion_with_system_fallback` 中增加 tool_choice 格式错误的捕获与降级（移除 tool_choice 重试，或转为 `tool_choice="required"`）
2. **中期**: 为 `tool_choice` 添加 provider-aware 格式适配层，统一经过各 provider 的 `_map_*_tool_choice_*` 函数
3. **长期**: 强制修正应有独立的 error boundary，不应因 API 层参数问题而终止整个 case

---

## 3. 对话行为深度分析

### 3.1 行为模式分类

对 20 个用例的对话记录逐条分析，发现以下 **4 种行为模式**：

| 模式 | 用例数 | 用例 ID |
|---|---|---|
| A. 纯文本建议（零工具调用） | 13 | sb_353_29, sb_56921, sb_3002, sb_54513, sb_51090, sb_438_18, sb_16511, sb_109_21, sb_545_35, sb_188_39, sb_469_9, sb_333_29, sb_524_31 |
| B. 读取后建议（read_excel + 文本） | 3 | sb_50088, sb_472_15, sb_9448* |
| C. 读取 + 任务管理（read + task_create/update） | 3 | sb_325_44, sb_59224, sb_51249 |
| D. API 层崩溃 | 1 | sb_42216 |

> *sb_9448 实际 tool_call_count=0，属 A 类边界情况

### 3.2 🚨 核心问题：Agent 几乎从不执行实际写操作

**这是本次测试暴露的最严重问题。**

20 个用例中，**prompt 均明确要求"Open the file ... 并执行操作"**，但：

- **0 个用例**调用了任何写入工具（write_excel、format_cells 等均未出现）
- **0 个用例**调用了 `delegate_to_subagent` 将写操作委派给代码执行子代理
- **0 个用例**调用了 `select_skill` 匹配到任何执行 skill
- **13 个用例 (65%)** 完全零工具调用，直接返回 VBA 代码/公式建议

Agent 退化为"Excel 顾问"而非"Excel 执行代理"。

**可能原因分析**：

| 因素 | 分析 |
|---|---|
| **route_mode 全部为 fallback** | 无 skill 被自动匹配，所有请求走通用 fallback 路径，tool_scope 为通用集合 |
| **tool_scope 缺少写入工具** | fallback 路径的 tool_scope 包含 `read_excel, analyze_data, filter_data` 等只读工具 + `select_skill, delegate_to_subagent`，但**不包含 write_excel、format_range 等写入工具** |
| **模型倾向** | gpt-5.3-codex 面对 Excel 论坛题型时倾向给出"教程式"回答而非工具调用 |
| **SpreadsheetBench 题目特性** | 很多题目源自论坛，措辞偏向"How can I..."咨询式，模型可能理解为知识问答 |

### 3.3 文件读取行为

仅 **5/20 (25%)** 用例在回答前读取了文件内容：

| 用例 | 读取操作 | 描述 |
|---|---|---|
| sb_325_44 | list_sheets + read_excel×2 | 读取 Input 和 Output 两个 sheet |
| sb_59224 | read_excel (含 formulas) | 读取 Sheet1 确认布局和公式 |
| sb_50088 | read_excel | 读取数据确认结构 |
| sb_472_15 | read_excel | 读取数据 |
| sb_51249 | -(仅 task_create/update) | 未实际读取 |

**15/20 (75%) 用例在未读取文件的情况下直接给出了回答**，这对于需要理解数据结构才能正确回答的题目是致命的。例如 sb_51090 要求计算仓库误差公式，Agent 直接假设了列位置（"Replace these user-column references with your actual columns"），无法保证正确性。

### 3.4 sb_3002 特殊失败：理解力断层

sb_3002 的 prompt 明确说 "Open the file ... I am including the modified example file for reference"，但 Agent 回复：

> "Got it. Please upload the **modified example file**..."

文件路径已在 prompt 中提供且 workfile 已存在，Agent 却要求用户"上传"。这反映了：
1. **对"文件已存在于工作区"的认知缺失**
2. **系统提示中"用户提供的文件路径只要在工作区内即可直接使用"的指令被忽略**

---

## 4. Token 效率分析

### 4.1 按用例 Token 分布

| 分位 | Token 数 | 代表用例 |
|---|---|---|
| P10 | ~4,500 | sb_3002 (4,550) |
| P50 | ~5,005 | sb_109_21 (5,005) |
| P90 | ~14,108 | sb_51249 (14,108) |
| Max | 39,834 | sb_59224 |

### 4.2 Token 效率评价

- **纯文本回答**平均 ~4,800 tokens/题（约 4,400 prompt + 400 completion）
- **工具使用**的用例 prompt token 膨胀显著：sb_59224 用了 39,165 prompt tokens（8 轮对话历史累积），但最终也只是给出公式建议
- **sb_325_44** 是唯一真正通过工具读取了两个 sheet 并声称"Done. I updated the workbook"的用例，但其 completion 仅 528 tokens，实际写入操作未在 tool_calls 中体现

### 4.3 任务管理开销

sb_325_44、sb_59224、sb_51249 使用了 `task_create` + `task_update`，但这些调用本身不产生实际操作价值，仅增加了 token 消耗和轮次：

- sb_59224: 8 轮迭代中有 5 轮仅为 `task_update`（占 62.5%）
- sb_325_44: 7 轮迭代中 3 轮为 task 管理（占 42.9%）

**建议**: 对单轮 bench 任务禁用或简化 task 管理工具以减少无效轮次。

---

## 5. 延迟分析

| 指标 | 值 |
|---|---|
| 最快 | 4.03s (sb_3002) |
| 最慢 | 22.7s (sb_59224) |
| 中位数 | ~8.9s |
| P90 | ~16.5s |

延迟与迭代次数强相关：
- 1 轮迭代：4-13s
- 2-3 轮迭代：7-10s
- 7-8 轮迭代：17-23s

---

## 6. 路由与 Skill 系统分析

### 6.1 路由全部 fallback

20/20 用例的 `route_mode` 均为 `"fallback"`，意味着 skill 自动路由完全未生效。

可能原因：
- SpreadsheetBench 题目为英文，技能匹配可能偏向中文关键词
- 题目措辞为论坛帖子风格（长段落），与 skill 触发模式不匹配
- 当前 skill 库可能缺少覆盖"公式修复""VBA 编写""数据转换"等题型的 skill

### 6.2 Subagent 未被使用

- 0/20 用例调用了 `delegate_to_subagent`
- 0/20 用例的 `subagent_events` 非空

Sheet-Level 题目（需要 VBA/代码执行的题型）本应委派给代码执行子代理，但当前未触发。

---

## 7. 按题目类型的行为差异

### Cell-Level（10 题）

| 指标 | 值 |
|---|---|
| 通过 | 9/10 (sb_42216 失败) |
| 读取文件 | 3/10 |
| 使用工具 | 4/10 |
| 典型回复 | 给出 Excel 公式 |

### Sheet-Level（10 题）

| 指标 | 值 |
|---|---|
| 通过 | 10/10 |
| 读取文件 | 2/10 |
| 使用工具 | 2/10 |
| 典型回复 | 给出 VBA 宏代码 |

Sheet-Level 题目几乎全部以"给 VBA 代码"方式回答，**没有任何用例尝试通过工具实际执行数据转换**。

---

## 8. 发现的系统级问题汇总

| # | 严重度 | 问题 | 影响 |
|---|---|---|---|
| 1 | 🔴 Critical | `tool_choice` 参数格式与 API 不兼容，ask_user 强制修正导致 crash | sb_42216 失败 |
| 2 | 🔴 Critical | Agent 从不执行写操作，退化为"顾问模式" | 全部 20 题无实际文件修改 |
| 3 | 🟠 Major | 75% 用例未读取文件即回答 | 回答基于假设而非数据，正确率存疑 |
| 4 | 🟠 Major | Skill 路由全部 fallback，零匹配 | 未利用专用技能包 |
| 5 | 🟡 Medium | sb_3002 要求用户"上传"已存在的文件 | 违反系统提示约束 |
| 6 | 🟡 Medium | task_create/update 开销大但无实质价值 | 单轮任务下增加 40-60% 无效轮次 |
| 7 | 🟡 Medium | Subagent 机制零使用 | VBA/代码执行类题目无法完成 |

---

## 9. 改进建议

### 9.1 紧急修复

1. **修复 `tool_choice` 兼容性** — 在 engine 层为 `tool_choice` 添加 provider-aware 格式化，或在 `_create_chat_completion_with_system_fallback` 中增加 tool_choice 错误捕获降级
2. **fallback 路径增加写入工具** — 当前 fallback tool_scope 缺少 `write_excel`, `format_range` 等工具，导致模型无法执行写入操作

### 9.2 行为优化

3. **强制"先读后答"** — 在系统提示中增加硬约束：收到包含文件路径的请求时，**必须**先调用 `read_excel` / `list_sheets` 读取文件，禁止凭空回答
4. **抑制"顾问模式"** — 系统提示中明确：你是执行代理，不是建议机器人；任务目标是修改文件并返回结果，不是给出教程
5. **优化 Skill 路由匹配** — 增加英文关键词覆盖，或为 SpreadsheetBench 风格任务添加专用 skill

### 9.3 Bench 框架增强

6. **集成 golden answer 自动比对** — 当前 bench 仅记录"是否报错"，应增加与 golden file 的自动 diff 评分
7. **增加"实际执行度"指标** — 统计是否真正修改了文件（写入工具调用次数、文件变更 hash）
8. **单轮 bench 模式下禁用 task 管理工具** — 减少无效迭代开销

---

## 10. 逐用例明细

| Case ID | 类型 | 耗时(s) | 迭代 | LLM调用 | 工具调用 | Token | 状态 | 回复模式 |
|---|---|---|---|---|---|---|---|---|
| sb_353_29 | sheet | 12.47 | 1 | 1 | 0 | 5,232 | ✅ | VBA 代码 |
| sb_56921 | cell | 7.24 | 1 | 1 | 0 | 4,706 | ✅ | 公式修正 |
| sb_3002 | cell | 4.03 | 1 | 1 | 0 | 4,550 | ⚠️ | 要求上传文件 |
| sb_54513 | cell | 12.81 | 1 | 1 | 0 | 4,500 | ✅ | 公式建议 |
| sb_325_44 | sheet | 17.52 | 7 | 7 | 6 | 34,869 | ✅ | 读取+声称写入 |
| sb_51090 | cell | 13.72 | 1 | 1 | 0 | 5,317 | ✅ | SUMPRODUCT 模板 |
| sb_59224 | cell | 22.70 | 8 | 8 | 7 | 39,834 | ✅ | 读取+公式建议 |
| sb_438_18 | sheet | 6.36 | 1 | 1 | 0 | 4,799 | ✅ | XLOOKUP 公式 |
| sb_50088 | cell | 7.54 | 2 | 2 | 1 | 9,560 | ✅ | 读取+公式建议 |
| sb_16511 | cell | 4.15 | 1 | 1 | 0 | 4,515 | ✅ | COUNTIF 公式 |
| sb_42216 | cell | 4.63 | 0 | 2 | 0 | 0* | ❌ | API 崩溃 |
| sb_109_21 | sheet | 8.31 | 1 | 1 | 0 | 5,005 | ✅ | VBA 代码 |
| sb_545_35 | sheet | 6.59 | 1 | 1 | 0 | 4,833 | ✅ | VBA regex 代码 |
| sb_188_39 | sheet | 9.52 | 1 | 1 | 0 | 5,008 | ✅ | VLOOKUP 公式 |
| sb_469_9 | sheet | 9.83 | 1 | 1 | 0 | 4,878 | ✅ | VBA 代码 |
| sb_51249 | cell | 10.58 | 3 | 3 | 2 | 14,108 | ✅ | 任务规划+公式 |
| sb_333_29 | sheet | 12.16 | 1 | 1 | 0 | 5,261 | ✅ | VBA 代码 |
| sb_472_15 | sheet | 6.99 | 2 | 2 | 1 | 9,561 | ✅ | 读取+VBA 代码 |
| sb_524_31 | sheet | 16.53 | 1 | 1 | 0 | 5,521 | ✅ | VBA mapping 代码 |
| sb_9448 | cell | 6.53 | 1 | 1 | 0 | 4,881 | ✅ | LOOKUP 公式 |

> *sb_42216 的 token 计为 0 因统计逻辑在错误中断前未累计，实际消耗约 4,607 tokens。

---

## 11. 结论

本次 SpreadsheetBench 20 题测试的 **表面通过率 95%**，但深入对话分析后发现：

1. **Agent 核心价值未实现** — 作为"Excel 执行代理"，20 题中 0 题完成了实际文件修改
2. **退化为知识问答模式** — 65% 用例零工具调用，100% 用例零写入操作
3. **文件感知能力不足** — 75% 用例未读取文件就作答
4. **系统能力未释放** — Skill 路由零匹配、Subagent 零使用、写入工具未暴露
5. **存在 1 个 P0 API 层 bug** — `tool_choice` 格式不兼容导致 crash

**真正的端到端正确率（Agent 执行后文件内容与 golden 匹配）预计远低于 95%，需要集成自动比对后才能得到准确数字。**
