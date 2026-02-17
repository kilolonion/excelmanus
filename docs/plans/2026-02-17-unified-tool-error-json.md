# 统一工具错误 JSON 透传 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 在 `registry.py` 的 `call_tool` 中统一捕获工具执行异常，返回结构化 JSON 字符串（而非抛出 ToolExecutionError），使所有工具错误以一致的格式透传给 LLM agent。

**Architecture:** 将 `call_tool` 中的 `raise ToolExecutionError` 改为返回 JSON 字符串，格式与现有的 `_format_argument_validation_error` 保持一致。同时更新 engine.py 中的异常处理逻辑，移除对 ToolExecutionError 的特殊处理。回退 `add_conditional_rule` 中冗余的工具级 try-except（因为 registry 层已统一处理）。

**Tech Stack:** Python 3.12, openpyxl, pytest

---

### Task 1: 修改 registry.py — call_tool 返回 JSON 而非抛异常

**Files:**
- Modify: `excelmanus/tools/registry.py` — `call_tool` 方法（约第 164-168 行）

**Step 1: 修改 call_tool 的异常处理**

将当前代码：
```python
        try:
            return tool.func(**arguments)
        except Exception as exc:
            raise ToolExecutionError(f"工具 '{tool_name}' 执行失败: {exc}") from exc
```

改为：
```python
        try:
            return tool.func(**arguments)
        except Exception as exc:
            logger.warning(
                "工具 '%s' 执行异常: %s; arguments=%s",
                tool_name,
                exc,
                arguments,
            )
            return self._format_execution_error(
                tool_name=tool_name,
                exc=exc,
            )
```

**Step 2: 添加 `_format_execution_error` 静态方法**

在 `_format_argument_validation_error` 方法之后添加：
```python
    @staticmethod
    def _format_execution_error(
        *,
        tool_name: str,
        exc: Exception,
    ) -> str:
        """构造统一的工具执行错误返回（JSON 字符串），透传原始异常信息。"""
        payload = {
            "status": "error",
            "error_code": "TOOL_EXECUTION_ERROR",
            "tool": tool_name,
            "exception": type(exc).__name__,
            "message": str(exc),
        }
        # 如果有链式异常（__cause__），也透传
        if exc.__cause__ is not None and str(exc.__cause__) != str(exc):
            payload["cause"] = str(exc.__cause__)
        return json.dumps(payload, ensure_ascii=False)
```

**Step 3: 运行现有测试确认影响**

Run: `pytest tests/test_skill_registry.py -v -q 2>&1 | head -40`
Expected: `test_call_tool_execution_error` 会 FAIL（因为不再抛 ToolExecutionError）

**Step 4: Commit**

```bash
git add excelmanus/tools/registry.py
git commit -m "refactor: call_tool 返回结构化 JSON 而非抛出 ToolExecutionError"
```

---

### Task 2: 更新测试 — test_call_tool_execution_error

**Files:**
- Modify: `tests/test_skill_registry.py` — `test_call_tool_execution_error`（约第 131-145 行）

**Step 1: 更新测试用例**

将当前代码：
```python
    def test_call_tool_execution_error(self) -> None:
        """工具执行异常应包装为 ToolExecutionError。"""
        def bad_func(**kwargs):
            raise ValueError("boom")

        tool = ToolDef(
            name="bad",
            description="会失败的工具",
            input_schema={"type": "object", "properties": {}},
            func=bad_func,
        )
        registry = ToolRegistry()
        registry.register_tools([tool])
        with pytest.raises(ToolExecutionError, match="boom"):
            registry.call_tool("bad", {})
```

改为：
```python
    def test_call_tool_execution_error(self) -> None:
        """工具执行异常应返回结构化 JSON 错误（不再抛异常）。"""
        def bad_func(**kwargs):
            raise ValueError("boom")

        tool = ToolDef(
            name="bad",
            description="会失败的工具",
            input_schema={"type": "object", "properties": {}},
            func=bad_func,
        )
        registry = ToolRegistry()
        registry.register_tools([tool])
        result = registry.call_tool("bad", {})
        parsed = json.loads(result)
        assert parsed["status"] == "error"
        assert parsed["error_code"] == "TOOL_EXECUTION_ERROR"
        assert parsed["tool"] == "bad"
        assert parsed["exception"] == "ValueError"
        assert "boom" in parsed["message"]
```

注意：测试文件顶部需要 `import json`（如果还没有的话）。

**Step 2: 运行测试**

Run: `pytest tests/test_skill_registry.py -v -q`
Expected: ALL PASS

**Step 3: Commit**

```bash
git add tests/test_skill_registry.py
git commit -m "test: 更新 test_call_tool_execution_error 适配 JSON 返回"
```

---

### Task 3: 更新 engine.py — 移除冗余的 ToolExecutionError 捕获

**Files:**
- Modify: `excelmanus/engine.py` — `_execute_tool_call` 方法中的 except 块（约第 3930-3950 行）

**Step 1: 理解当前 engine 异常处理**

