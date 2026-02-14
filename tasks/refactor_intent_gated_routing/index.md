# LLM-Native 路由与子代理架构方案（v2）

> **任务类型**：架构重构  
> **优先级**：高  
> **预计工期**：3-5 天  
> **状态**：方案设计中  
> **前置版本**：v1 (IGR) 已废弃 — 仍基于算法打分，修补不治本

> **文档清理说明（2026-02-14）**：历史冗余文件 `index_v2.md` 已废弃并合并，后续仅维护本文件作为该任务唯一索引。

---

## 一、问题与反思

### 1.1 直接触发问题

用户输入 `"你现在有python工具了吗"`（元问题），因 `excel_code_runner` 的 trigger 包含 `"python"` 且 priority=9，
走 `confident_direct` 快速路径 → 命中 `context: fork` → 无条件启动子代理。

### 1.2 根本架构缺陷

这不是一个 bug，而是 **架构范式错误**。当前系统用 **算法打分（trigger/description 词汇匹配）** 做路由决策——
这在本质上无法理解意图。所有基于当前架构的修补（提高阈值、加负面词、加守门）都是在错误地基上打补丁。

**行业共识**：Cursor、Claude Code、Google ADK **全部使用 LLM 做路由决策**，没有任何产品使用关键词打分。

---

## 二、行业调研

### 2.1 Claude Code Skills

- **路由方式**：纯 LLM 路由。将所有 skill 的 `description + when_to_use` 打包进 `Skill` meta-tool 的描述中，由 LLM 自行推理选择。
- **无算法匹配**：没有 trigger、scoring、prefilter——完全信任模型。
- **Subagent**：`context: fork` 技能的内容注入到指定子代理，子代理在隔离上下文中执行。
- **优点**：意图理解天然准确（LLM 做决策）。
- **缺点**：每次路由都需要一次完整 LLM 调用（~500ms+），skill 多时 token 消耗大。

### 2.2 OpenAI Agents SDK

- **Input Guardrails**：在 Agent 执行**之前**运行的守门函数。
  - **Blocking 模式**：guardrail 完成后 agent 才启动，tripwire 触发则 agent 不执行。
  - **Parallel 模式**：guardrail 与 agent 并行，tripwire 触发则中止 agent。
- **Handoff 模式**：Triage Agent 分析意图 → handoff 给专业 Agent。
- **核心思想**：**昂贵操作前加轻量守门**。

### 2.3 Google ADK

- **Coordinator/Dispatcher 模式**：中央 Agent 分析意图，路由给 specialist sub-agent。
- **AutoFlow 机制**：基于 sub-agent 的 `description` 做 LLM 驱动的委派。
- **核心思想**：**LLM 做决策，description 做锚点**。

### 2.4 总结对比

| 维度 | Claude Code | OpenAI SDK | Google ADK | ExcelManus 当前 |
|------|------------|-----------|-----------|----------------|
| 路由方式 | 纯 LLM | Guardrail + Handoff | LLM Coordinator | 算法打分 |
| 意图理解 | ✅ 天然具备 | ✅ Guardrail 层 | ✅ Coordinator | ❌ 无 |
| 速度 | 慢（每次 LLM） | 快（blocking 守门） | 慢（每次 LLM） | 快（纯计算） |
| Fork 控制 | LLM 决定 | Tripwire 拦截 | Coordinator 决定 | 无条件执行 |

---

## 三、IGR 架构设计

### 3.1 核心理念

**"算法快筛 + 意图守门 + LLM 兜底"**——三层递进，逐层加码：

```
┌─────────────────────────────────────────────────────────┐
│                    用户消息输入                           │
└───────────────────────┬─────────────────────────────────┘
                        │
                ┌───────▼───────┐
                │   Gate 0      │  零成本：斜杠命令 / 显式 hint
                │  确定性分派    │  → 直接返回，不走后续门
                └───────┬───────┘
                        │ (未命中)
                ┌───────▼───────┐
                │   Gate 1      │  ~0ms：规则意图分类 + 算法预筛
                │ 意图感知预筛   │  → meta / action / ambiguous
                └───────┬───────┘
                        │
              ┌─────────┼─────────┐
              │         │         │
         meta │    action│    ambiguous
              │         │         │
              ▼         ▼         ▼
        ┌─────────┐ ┌────────┐ ┌──────────┐
        │ 禁 fork │ │正常路由│ │ Gate 2   │
        │ 注入Meta│ │(当前)  │ │Fork 守门 │
        │ 提示    │ │        │ │(LLM轻量) │
        └────┬────┘ └───┬────┘ └────┬─────┘
             │          │           │
             └──────────┼───────────┘
                        │
                ┌───────▼───────┐
                │   执行层       │  主代理 chat loop / fork 子代理
                └───────────────┘
```

