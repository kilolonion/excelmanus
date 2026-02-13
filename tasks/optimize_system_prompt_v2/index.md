# 系统提示词优化 v2

## 状态：✅ 已完成

## 背景

参考 Claude Code、OpenAI GPT-5 Prompting Guide、Cursor 等主流 Agent 的公开最佳实践，对 ExcelManus 主系统提示词和子代理提示词进行结构化升级。

## 调研来源

- Claude Code Best Practices: https://code.claude.com/docs/en/best-practices
- Claude API Prompting Best Practices: https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices
- OpenAI GPT-5 Prompting Guide: https://developers.openai.com/cookbook/examples/gpt-5/gpt-5_prompting_guide

## 核心改动

### 1. 主系统提示词（`excelmanus/memory.py`）

**Before**：能力清单式，列举"可以做什么"  
**After**：分段协议式，定义"如何做"

新结构：
- **工作循环**：探索 → 计划 → 执行 → 验证 → 汇报（5步闭环）
- **工具策略**：不猜测、先读后写、意图明确时默认执行、工具前导语
- **安全策略**：可逆直接执行 / 高风险需确认 / 权限限制透明
- **输出要求**：结果摘要 + 关键证据 + 简洁

### 2. 子代理提示词（`excelmanus/subagent/builtin.py`）

四个内置子代理（explorer / analyst / writer / coder）统一加入：
- `## 完成标准`：明确输出必须包含什么
- `## 失败策略`：失败时应如何汇报，避免静默或死循环

### 3. 子代理执行器默认模板（`excelmanus/subagent/executor.py`）

未配置 system_prompt 的子代理，默认模板从一句话升级为含"工作规范"的结构化模板。

### 4. 测试修复（`tests/test_memory.py`）

新提示词更长（~180 tokens vs 旧版 ~80 tokens），两个截断测试的硬编码阈值需上调：
- `test_truncation_removes_oldest_first`: 200 → 400
- `test_single_huge_message_is_shrunk_to_threshold`: 100 → 250

## 测试结果

590 passed, 0 failed

## 后续可选优化（P1/P2）

- **P1**：`agent_eagerness` 配置开关（cautious/balanced/proactive）
- **P1**：`tool_preamble` 配置开关（off/brief/verbose）
- **P2**：上下文采集策略提示（`<context_gathering>` 块，参考 GPT-5 指南）
- **P2**：并行工具调用显式提示（`<use_parallel_tool_calls>` 块，参考 Claude 指南）