当前 engine.py 的 except 链：
```python
except ValueError as exc:
    result_str = str(exc)
    success = False
    error = result_str
except ToolNotAllowedError:
    # JSON 格式
except Exception as exc:
    result_str = f"工具执行错误: {exc}"
    success = False
    error = str(exc)
```

由于 registry 不再抛 ToolExecutionError，`except Exception` 块理论上只会捕获 `_call_registry_tool` 或 `_execute_tool_with_audit` 中的非工具异常（如 asyncio 错误、线程池错误等）。

**不需要删除 `except Exception` 块**——它仍然是兜底保护。但需要确认：当 registry 返回 JSON 错误字符串时，engine 会把它当作成功结果（`success = True`），因为没有异常抛出。

这正是我们想要的行为：工具返回了结果（虽然是错误 JSON），engine 把它作为 tool message 返回给 LLM，LLM 解析 JSON 后可以自行决定重试。

**Step 2: 无需修改 engine.py**

当前逻辑已经兼容：
- registry 返回 JSON 字符串 → engine `str(result_value)` → `success = True` → LLM 收到 JSON
- LLM 看到 `"status": "error"` 就知道工具失败了

但有一个问题：`success = True` 会导致 POST_TOOL_USE 事件（而非 POST_TOOL_USE_FAILURE）。这其实是合理的——工具"执行"了，只是结果是错误。但如果需要区分，可以在 engine 层检测返回值是否包含 `"status": "error"`。

**决策：暂不修改 engine.py**，保持兼容。后续如果需要区分成功/失败的 hook 事件，再单独处理。

**Step 3: 运行 engine 测试确认无回归**

Run: `pytest tests/test_engine.py -v -q -x 2>&1 | tail -20`
Expected: 关注 `test_tool_execution_error` 相关测试

---

### Task 4: 回退 add_conditional_rule 中冗余的工具级 try-except

**Files:**
- Modify: `excelmanus/tools/advanced_format_tools.py` — `add_conditional_rule` 函数（约第 628-725 行）

**Step 1: 移除工具级 try-except**

由于 registry 层已统一处理异常，`add_conditional_rule` 中的 try-except 变得冗余。将：

```python
    rule_detail = rule_type

    try:
        if rule_type == "cell_is":
            ...
        ws.conditional_formatting.add(cell_range, rule)
        wb.save(safe_path)
        wb.close()
        logger.info(...)
        return json.dumps({"status": "success", ...})
    except Exception as exc:
        wb.close()
        return json.dumps({"error": ..., "exception": ..., "detail": ...})
```

改回为：
```python
    rule_detail = rule_type

    if rule_type == "cell_is":
        ...
    ws.conditional_formatting.add(cell_range, rule)
    wb.save(safe_path)
    wb.close()
    logger.info(...)
    return json.dumps({"status": "success", ...})
```

**注意**：移除 try-except 后，如果异常发生，`wb.close()` 不会被调用。但 registry 层的 `_format_execution_error` 也无法调用 `wb.close()`。

**重新考虑**：保留 try-except 但只做资源清理（`wb.close()`），然后 re-raise 让 registry 层统一格式化：

```python
    rule_detail = rule_type

    try:
        if rule_type == "cell_is":
            ...
        ws.conditional_formatting.add(cell_range, rule)
        wb.save(safe_path)
        wb.close()
    except Exception:
        wb.close()
        raise

    logger.info(...)
    return json.dumps({"status": "success", ...})
```

这样既保证资源清理，又让 registry 层统一格式化错误。

**Step 2: 运行测试**

Run: `pytest tests/ -k "conditional" -v -q`
Expected: ALL PASS

**Step 3: Commit**

```bash
git add excelmanus/tools/advanced_format_tools.py
git commit -m "refactor: add_conditional_rule 回退工具级错误格式化，改为 re-raise 让 registry 统一处理"
```

---

### Task 5: 运行完整测试套件验证

**Step 1: 运行全量测试**

Run: `pytest tests/ -v -q --tb=short 2>&1 | tail -30`
Expected: ALL PASS（或仅有预期的 skip/xfail）

**Step 2: 如有失败，检查是否与 ToolExecutionError 相关**

重点关注：
- `tests/test_engine.py` 中引用 `ToolExecutionError` 的测试
- `tests/test_subagent_executor.py` 中检查 `error_type == "ToolExecutionError"` 的断言

这些测试可能需要更新：
- `test_engine.py:5355` — `assert data["execution"]["error_type"] == "ToolExecutionError"`
- `test_subagent_executor.py:434` — `assert data["execution"]["error_type"] == "ToolExecutionError"`

如果这些测试失败，说明 approval 层的 `execute_and_audit` 捕获异常时记录了 `error_type`。由于 registry 不再抛异常，approval 层也不会捕获到异常，审计记录的 `execution_status` 会变为 "success"（即使工具返回了错误 JSON）。

**处理方式**：这些测试需要更新，因为工具执行"成功"了（返回了结果），只是结果内容是错误信息。

**Step 3: Commit**

```bash
git add -A
git commit -m "test: 修复因 ToolExecutionError 行为变更导致的测试失败"
```
