# 提示词工程全量改造 v3

## 状态：✅ 已完成

## 背景

v2 将提示词从"能力清单式"升级为"分段协议式"，但仍存在以下问题：
1. **空承诺幻觉**：Agent 输出"请稍等""我先…"等计划性文字但不调用工具，引擎收到纯文本直接终止
2. **工作循环第2步诱导文本计划**："计划：给出简明的执行步骤"暗示 LLM 可先输出文字计划
3. **"每次工具调用前用一句话说明目的"**：被 LLM 误解为需要单独文本轮次
4. **缺少轮次结束条件**：LLM 不知道何时该停（返回纯文本）、何时该继续调工具
5. **反空承诺指令太弱**：唯一一句"不要以仅给出计划结束"藏在任务管理段，措辞弱

## 调研依据

| Agent | 关键设计 | ExcelManus 借鉴点 |
|-------|---------|-------------------|
| **Claude Code** | Tone/Style 段："MUST avoid 'Here is what I will do next...'"；计划通过 TodoWrite 工具完成；20+模块化分段 | 反空承诺放在最显眼位置；计划工具化（task_create）；模块化段落 |
| **Cursor** | 工具自带 `explanation` 参数；`<search_and_reading>` 段鼓励"call more tools before ending your turn" | 删除"工具前说明目的"；添加轮次结束条件 |
| **Windsurf** | 行动导向："implement changes rather than only suggesting them" | 行动优先，不仅给出建议 |

## 改造原则

- **纯提示词改造**：不做引擎层程序匹配（nudge 机制等），完全通过提示词约束 LLM 行为
- **接口不变**：`_DEFAULT_SYSTEM_PROMPT` 仍是字符串，`ConversationMemory` API 不变
- **模块化分段**：9 个 `_SEGMENT_*` 常量按优先级组装，关键约束靠前
- **精简 token**：目标从 ~1200 tks 降到 ~750 tks，给 WURM/技能包上下文腾空间

---

## 改造清单

### Phase 1：主系统提示词重写（memory.py）

**文件**：`excelmanus/memory.py` 第 9-95 行

#### Step 1.1：定义 9 个 SEGMENT 常量

按优先级排序：

| # | 常量名 | 职责 | 关键改动 |
|---|--------|------|---------|
| ① | `_SEGMENT_IDENTITY` | 身份定义 | 极简化（< 80字），保留路径说明 |
| ② | `_SEGMENT_TONE_STYLE` | 风格+反空承诺 | **核心**：禁止空承诺 + 轮次结束条件 + 中文反例 |
| ③ | `_SEGMENT_TOOL_POLICY` | 工具行为约束 | 删除"工具前说明目的"，强化"行动优先" |
| ④ | `_SEGMENT_WORK_CYCLE` | 工作循环 | 第2步改为 task_create 工具化，不再"给出执行步骤" |
| ⑤ | `_SEGMENT_TASK_MANAGEMENT` | 任务管理 | 精简到4条核心规则 |
| ⑥ | `_SEGMENT_SAFETY` | 安全策略 | 保留，精简措辞 |
| ⑦ | `_SEGMENT_CONFIDENTIAL` | 保密边界 | 保留，精简措辞 |
| ⑧ | `_SEGMENT_CAPABILITIES` | 能力范围 | 压缩为一行 |
| ⑨ | `_SEGMENT_MEMORY` | 记忆管理 | 保留分类，精简说明文字 |

#### Step 1.2：组装为 `_DEFAULT_SYSTEM_PROMPT`

```python
_DEFAULT_SYSTEM_PROMPT = "\n\n".join([
    _SEGMENT_IDENTITY,
    _SEGMENT_TONE_STYLE,
    _SEGMENT_TOOL_POLICY,
    _SEGMENT_WORK_CYCLE,
    _SEGMENT_TASK_MANAGEMENT,
    _SEGMENT_SAFETY,
    _SEGMENT_CONFIDENTIAL,
    _SEGMENT_CAPABILITIES,
    _SEGMENT_MEMORY,
])
```

