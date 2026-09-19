"""Bug 条件探索测试 — 代码质量低优先级清理（R10 + Y7 + I10）。

任务 1：在未修复代码上运行，确认 bug 条件确实存在。
- 测试 FAILS on unfixed code → 证明 bug 存在（这是预期结果）
- 测试 PASSES after fix → 验证修复正确

**验证：需求 1.1, 1.2, 1.3, 1.4, 1.5, 1.6**
"""

from __future__ import annotations

import asyncio
import pytest


# ── 辅助：构造最小 mock stream ────────────────────────────


class _MockChunk:
    """模拟 OpenAI ChatCompletionChunk 格式。"""

    def __init__(self, content: str | None = None, finish_reason: str | None = None):
        self.choices = [_MockChoice(content=content, finish_reason=finish_reason)]
        self.usage = None


class _MockChoice:
    def __init__(self, content: str | None, finish_reason: str | None):
        self.delta = _MockDelta(content=content)
        self.finish_reason = finish_reason


class _MockDelta:
    def __init__(self, content: str | None):
        self.content = content
        self.tool_calls = None


class _MockAsyncStream:
    """最小异步迭代器，模拟流式响应。"""

    def __init__(self, chunks: list[_MockChunk]):
        self._chunks = iter(chunks)

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self._chunks)
        except StopIteration:
            raise StopAsyncIteration


# ── 辅助：构造缺少 _client 的 mock engine ────────────────


class _MockEngineNoClient:
    """模拟缺少 _client 属性的 engine（用于 I10 测试）。"""
    pass


# ═══════════════════════════════════════════════════════════
# Property 1: Fault Condition — 四个 Bug 条件验证
# ═══════════════════════════════════════════════════════════