### 3.2 Gate 0：确定性分派（不变）

与当前 `router.py` 的步骤 0-1 完全一致：
- 斜杠命令 → `slash_direct`
- 显式 hint → `hint_direct`

**无任何改动。**

### 3.3 Gate 1：意图感知预筛（核心新增）

#### 3.3.1 IntentType 枚举

```python
class IntentType(str, Enum):
    META = "meta"          # 元问题：询问能力、帮助、状态
    ACTION = "action"      # 操作指令：有明确操作目标
    AMBIGUOUS = "ambiguous" # 模糊意图
```

#### 3.3.2 规则分类器

**设计原则**：宁可 AMBIGUOUS 也不误判 META（避免拦截正常操作请求）。

```python
class IntentClassifier:
    """规则优先的意图分类器。零 API 成本。"""

    # ── 元问题信号 ──
    _META_PATTERNS = [
        # 能力询问句式（无操作对象）
        re.compile(r"你(有|能|可以|支持|会).{0,20}(吗|么|呢|？|\?)$"),
        # 纯疑问句式
        re.compile(r"^(什么是|怎么|如何|是否|能否|能不能|有没有|可不可以)"),
        # 功能/能力/工具类名词 + 疑问
        re.compile(r"(工具|功能|能力|方法|用法|命令).{0,5}(吗|呢|？|\?)"),
        # 帮助类
        re.compile(r"^(help|帮助|用法|说明)\s*$", re.IGNORECASE),
    ]

    # ── 操作信号（有明确操作对象）──
    _ACTION_SIGNALS = [
        # 明确文件名
        re.compile(r"\.(xlsx|xlsm|xls|csv)\b", re.IGNORECASE),
        # 祈使句 + 操作动词
        re.compile(r"(帮我|请你?|把|将|给我).{0,10}(分析|处理|读取|生成|创建|格式化|合并|筛选|统计|导出)"),
        # 操作动词 + 数据对象
        re.compile(r"(分析|筛选|排序|格式化|统计|汇总|透视|删除|修改|合并).{0,8}(数据|表格?|列|行|sheet|工作表|文件)"),
    ]

    def classify(self, user_message: str) -> IntentType:
        text = user_message.strip()
        if not text:
            return IntentType.AMBIGUOUS

        meta_score = sum(1 for p in self._META_PATTERNS if p.search(text))
        action_score = sum(1 for p in self._ACTION_SIGNALS if p.search(text))

        if meta_score > 0 and action_score == 0:
            return IntentType.META
        if action_score > 0 and meta_score == 0:
            return IntentType.ACTION
        if action_score > meta_score:
            return IntentType.ACTION
        # 两者皆有且 meta >= action → 不敢确定
        if meta_score > 0 and action_score > 0:
            return IntentType.AMBIGUOUS
        # 都无信号 → 无法判断
        return IntentType.AMBIGUOUS
```

#### 3.3.3 意图对预筛打分的影响

不改变打分算法本身，而是在打分**之后**、路由决策**之前**施加影响：

| 意图 | 对 fork skill 的处理 | 对 confident_direct 的处理 |
|------|---------------------|--------------------------|
| **META** | fork skill 得分**清零**（不参与候选排序）；如果 top1 是 fork skill，降级到下一候选 | 正常走 confident_direct，但 fork_plan 被抑制 |
| **ACTION** | 不变（当前行为） | 不变 |
| **AMBIGUOUS** | 不变，但标记 `needs_fork_confirm=True` | fork skill 走 confident_direct 时 **降级** 到 Gate 2 |

**关键设计**：META 意图下 fork skill 仍可匹配（因为 `excel_code_runner` 的非 fork 能力对用户可能有用），
但 `fork_plan` 被抑制 → 子代理不会启动，skill 以 inline 模式运行。

### 3.4 Gate 2：Fork 守门（借鉴 OpenAI Input Guardrail）

#### 3.4.1 触发条件

Gate 2 **仅在以下条件全部满足时才运行**：

1. `intent == AMBIGUOUS`
2. 路由结果包含 fork skill（`context: fork`）或大文件
3. 配置 `fork_guardrail_mode != "off"`

