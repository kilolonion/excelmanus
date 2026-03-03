# Chitchat 快速通道实现文档

> **负责人**：开发者 A
> **优先级**：P0（已有原型代码，需审查 + 补全 + 测试）
> **预估工时**：2-3 天

---

## 1. 背景与目标

当用户发送简单问候/闲聊消息（如「你好」「谢谢」「你是谁」）时，当前系统仍会走完整工具管线：
- 构建 ~25K tokens 的系统提示
- 注入 ~5K-8K tokens 的工具 schema
- 执行 FileRegistry 扫描（100-500ms I/O）
- 启动语义检索任务

**目标**：对 chitchat 消息实现零工具、最小提示词的快速通道，节省 ~30K tokens + ~500ms 延迟。

---

## 2. 架构概览

```
用户消息
  │
  ▼
router.py: _CHITCHAT_RE 正则匹配
  │
  ├── 匹配 + 安全门控通过 → route_mode="chitchat"
  │     │
  │     ▼
  │   engine.py: 多轮安全降级检查
  │     │
  │     ├── 有活跃上下文 → 降级为 "all_tools"
  │     │
  │     └── 确认 chitchat → 取消语义检索
  │           │
  │           ▼
  │         context_builder.py: 仅注入 identity+rules+channel
  │           │
  │           ▼
  │         _tool_calling_loop: tools=[], max_iter=1, 跳过 FileRegistry
  │
  └── 不匹配 → 正常路由流程
```

---

## 3. 涉及文件与改动点

### 3.1 `excelmanus/skillpacks/router.py`

**位置**：`route()` 方法，第 240-259 行左右

**当前状态**：已实现原型

**逻辑**：
```python
# 安全门控条件（全部满足才走 chitchat）：
_is_chitchat = (
    _CHITCHAT_RE.match(user_message.strip())  # 正则全匹配
    and not candidate_file_paths               # 无文件路径
    and not images                             # 无图片附件
    and len(user_message.strip()) <= 50        # 消息长度 ≤ 50 字
)
```

返回：
```python
SkillMatchResult(
    skills_used=[],
    route_mode="chitchat",
    system_contexts=[],
    parameterized=False,
    write_hint="read_only",
    task_tags=_plan_tags,  # plan 模式下追加 "plan_not_needed"
)
```

**待完善**：
1. ✅ `_CHITCHAT_RE` 正则已定义（第 52-71 行），覆盖中英文问候/身份问答/帮助请求
2. ✅ **扩展正则覆盖**：已补充以下场景并通过测试确认：
   - 短确认词：「嗯」「嗯嗯」「好」「收到」「明白」「了解」「知道了」「没问题」「可以」「行」「对」「是的」「是」「没事了」「不用了」
   - 告别词：「再见」「拜拜」「bye」「goodbye」「see you」「晚安」「good night」「88」「886」
   - 扩展感谢：「感谢」「thx」「ty」「谢了」「多谢」
   - 带标点变体：「你好！！」「hello???」「收到！」「再见。」均通过测试
3. ✅ **消息长度阈值**：50 字经验证合理，51 字边界测试确认超限消息不走快速通道

---

### 3.2 `excelmanus/engine.py`

**位置**：`chat()` 方法，第 2272-2306 行左右

**当前状态**：已实现原型

**多轮安全降级逻辑**：
```
if route_mode == "chitchat":
    检查以下任一条件是否成立 → 降级为 "all_tools":
    1. self._active_skills 非空（有活跃技能）
    2. self._question_flow.has_pending()（有待回答问题）
    3. self._approval.has_pending()（有待审批操作）
    4. 最近 6 条消息中有 role="tool"（近期有工具调用）

    如果不降级 → 取消所有语义检索任务
```

**待完善**：
1. ✅ **降级条件 review**：6 条窗口合理，新增测试验证 tool call 在窗口外时不触发降级（`test_tool_calls_beyond_6_message_window_no_downgrade`）
2. ✅ **日志与诊断**：降级原因 / 快速通道确认现已被捕获到 `_session_diagnostics`（新增 `chitchat_downgrade_reason` / `chitchat_fast_path` 字段）
3. ✅ **SSE 事件**：不需要特殊事件，保持静默

---

### 3.3 `excelmanus/engine.py` — `_tool_calling_loop()`

**位置**：第 3594-3597 行（max_iter 限制）、第 3629-3639 行（FileRegistry 跳过）、第 3762-3766 行（tools=[]）

**当前状态**：已实现原型

**三处改动**：

| 改动 | 位置 | 说明 |
|------|------|------|
| `max_iter = 1` | 3596 | chitchat 无需多轮工具迭代 |
| 跳过 `await_registry_scan` | 3633 | 仅做凭证刷新，省 100-500ms |
| `tools = []` | 3762 | 不传工具 schema，省 3K-8K tokens |

**待完善**：
1. ✅ **确认 `if tools:` 守卫**：第 3779 行 `if tools: kwargs["tools"] = tools` 已自动处理空列表，新增测试 `test_empty_tools_not_injected_to_kwargs` 确认
2. ✅ **thinking 参数**：不额外处理，主模型 thinking 配置保持不变