class TestFaultCondition:
    """探索性检查：在未修复代码上确认四个 bug 条件均可被观测到。

    CRITICAL: 这些测试在未修复代码上 MUST FAIL。
    失败即证明 bug 存在，是预期的正确结果。
    修复后这些测试应全部通过。

    **验证：需求 1.1, 1.2, 1.3, 1.4, 1.5, 1.6**
    """

    def test_r10_fallback_core_files_is_empty(self):
        """R10：_FALLBACK_CORE_FILES 应为空字典（修复后）。

        未修复时：字典非空，包含硬编码的 v4.0.0 内容 → 测试 FAILS（证明 bug 存在）
        修复后：字典为空 {} → 测试 PASSES（bug 已消除）

        **验证：需求 1.1, 1.2**
        """
        from excelmanus.prompt.load import _FALLBACK_CORE_FILES

        # 修复后期望：字典为空
        assert _FALLBACK_CORE_FILES == {}, (
            f"R10 Bug 条件确认：_FALLBACK_CORE_FILES 非空，包含 {len(_FALLBACK_CORE_FILES)} 个硬编码文件。"
            f" 键名：{list(_FALLBACK_CORE_FILES.keys())}"
        )

    def test_r10_fallback_core_files_no_version_string(self):
        """R10：_FALLBACK_CORE_FILES 不应包含 '4.0.0' 版本字符串（修复后）。

        未修复时：内容包含 version: "4.0.0" 标注 → 测试 FAILS（证明版本漂移 bug 存在）
        修复后：字典为空，不含任何版本字符串 → 测试 PASSES

        **验证：需求 1.1, 1.2**
        """
        from excelmanus.prompt.load import _FALLBACK_CORE_FILES

        all_content = "\n".join(_FALLBACK_CORE_FILES.values())
        assert "4.0.0" not in all_content, (
            f"R10 Bug 条件确认：_FALLBACK_CORE_FILES 包含过时的 v4.0.0 版本字符串，"
            f" 存在版本漂移风险。"
        )

    def test_y7_stream_recorder_finalize_no_delta_chars(self):
        """Y7：_StreamRecorder._finalize 不应写入 delta_chars 字段（修复后）。

        未修复时：_finalize 写入 delta_chars 和 tool_call_deltas → 测试 FAILS（证明死代码 bug 存在）
        修复后：这两个字段被移除 → 测试 PASSES

        **验证：需求 1.4**
        """
        from excelmanus.bench import _StreamRecorder

        call_record: dict = {}
        chunks = [
            _MockChunk(content="hello"),
            _MockChunk(content=" world", finish_reason="stop"),
        ]
        stream = _MockAsyncStream(chunks)
        recorder = _StreamRecorder(stream, call_record)

        # 消费完整个流
        async def consume():
            async for _ in recorder:
                pass

        asyncio.run(consume())

        response = call_record.get("response", {})

        # 修复后期望：response 不包含 delta_chars
        assert "delta_chars" not in response, (
            f"Y7 Bug 条件确认：call_record['response'] 包含未使用的 'delta_chars' 字段，"
            f" 值为 {response.get('delta_chars')}。该字段是死代码。"
        )

        # 修复后期望：response 不包含 tool_call_deltas
        assert "tool_call_deltas" not in response, (
            f"Y7 Bug 条件确认：call_record['response'] 包含未使用的 'tool_call_deltas' 字段，"
            f" 值为 {response.get('tool_call_deltas')}。该字段是死代码。"
        )

    def test_i10_llm_call_interceptor_raises_clear_error_on_missing_client(self):
        """I10：_LLMCallInterceptor 在 engine 缺少 _client 时应抛出含明确说明的 AttributeError（修复后）。

        未修复时：抛出的 AttributeError 不包含 'bench requires' 字样 → 测试 FAILS（证明脆弱耦合 bug 存在）
        修复后：抛出包含 'bench requires engine._client' 的明确错误 → 测试 PASSES

        **验证：需求 1.5**
        """
        from excelmanus.bench import _LLMCallInterceptor

        mock_engine = _MockEngineNoClient()

        with pytest.raises(AttributeError) as exc_info:
            _LLMCallInterceptor(mock_engine)

        error_message = str(exc_info.value)

        # 修复后期望：错误信息包含 "bench requires"
        assert "bench requires" in error_message, (
            f"I10 Bug 条件确认：缺少 _client 时抛出的 AttributeError 不含明确的 bench 上下文信息。"
            f" 实际错误信息：{error_message!r}"
        )


# ═══════════════════════════════════════════════════════════
# Property 2: Preservation — 正常路径行为基线验证
# ═══════════════════════════════════════════════════════════


from hypothesis import given, settings
from hypothesis import strategies as st


# ── 辅助：构造带 usage 的 mock chunk ─────────────────────


class _MockUsage:
    """模拟 OpenAI usage 对象。"""

    def __init__(self, prompt_tokens: int, completion_tokens: int):
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.total_tokens = prompt_tokens + completion_tokens


class _MockChunkWithUsage:
    """模拟带 usage 的最后一个 chunk。"""

    def __init__(
        self,
        content: str | None = None,
        finish_reason: str | None = None,
        usage: _MockUsage | None = None,
    ):
        self.choices = [_MockChoice(content=content, finish_reason=finish_reason)]
        self.usage = usage


# ── 辅助：构造具备完整属性的 mock engine ─────────────────


class _MockClient:
    """模拟 engine._client 的最小结构。"""

    class _Chat:
        class _Completions:
            @staticmethod
            async def create(**kwargs):
                return {"mock": True}

        completions = _Completions()

    chat = _Chat()


class _MockEngineWithAllAttrs:
    """模拟具备所有 bench 所需私有属性的 engine。"""

    def __init__(self):
        self._client = _MockClient()
        self._prepare_system_prompts_for_request = self._mock_prepare

    @staticmethod
    def _mock_prepare(skill_contexts, **kwargs):
        return (["system prompt"], None)