#### Step 1.3：更新测试

- `tests/test_memory.py`：断言引用 `_DEFAULT_SYSTEM_PROMPT`（仍导出同名常量，无需改断言逻辑）
- `tests/test_engine.py`：同上，只要常量名不变即可
- 如果截断测试阈值需要调整（新 prompt 更短），对应调低

---

### Phase 2：元工具 description 强化（engine.py）

**文件**：`excelmanus/engine.py` `_build_meta_tools()` 方法

#### Step 2.1：`select_skill` description

在现有 description 末尾追加：
```
重要：调用本工具后立即执行任务，不要仅输出计划文字。
```

#### Step 2.2：`delegate_to_subagent` description

追加：
```
注意：委派即执行，不要先描述你将要委派什么，直接调用。
```

#### Step 2.3：`ask_user` description

无需改动（已足够清晰）。

---

### Phase 3：路由注入上下文措辞强化（router.py）

**文件**：`excelmanus/skillpacks/router.py`

#### Step 3.1：`_build_file_structure_context` 渲染文本

在文件结构预览末尾追加一行行动指引：
```
请基于以上预览直接调用工具执行用户请求，不要重复描述文件结构。
```

#### Step 3.2：`_build_large_file_context` 渲染文本

在大文件提示末尾追加：
```
请直接调用推荐的工具开始处理，不要先输出处理计划。
```

---

### Phase 4：WURM 窗口感知注入措辞微调（renderer.py）

**文件**：`excelmanus/window_perception/renderer.py`

#### Step 4.1：`render_system_notice` enriched 模式头文本

当前：
```
如果所需信息已在下方窗口中（列名、行数、预览数据等），直接引用回答，无需重复调用工具获取。
```

改为（更强行动导向）：
```
如果所需信息已在下方窗口中，直接引用回答或基于窗口数据调用工具执行，无需重复读取。
```

---

### Phase 5：跑测试验证

```bash
cd /Users/jiangwenxuan/Desktop/excelagent && python -m pytest tests/ -x -q
```

确认全部通过，无回归。

---

## 各段落具体文本（Phase 1 详细设计）

### ② _SEGMENT_TONE_STYLE（最关键改动）

```
## 输出风格
- 简洁直接，聚焦于做了什么和结果。
- **禁止空承诺**：不要输出"请稍等""我先…""马上开始""让我来…"等文字。
  收到请求后直接调用工具执行，说明与工具调用在同一轮完成。
- 只在以下情况返回纯文本结束轮次：
  (a) 任务已完成，输出最终结果
  (b) 通过 ask_user 等待用户回答
  (c) 遇到不可恢复的错误
- 不输出冗余的开场白、道歉或重复总结。
- 发现数据异常时如实报告，不忽略。
- 不给出时间估算，聚焦于做什么。
```

### ④ _SEGMENT_WORK_CYCLE（核心改写）

```
## 工作循环
1. **检查上下文**：窗口感知是否已提供所需信息？若有则直接执行。
2. **补充探查**：信息不足时用最少的只读工具补充。
3. **执行**：调用工具完成任务；独立操作并行，依赖步骤串行。
   复杂任务（3步以上）先用 task_create 建立步骤清单，再逐步执行。
4. **验证**：对关键结果做一致性检查（行数、汇总值、路径）。
5. **汇报**：简要说明做了什么和产出。
```

关键变化：
- 删除原第2步"计划：给出简明的执行步骤"
- 计划通过 task_create 工具完成（嵌入第3步）
- 第1步强调先检查 WURM 窗口上下文

---

## 验收标准

1. `pytest tests/ -x -q` 全部通过
2. 新 `_DEFAULT_SYSTEM_PROMPT` 包含"禁止空承诺"关键词
3. 新 prompt 字符数 < 2000（原 ~2800）
4. `_SEGMENT_TONE_STYLE` 在组装后位于第 2 段（仅次于身份定义）
5. 不存在"给出简明的执行步骤"或"每次工具调用前用一句话说明目的"等诱导文本