---

### 3.4 `excelmanus/engine_core/context_builder.py`

**位置**：`_prepare_system_prompts_for_request()` 方法，第 887-893 行

**当前状态**：已实现原型

**逻辑**（已修复）：
```python
if _route_mode == "chitchat":
    # 构建真正精简的 chitchat prompt（identity + rules + channel）
    # 不使用 stable_prompt 因为它还包含 access/backup/mcp
    _chitchat_prompt = e.memory.system_prompt
    if rules_notice:
        _chitchat_prompt += "\n\n" + rules_notice
    if channel_notice:
        _chitchat_prompt += "\n\n" + channel_notice
    return [_chitchat_prompt], None
```

**待完善**：
1. ✅ **稳定前缀内容 review**：**发现并修复根因 bug** — 原代码返回 `[stable_prompt]` 但 stable_prompt 包含 access/backup/mcp（~5-10K tokens 冗余），注释说「仅 identity+rules+channel」但实际不一致。现已改为独立构建真正精简的 chitchat prompt。
2. ✅ **更激进的裁剪**：已实现 — chitchat 路径跳过 access_notice / backup_notice / mcp_context，新增 `TestContextBuilderChitchatFastPath` 测试验证

---

## 4. 测试计划

### 4.1 已有测试

文件：`tests/test_tiered_routing.py`（已有 **162 条测试**）

| 测试类 | 覆盖内容 |
|--------|----------|
| `TestChitchatRouteMode` | 正则匹配 → route_mode="chitchat" |
| `TestChitchatSafetyGates` | 安全门控（文件路径/图片/消息长度） |
| `TestMultiTurnDowngrade` | 多轮上下文降级 |
| `TestContextBuilderChitchat` | context_builder 快速通道 |
| `TestToolCallingLoopChitchat` | tools=[] + max_iter=1 |
| `TestChitchatSemantic` | 语义级别验证（任务消息不误判） |
| `TestEdgeCases` | 边界情况 |

### 4.2 已补充的测试

1. ✅ **正则扩展测试**：`TestChitchatRegexCoverage` 含 60+ 条参数化消息（含新增确认词/告别词/感谢词 + 标点变体 + 任务消息反例）
2. ✅ **ContextBuilder 集成测试**：`TestContextBuilderChitchatFastPath` 验证 chitchat prompt 不含 access/backup/mcp
3. ✅ **降级恢复测试**：`TestChitchatDowngradeRecovery` 验证降级后上下文清空能恢复快速通道 + tool call 窗口滑出
4. ✅ **诊断捕获测试**：`TestChitchatDiagnostics` 验证降级原因 / 快速通道标记正确注入 session diagnostics
5. ✅ **边界测试增强**：51 字精确边界、大小写不敏感、子串不匹配等
6. ⬜ **性能基准测试**：对比 chitchat 快速通道 vs 全量路径的耗时（需线上环境，暂缓）

### 4.3 运行测试

```bash
# 仅运行分层路由测试
pytest tests/test_tiered_routing.py -v

# 运行完整路由测试套件（确认无回归）
pytest tests/test_tiered_routing.py tests/test_router_write_hint.py -v
```

---

## 5. 配置项

| 环境变量 | 默认值 | 说明 |
|----------|--------|------|
| （暂无） | — | 当前 chitchat 阈值硬编码 |

**建议**：
- 消息长度阈值 50 字可提取为 `EXCELMANUS_CHITCHAT_MAX_LENGTH`（优先级低，当前硬编码即可）
- 正则模式无需外部配置，代码内维护即可

---

## 6. 上线检查清单

- [x] 所有原型代码 review 通过
- [x] `_CHITCHAT_RE` 正则覆盖率验证（162 条测试用例覆盖所有分支）
- [x] 多轮安全降级 4 个条件均有测试覆盖（含优先级 + 窗口滑出）
- [x] context_builder 快速通道确认不影响非 chitchat 路径（已修复根因 bug：排除 access/backup/mcp）
- [x] `_tool_calling_loop` 中 3 处改动确认不影响非 chitchat 路径
- [x] 诊断捕获：降级原因 / 快速通道标记注入 `_session_diagnostics`
- [ ] 性能基准：chitchat 响应 < 1s（含 LLM 推理）— 需线上环境验证
- [ ] 生产日志确认：`chitchat 短路` / `chitchat 安全降级` 日志正常输出
- [ ] 灰度发布后监控：检查是否有任务消息被误判为 chitchat（关注降级率）

---

## 7. 风险与缓解

| 风险 | 影响 | 缓解措施 |
|------|------|----------|
| 任务消息被误判为 chitchat | 工具不可用，用户体验差 | 保守正则 + 4 重安全门控 + 多轮降级 |
| chitchat 消息未命中正则 | 多花 ~30K tokens，无功能损失 | 可接受，渐进扩展正则 |
| 多轮降级过于保守 | chitchat 在任务间隙仍走全量路径 | 可接受，宁可多花 token 也不误判 |