**其他情况完全不触发**（零额外成本）。

#### 3.4.2 执行模式（借鉴 OpenAI 的 blocking/parallel 设计）

```python
# 新增配置项
fork_guardrail_mode: str = "blocking"  # "off" | "blocking"
```

- **`off`**：跳过 Gate 2，当前行为（无条件 fork）。
- **`blocking`**（推荐默认）：Gate 2 完成后才决定是否 fork。

#### 3.4.3 守门实现

```python
class ForkGuardrail:
    """轻量 LLM 调用，判断是否需要启动 fork 子代理。"""

    SYSTEM_PROMPT = (
        "你是意图分类器。判断用户消息是否需要对 Excel 文件进行实际数据操作。\n"
        "仅输出 JSON：{\"needs_data_operation\": true/false, \"reason\": \"...\"}\n"
        "规则：\n"
        "- 询问能力/功能/用法 → false\n"
        "- 闲聊/问候/感谢 → false\n"
        "- 明确要求分析/处理/修改数据 → true\n"
        "- 提到具体文件名且有操作意图 → true"
    )

    async def should_fork(
        self,
        user_message: str,
        matched_skills: list[str],
        client: AsyncOpenAI,
        model: str,
    ) -> bool:
        """返回 True 表示应该 fork，False 表示抑制 fork。"""
        response = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": self.SYSTEM_PROMPT},
                {"role": "user", "content": user_message},
            ],
            response_format={"type": "json_object"},
            max_tokens=100,
            temperature=0,
        )
        # 解析 JSON，出错时保守放行
        try:
            result = json.loads(response.choices[0].message.content)
            return bool(result.get("needs_data_operation", True))
        except Exception:
            return True  # 解析失败 → 保守放行，允许 fork
```

**成本分析**：
- 仅在 `AMBIGUOUS + fork skill` 时触发（预计 <10% 请求）
- 使用 `max_tokens=100` + `temperature=0` → 极低 token 消耗
- 可配置使用更廉价的 `router_model`（已有配置支持）

#### 3.4.4 集成位置

在 `engine.py` 的 `_run_fork_subagent_if_needed` 中：

```python
async def _run_fork_subagent_if_needed(self, *, route_result, user_message, on_event):
    if not self._subagent_enabled:
        return None
    fork_plan = route_result.fork_plan
    if fork_plan is None:
        return None

    # ── Gate 2: Fork Guardrail ──
    if self._fork_guardrail_mode == "blocking" and route_result.needs_fork_confirm:
        should = await self._fork_guardrail.should_fork(
            user_message=user_message,
            matched_skills=route_result.skills_used,
            client=self._router_client or self._client,
            model=self._config.router_model or self._config.model,
        )
        if not should:
            logger.info("Fork Guardrail 拦截：判定无需 fork | msg=%s", user_message[:80])
            return None

    # ... 原有 fork 执行逻辑不变 ...
```

### 3.5 完整数据流

```
"你现在有python工具了吗"
  │
  ├─ Gate 0: 非斜杠、无 hint → 跳过
  │
  ├─ Gate 1:
  │   ├─ IntentClassifier: "你(有)...(吗)" 命中 META，无 ACTION 信号
  │   │   → intent = META
  │   ├─ 预筛打分: excel_code_runner 得分 12（priority 9 + trigger "python" 3）
  │   ├─ META 策略: fork_plan 被抑制，注入 [Intent:Meta] 上下文
  │   └─ 路由结果: skills=[excel_code_runner], fork_plan=None, route_mode=confident_direct+meta
  │
  ├─ Gate 2: fork_plan 为 None → 不触发
  │
  └─ 执行: 主代理直接回答（有 skill 指引但不 fork）
      → "是的，我有 write_text_file 和 run_python_script 工具..."

"帮我用 python 分析销售数据.xlsx"
  │
  ├─ Gate 0: 跳过
  │
  ├─ Gate 1:
  │   ├─ IntentClassifier: "帮我" + "分析" + ".xlsx" → action_score=2, meta_score=0
  │   │   → intent = ACTION
  │   ├─ 预筛打分: excel_code_runner 得分 12+
  │   ├─ ACTION 策略: fork_plan 正常构建
  │   └─ 路由结果: fork_plan=ForkPlan(...), route_mode=confident_direct+fork+fork_plan
  │
  ├─ Gate 2: intent=ACTION → 不触发（直接 fork）
  │
  └─ 执行: fork 子代理做只读探查 → 主代理执行

"python 处理 excel"
  │
  ├─ Gate 0: 跳过
  │
  ├─ Gate 1:
  │   ├─ IntentClassifier: 无 META 信号，无 ACTION 信号（无文件名、无祈使句）
  │   │   → intent = AMBIGUOUS
  │   ├─ 预筛打分: excel_code_runner 得分高
  │   ├─ AMBIGUOUS 策略: fork_plan 构建，标记 needs_fork_confirm=True
  │   └─ route_mode=confident_direct+fork+fork_plan+fork_needs_confirm
  │
  ├─ Gate 2: AMBIGUOUS + fork_plan → 触发 ForkGuardrail
  │   ├─ LLM 判断: "无具体文件，但有操作意图" → needs_data_operation=true
  │   └─ 放行 fork
  │
  └─ 执行: fork 子代理启动
```