class TestPreservation:
    """保留性属性测试：验证正常路径行为在修复前后保持一致。

    这些测试在未修复代码上 MUST PASS，建立基线。
    修复后这些测试仍应全部通过（无回归）。

    **验证：需求 3.1, 3.2, 3.3, 3.4, 3.5, 3.6**
    """

    # ── P5b: _StreamRecorder 透传所有 chunk 且正确累计指标 ──

    @given(
        n_content_chunks=st.integers(min_value=0, max_value=10),
        prompt_tokens=st.integers(min_value=0, max_value=10000),
        completion_tokens=st.integers(min_value=0, max_value=10000),
    )
    @settings(max_examples=30)
    def test_stream_recorder_preserves_chunks_and_metrics(
        self, n_content_chunks, prompt_tokens, completion_tokens
    ):
        """_StreamRecorder 透传所有 chunk，且 finish_reason/usage 正确累计。

        使用 Hypothesis 生成随机 chunk 数量和 token 计数，
        验证：
        1. 所有 chunk 被透传（数量一致）
        2. finish_reason 正确记录
        3. usage 正确累计

        **验证：需求 3.3, 3.4**
        """
        from excelmanus.bench import _StreamRecorder

        # 构造 chunk 序列
        chunks = []
        for i in range(n_content_chunks):
            chunks.append(_MockChunk(content=f"chunk_{i}"))

        # 最后一个 chunk 带 finish_reason 和 usage
        usage = _MockUsage(prompt_tokens, completion_tokens)
        chunks.append(
            _MockChunkWithUsage(
                content=None,
                finish_reason="stop",
                usage=usage,
            )
        )

        stream = _MockAsyncStream(chunks)
        call_record: dict = {}
        recorder = _StreamRecorder(stream, call_record)

        # 消费并收集透传的 chunk
        collected = []

        async def consume():
            async for chunk in recorder:
                collected.append(chunk)

        asyncio.run(consume())

        # 验证：所有 chunk 被透传
        assert len(collected) == len(chunks), (
            f"期望透传 {len(chunks)} 个 chunk，实际 {len(collected)}"
        )

        # 验证：finish_reason 正确
        response = call_record.get("response", {})
        assert response.get("finish_reason") == "stop", (
            f"期望 finish_reason='stop'，实际 {response.get('finish_reason')!r}"
        )

        # 验证：usage 正确
        resp_usage = response.get("usage", {})
        assert resp_usage.get("prompt_tokens") == prompt_tokens
        assert resp_usage.get("completion_tokens") == completion_tokens
        assert resp_usage.get("total_tokens") == prompt_tokens + completion_tokens

    # ── P5c: bench 初始化和 restore() 行为正常 ──────────

    def test_llm_call_interceptor_init_and_restore_with_valid_engine(self):
        """engine 属性均存在时，_LLMCallInterceptor 初始化正常，restore() 正确恢复。

        **验证：需求 3.5, 3.6**
        """
        from excelmanus.bench import _LLMCallInterceptor

        engine = _MockEngineWithAllAttrs()
        original_create = engine._client.chat.completions.create

        # 初始化应成功
        interceptor = _LLMCallInterceptor(engine)

        # monkey-patch 后 create 应被替换
        assert engine._client.chat.completions.create is not original_create
        assert engine._client.chat.completions.create == interceptor._intercepted_create

        # restore 后应恢复原始方法
        interceptor.restore()
        assert engine._client.chat.completions.create is original_create

    def test_engine_tracer_init_and_restore_with_valid_engine(self):
        """engine 属性均存在时，_EngineTracer 初始化正常，restore() 正确恢复。

        **验证：需求 3.5, 3.6**
        """
        from excelmanus.bench import _EngineTracer

        engine = _MockEngineWithAllAttrs()
        original_prepare = engine._prepare_system_prompts_for_request

        # 初始化应成功
        tracer = _EngineTracer(engine)

        # monkey-patch 后方法应被替换
        assert engine._prepare_system_prompts_for_request is not original_prepare

        # restore 后应恢复原始方法
        tracer.restore()
        assert engine._prepare_system_prompts_for_request is original_prepare