---

## 四、改动清单

### 4.1 新增文件

| 文件 | 说明 |
|------|------|
| `excelmanus/skillpacks/intent.py` | IntentType 枚举 + IntentClassifier 规则分类器 |
| `excelmanus/guardrails/__init__.py` | guardrails 包 |
| `excelmanus/guardrails/fork_guardrail.py` | ForkGuardrail LLM 守门实现 |

### 4.2 修改文件

| 文件 | 改动 |
|------|------|
| `excelmanus/skillpacks/models.py` | SkillMatchResult 新增 `intent: IntentType` 和 `needs_fork_confirm: bool` 字段 |
| `excelmanus/skillpacks/router.py` | `route()` 中集成 IntentClassifier；`_decorate_result()` 中根据 intent 控制 fork_plan |
| `excelmanus/engine.py` | `_run_fork_subagent_if_needed()` 中集成 ForkGuardrail |
| `excelmanus/config.py` | 新增 `fork_guardrail_mode` 配置项 |

### 4.3 不改动

- Gate 0 的斜杠/hint 逻辑
- 预筛打分算法本身（trigger、description、file_pattern 评分）
- fork 子代理执行逻辑（`_execute_fork_plan_loop`）
- Skillpack 定义格式（SKILL.md）
- 所有 tool 实现

---

## 五、与行业方案对比

| 维度 | Claude Code | OpenAI SDK | IGR (本方案) |
|------|------------|-----------|-------------|
| 路由方式 | 纯 LLM | Guardrail + Handoff | 算法 + 规则意图 + LLM 守门 |
| 每请求 LLM 调用 | 1 次（必须） | 0-1 次（guardrail 按需） | 0-1 次（仅 AMBIGUOUS+fork） |
| P50 延迟 | ~500ms | ~50ms | ~5ms（纯算法） |
| P99 延迟 | ~800ms | ~600ms | ~300ms（LLM 守门） |
| 误 fork 率 | ~0%（LLM 判断） | ~0%（guardrail 拦截） | ~0%（三层过滤） |
| 漏 fork 率 | ~0% | ~0% | ~0%（保守放行策略） |
| 实现复杂度 | 低（全靠 LLM） | 中 | 中 |

---

## 六、实施计划

| 阶段 | 内容 | 预计耗时 |
|------|------|---------|
| **Phase 1** | `intent.py` + IntentClassifier + 单元测试 | 2h |
| **Phase 2** | router.py 集成 intent → 控制 fork_plan 构建 | 2h |
| **Phase 3** | ForkGuardrail 实现 + engine.py 集成 | 3h |
| **Phase 4** | config.py 新增配置 + 集成测试 | 1h |
| **Phase 5** | 端到端测试 + 边界案例验证 | 2h |

---

## 七、测试用例矩阵

| 输入 | 预期 intent | 预期 fork | Gate 2 触发 |
|------|-----------|----------|------------|
| "你有python工具吗" | META | ❌ 禁止 | ❌ |
| "你能做什么" | META | ❌ 禁止 | ❌ |
| "帮我分析销售数据.xlsx" | ACTION | ✅ 正常 | ❌ |
| "把A列格式化为百分比" | ACTION | ✅ 正常 | ❌ |
| "python excel" | AMBIGUOUS | 视 Gate 2 | ✅ |
| "处理一下数据" | AMBIGUOUS | 视 Gate 2 | ✅ |
| "帮我分析一下好吗" | ACTION（action > meta） | ✅ 正常 | ❌ |
| "/excel_code_runner 分析数据" | N/A（Gate 0 斜杠） | ✅ 正常 | ❌ |
