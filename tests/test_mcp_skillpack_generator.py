"""MCP Skillpack 自动生成器单元测试。

覆盖：指纹计算、缓存读写、LLM 生成、回退逻辑、后台刷新。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from excelmanus.mcp.skillpack_generator import (
    MCPSkillpackGenerator,
    _llm_call,
    _llm_generate_and_validate,
    _cache_dict_to_skillpack,
    _extract_json,
    _new_diagnostics,
    _skillpack_to_cache_dict,
    _validate_skillpack_fields,
    _validate_and_build,
    compute_fingerprint,
    generate_skillpack_with_llm,
    load_cache,
    save_cache,
    _CACHE_VERSION,
)
from excelmanus.skillpacks.models import Skillpack


# ── 辅助工厂 ──────────────────────────────────────────────


def _make_mcp_tool(
    name: str = "tool_a",
    description: str = "工具描述",
    input_schema: dict | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        name=name,
        description=description,
        inputSchema=input_schema or {"type": "object", "properties": {}},
    )


def _make_skillpack(name: str = "mcp_test", **overrides) -> Skillpack:
    defaults = dict(
        name=name,
        description="测试描述",
        allowed_tools=["mcp:test:*"],
        triggers=["测试"],
        instructions="测试指引",
        source="system",
        root_dir="",
        priority=3,
        version="1.0.0",
    )
    defaults.update(overrides)
    return Skillpack(**defaults)


def _make_llm_response(content: str) -> MagicMock:
    """构造模拟的 LLM chat completion 响应。"""
    choice = MagicMock()
    choice.message.content = content
    response = MagicMock()
    response.choices = [choice]
    return response


def _actionable_instructions(prefix: str = "mcp_test_") -> str:
    """构造满足可执行性校验的 instructions。"""
    return (
        f"工具前缀：{prefix}。"
        "推荐调用顺序：先执行读取，再执行写入，最后复核结果。"
        "错误处理：失败时先检查参数类型和字段名，再按顺序重试。"
    )


# ══════════════════════════════════════════════════════════
# 测试 1：指纹计算
# ══════════════════════════════════════════════════════════


class TestComputeFingerprint:

    def test_same_tools_same_fingerprint(self):
        """相同工具集应产生相同指纹。"""
        tools = [_make_mcp_tool("a", "desc_a"), _make_mcp_tool("b", "desc_b")]
        assert compute_fingerprint(tools) == compute_fingerprint(tools)

    def test_different_tools_different_fingerprint(self):
        """不同工具集应产生不同指纹。"""
        tools1 = [_make_mcp_tool("a", "desc_a")]
        tools2 = [_make_mcp_tool("a", "desc_a"), _make_mcp_tool("b", "desc_b")]
        assert compute_fingerprint(tools1) != compute_fingerprint(tools2)

    def test_description_change_changes_fingerprint(self):
        """工具描述变化应改变指纹。"""
        tools1 = [_make_mcp_tool("a", "old")]
        tools2 = [_make_mcp_tool("a", "new")]
        assert compute_fingerprint(tools1) != compute_fingerprint(tools2)

    def test_order_independent(self):
        """工具顺序不影响指纹。"""
        tools1 = [_make_mcp_tool("a", "x"), _make_mcp_tool("b", "y")]
        tools2 = [_make_mcp_tool("b", "y"), _make_mcp_tool("a", "x")]
        assert compute_fingerprint(tools1) == compute_fingerprint(tools2)

    def test_empty_tools(self):
        """空工具列表应返回有效指纹。"""
        fp = compute_fingerprint([])
        assert isinstance(fp, str) and len(fp) == 16

    def test_fingerprint_length(self):
        """指纹应为 16 位十六进制字符串。"""
        fp = compute_fingerprint([_make_mcp_tool()])
        assert len(fp) == 16
        assert all(c in "0123456789abcdef" for c in fp)


# ══════════════════════════════════════════════════════════
# 测试 2：缓存读写
# ══════════════════════════════════════════════════════════


class TestCacheReadWrite:

    def test_round_trip(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """写入后读回应完全一致。"""
        cache_file = tmp_path / "cache.json"
        monkeypatch.setattr(
            "excelmanus.mcp.skillpack_generator._CACHE_FILE", cache_file
        )
        monkeypatch.setattr(
            "excelmanus.mcp.skillpack_generator._CACHE_DIR", tmp_path
        )

        data = {
            "version": _CACHE_VERSION,
            "servers": {
                "context7": {
                    "fingerprint": "abc123",
                    "skillpack": {"name": "mcp_context7", "description": "测试"},
                }
            },
        }
        save_cache(data)
        loaded = load_cache()

        assert loaded == data

    def test_missing_file_returns_empty(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """文件不存在时返回空结构。"""
        monkeypatch.setattr(
            "excelmanus.mcp.skillpack_generator._CACHE_FILE",
            tmp_path / "nonexistent.json",
        )
        result = load_cache()
        assert result == {"version": _CACHE_VERSION, "servers": {}}

    def test_invalid_json_returns_empty(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """JSON 格式错误时返回空结构。"""
        bad_file = tmp_path / "bad.json"
        bad_file.write_text("{invalid!!!", encoding="utf-8")
        monkeypatch.setattr(
            "excelmanus.mcp.skillpack_generator._CACHE_FILE", bad_file
        )
        result = load_cache()
        assert result == {"version": _CACHE_VERSION, "servers": {}}

    def test_version_mismatch_returns_empty(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """版本不匹配时返回空结构。"""
        cache_file = tmp_path / "old.json"
        cache_file.write_text(
            json.dumps({"version": 999, "servers": {"x": {}}}),
            encoding="utf-8",
        )
        monkeypatch.setattr(
            "excelmanus.mcp.skillpack_generator._CACHE_FILE", cache_file
        )
        result = load_cache()
        assert result["servers"] == {}


# ══════════════════════════════════════════════════════════
# 测试 3：Skillpack 序列化
# ══════════════════════════════════════════════════════════


class TestSkillpackSerialization:

    def test_round_trip(self):
        """Skillpack → dict → Skillpack 应保留关键字段。"""
        sp = _make_skillpack(
            name="mcp_git",
            description="Git 操作",
            triggers=["git", "提交"],
            instructions="使用 git 工具",
        )
        d = _skillpack_to_cache_dict(sp)
        restored = _cache_dict_to_skillpack(d)

        assert restored.name == sp.name
        assert restored.description == sp.description
        assert restored.triggers == sp.triggers
        assert restored.instructions == sp.instructions
        assert restored.allowed_tools == sp.allowed_tools
        assert restored.source == "system"
        assert restored.user_invocable is True


# ══════════════════════════════════════════════════════════
# 测试 3b：JSON 提取（_extract_json）
# ══════════════════════════════════════════════════════════


class TestExtractJson:

    def test_plain_json(self):
        """纯 JSON 直接解析。"""
        raw = '{"description": "测试", "triggers": [], "instructions": "指引"}'
        assert _extract_json(raw) == {"description": "测试", "triggers": [], "instructions": "指引"}

    def test_markdown_wrapped(self):
        """markdown 代码块包裹的 JSON。"""
        raw = '```json\n{"description": "测试"}\n```'
        assert _extract_json(raw) == {"description": "测试"}

    def test_markdown_no_lang(self):
        """无语言标识的 markdown 代码块。"""
        raw = '```\n{"description": "x"}\n```'
        assert _extract_json(raw) == {"description": "x"}

    def test_json_with_preamble(self):
        """JSON 前面有废话文字。"""
        raw = '好的，以下是生成结果：\n{"description": "测试", "triggers": ["a"]}'
        result = _extract_json(raw)
        assert result is not None
        assert result["description"] == "测试"

    def test_json_with_trailing_text(self):
        """JSON 后面有额外文字。"""
        raw = '{"description": "x", "triggers": []}\n希望对你有帮助！'
        result = _extract_json(raw)
        assert result is not None
        assert result["description"] == "x"

    def test_completely_invalid(self):
        """完全无效的内容返回 None。"""
        assert _extract_json("这不是JSON") is None

    def test_empty_string(self):
        """空字符串返回 None。"""
        assert _extract_json("") is None

    def test_json_array_rejected(self):
        """JSON 数组不应被接受。"""
        assert _extract_json('[1, 2, 3]') is None

    def test_nested_json(self):
        """嵌套 JSON 对象。"""
        raw = '{"description": "x", "triggers": ["a"], "instructions": "y"}'
        result = _extract_json(raw)
        assert result is not None
        assert result["description"] == "x"

    def test_unescaped_newline_in_string_value(self):
        """LLM 在字符串值内输出未转义换行符时应修复后解析。"""
        # 模拟 kimi-k2.5 的实际输出：description 中含字面换行
        raw = '{"description": "Excel 数据处理，支持读写、\n格式化及管理", "triggers": ["Excel"], "instructions": "指引内容"}'
        result = _extract_json(raw)
        assert result is not None
        assert "Excel" in result["description"]
        assert result["triggers"] == ["Excel"]

    def test_multiple_unescaped_newlines(self):
        """多处未转义换行符。"""
        raw = '{"description": "行1\n行2\n行3", "triggers": ["a",\n"b"], "instructions": "x\ny"}'
        result = _extract_json(raw)
        assert result is not None
        assert result["description"] == "行1 行2 行3"


# ══════════════════════════════════════════════════════════
# 测试 3c：字段校验（_validate_and_build）
# ══════════════════════════════════════════════════════════


class TestValidateAndBuild:

    def test_valid_data(self):
        """合法数据应构建 Skillpack。"""
        data = {
            "description": "测试描述",
            "triggers": ["关键词1", "关键词2"],
            "instructions": _actionable_instructions("mcp_test_server_"),
        }
        sp = _validate_and_build(data, "test-server", "test_server")
        assert sp is not None
        assert sp.name == "mcp_test_server"
        assert sp.description == "测试描述"
        assert sp.triggers == ["关键词1", "关键词2"]
        assert sp.allowed_tools == ["mcp:test-server:*"]

    def test_missing_description(self):
        """缺少 description 应返回 None。"""
        data = {"triggers": ["a"], "instructions": "指引"}
        assert _validate_and_build(data, "s", "s") is None

    def test_empty_description(self):
        """空 description 应返回 None。"""
        data = {"description": "", "triggers": [], "instructions": "指引"}
        assert _validate_and_build(data, "s", "s") is None

    def test_missing_instructions(self):
        """缺少 instructions 应返回 None。"""
        data = {"description": "desc", "triggers": ["a"]}
        assert _validate_and_build(data, "s", "s") is None

    def test_empty_instructions(self):
        """空 instructions 应返回 None。"""
        data = {"description": "desc", "triggers": [], "instructions": "   "}
        assert _validate_and_build(data, "s", "s") is None

    def test_description_truncated(self):
        """超长 description 应被截断。"""
        data = {
            "description": "A" * 100,
            "triggers": [],
            "instructions": _actionable_instructions("mcp_s_"),
        }
        sp = _validate_and_build(data, "s", "s")
        assert sp is not None
        assert len(sp.description) <= 80
        assert sp.description.endswith("...")

    def test_instructions_truncated(self):
        """超长 instructions 应被截断。"""
        data = {
            "description": "desc",
            "triggers": [],
            "instructions": _actionable_instructions("mcp_s_") + ("X" * 3000),
        }
        sp = _validate_and_build(data, "s", "s")
        assert sp is not None
        assert len(sp.instructions) <= 2000
        assert sp.instructions.endswith("...")

    def test_trigger_too_long_filtered(self):
        """超过 10 字的 trigger 应被过滤。"""
        data = {
            "description": "desc",
            "triggers": ["短词", "这是一个超长的触发关键词不应保留"],
            "instructions": _actionable_instructions("mcp_s_"),
        }
        sp = _validate_and_build(data, "s", "s")
        assert sp is not None
        assert sp.triggers == ["短词"]

    def test_triggers_capped_at_15(self):
        """triggers 数量应限制在 15 个以内。"""
        data = {
            "description": "desc",
            "triggers": [f"词{i}" for i in range(30)],
            "instructions": _actionable_instructions("mcp_s_"),
        }
        sp = _validate_and_build(data, "s", "s")
        assert sp is not None
        assert len(sp.triggers) == 15

    def test_non_list_triggers_ignored(self):
        """triggers 不是列表时应降级为空列表。"""
        data = {
            "description": "desc",
            "triggers": "not a list",
            "instructions": _actionable_instructions("mcp_s_"),
        }
        sp = _validate_and_build(data, "s", "s")
        assert sp is not None
        assert sp.triggers == []

    def test_non_string_trigger_items_filtered(self):
        """triggers 中的非字符串项应被过滤。"""
        data = {
            "description": "desc",
            "triggers": ["正常", 123, None, "也正常"],
            "instructions": _actionable_instructions("mcp_s_"),
        }
        sp = _validate_and_build(data, "s", "s")
        assert sp is not None
        assert sp.triggers == ["正常", "也正常"]

    def test_triggers_deduplicated_keep_order(self):
        """triggers 应去重并保持首次出现顺序。"""
        data = {
            "description": "desc",
            "triggers": ["A", "B", "A", "C", "B"],
            "instructions": _actionable_instructions("mcp_s_"),
        }
        sp = _validate_and_build(data, "s", "s")
        assert sp is not None
        assert sp.triggers == ["A", "B", "C"]

    def test_instruction_actionability_rejected(self):
        """instructions 不可执行时应返回明确错误码。"""
        normalized, reason_code, reason_text = _validate_skillpack_fields(
            {
                "description": "desc",
                "triggers": ["A"],
                "instructions": "请按需调用工具。",
            },
            server_name="test",
            silent=True,
        )
        assert normalized is None
        assert reason_code == "instructions_not_actionable"
        assert "缺失" in reason_text

    def test_instruction_actionability_passed(self):
        """命中工具前缀/顺序/错误处理中的两类以上应通过。"""
        normalized, reason_code, _ = _validate_skillpack_fields(
            {
                "description": "desc",
                "triggers": ["A"],
                "instructions": _actionable_instructions("mcp_s_"),
            },
            server_name="test",
            silent=True,
        )
        assert reason_code == "ok"
        assert normalized is not None


# ══════════════════════════════════════════════════════════
# 测试 4：LLM 生成
# ══════════════════════════════════════════════════════════


class TestGenerateWithLLM:

    @pytest.mark.asyncio
    async def test_successful_generation(self):
        """LLM 返回合法 JSON 时应生成 Skillpack。"""
        llm_output = json.dumps({
            "description": "查询第三方库文档",
            "triggers": ["文档", "查文档", "API"],
            "instructions": _actionable_instructions("mcp_context7_"),
        })
        mock_client = AsyncMock()
        mock_client.chat.completions.create.return_value = _make_llm_response(llm_output)

        sp = await generate_skillpack_with_llm(
            client=mock_client,
            model="test-model",
            server_name="context7",
            normalized_name="context7",
            tools=[_make_mcp_tool("resolve-library-id"), _make_mcp_tool("query-docs")],
        )

        assert sp is not None
        assert sp.name == "mcp_context7"
        assert sp.description == "查询第三方库文档"
        assert "文档" in sp.triggers
        assert sp.allowed_tools == ["mcp:context7:*"]

    @pytest.mark.asyncio
    async def test_markdown_wrapped_json(self):
        """LLM 返回 markdown 包裹的 JSON 时应正确解析。"""
        markdown_obj = {
            "description": "Git 版本控制",
            "triggers": ["git"],
            "instructions": _actionable_instructions("mcp_git_"),
        }
        llm_output = "```json\n" + json.dumps(markdown_obj, ensure_ascii=False) + "\n```"
        mock_client = AsyncMock()
        mock_client.chat.completions.create.return_value = _make_llm_response(llm_output)

        sp = await generate_skillpack_with_llm(
            client=mock_client,
            model="test-model",
            server_name="git",
            normalized_name="git",
            tools=[_make_mcp_tool("git_status")],
        )

        assert sp is not None
        assert sp.description == "Git 版本控制"

    @pytest.mark.asyncio
    async def test_invalid_json_returns_none(self):
        """LLM 返回非 JSON 时应返回 None。"""
        mock_client = AsyncMock()
        mock_client.chat.completions.create.return_value = _make_llm_response("这不是JSON")

        sp = await generate_skillpack_with_llm(
            client=mock_client,
            model="test-model",
            server_name="test",
            normalized_name="test",
            tools=[_make_mcp_tool()],
        )

        assert sp is None

    @pytest.mark.asyncio
    async def test_empty_description_returns_none(self):
        """LLM 返回空 description 时应返回 None。"""
        llm_output = json.dumps({
            "description": "",
            "triggers": [],
            "instructions": "some instructions",
        })
        mock_client = AsyncMock()
        mock_client.chat.completions.create.return_value = _make_llm_response(llm_output)

        sp = await generate_skillpack_with_llm(
            client=mock_client,
            model="test-model",
            server_name="test",
            normalized_name="test",
            tools=[_make_mcp_tool()],
        )

        assert sp is None

    @pytest.mark.asyncio
    async def test_api_exception_returns_none(self):
        """LLM API 异常时应返回 None。"""
        mock_client = AsyncMock()
        mock_client.chat.completions.create.side_effect = RuntimeError("API down")

        sp = await generate_skillpack_with_llm(
            client=mock_client,
            model="test-model",
            server_name="test",
            normalized_name="test",
            tools=[_make_mcp_tool()],
        )

        assert sp is None

    @pytest.mark.asyncio
    async def test_step1_parse_retry_can_recover(self):
        """Step1 首次不可解析时，应可通过纠错重试恢复。"""
        mock_client = AsyncMock()
        mock_client.chat.completions.create.side_effect = [
            _make_llm_response("not json"),  # Step1 首次失败
            _make_llm_response(  # Step1 重试成功
                json.dumps({
                    "description": "文档工具",
                    "triggers": ["文档"],
                })
            ),
            _make_llm_response(  # Step2 成功
                json.dumps({"instructions": _actionable_instructions("mcp_context7_")})
            ),
        ]

        sp = await generate_skillpack_with_llm(
            client=mock_client,
            model="test-model",
            server_name="context7",
            normalized_name="context7",
            tools=[_make_mcp_tool("resolve-library-id"), _make_mcp_tool("query-docs")],
        )

        assert sp is not None
        assert sp.description == "文档工具"
        assert mock_client.chat.completions.create.await_count == 3

    @pytest.mark.asyncio
    async def test_step2_parse_retry_can_recover(self):
        """Step2 首次缺少 instructions 时，应尝试纠错重试。"""
        mock_client = AsyncMock()
        mock_client.chat.completions.create.side_effect = [
            _make_llm_response(  # Step1 成功
                json.dumps({
                    "description": "文档工具",
                    "triggers": ["文档"],
                })
            ),
            _make_llm_response(  # Step2 首次失败（无 instructions）
                json.dumps({"foo": "bar"})
            ),
            _make_llm_response(  # Step2 重试成功
                json.dumps({"instructions": _actionable_instructions("mcp_context7_")})
            ),
        ]

        sp = await generate_skillpack_with_llm(
            client=mock_client,
            model="test-model",
            server_name="context7",
            normalized_name="context7",
            tools=[_make_mcp_tool("resolve-library-id"), _make_mcp_tool("query-docs")],
        )

        assert sp is not None
        assert "工具前缀" in sp.instructions
        assert mock_client.chat.completions.create.await_count == 3

    @pytest.mark.asyncio
    async def test_step2_fallback_uses_template_when_still_invalid(self):
        """Step2 连续失败时应使用稳定模板指引，而非空/一句话。"""
        mock_client = AsyncMock()
        mock_client.chat.completions.create.side_effect = [
            _make_llm_response(  # Step1 成功
                json.dumps({
                    "description": "文档工具",
                    "triggers": ["文档"],
                })
            ),
            _make_llm_response("not json"),  # Step2 首次失败
            _make_llm_response("still bad"),  # Step2 重试失败
        ]

        sp = await generate_skillpack_with_llm(
            client=mock_client,
            model="test-model",
            server_name="context7",
            normalized_name="context7",
            tools=[_make_mcp_tool("resolve-library-id", "解析库 ID")],
        )

        assert sp is not None
        assert "调用前缀" in sp.instructions
        assert "resolve-library-id" in sp.instructions

    @pytest.mark.asyncio
    async def test_diagnostics_counts_for_step1_repair_success(self):
        """Step1 失败后修复成功应更新 step1_fail 与 repair_success。"""
        mock_client = AsyncMock()
        mock_client.chat.completions.create.side_effect = [
            _make_llm_response("not json"),
            _make_llm_response(
                json.dumps({"description": "文档工具", "triggers": ["文档"]})
            ),
            _make_llm_response(
                json.dumps({"instructions": _actionable_instructions("mcp_context7_")})
            ),
        ]
        diag = _new_diagnostics()

        sp = await generate_skillpack_with_llm(
            client=mock_client,
            model="test-model",
            server_name="context7",
            normalized_name="context7",
            tools=[_make_mcp_tool("resolve-library-id"), _make_mcp_tool("query-docs")],
            diagnostics=diag,
        )

        assert sp is not None
        assert diag["step1_fail"] == 1
        assert diag["repair_success"] == 1
        assert diag["step2_fail"] == 0
        assert diag["fallback_used"] == 0

    @pytest.mark.asyncio
    async def test_diagnostics_counts_for_step2_fallback(self):
        """Step2 修复失败走模板兜底时应更新 step2_fail 与 fallback_used。"""
        mock_client = AsyncMock()
        mock_client.chat.completions.create.side_effect = [
            _make_llm_response(
                json.dumps({"description": "文档工具", "triggers": ["文档"]})
            ),
            _make_llm_response(json.dumps({"instructions": "请按需调用工具。"})),
            _make_llm_response(json.dumps({"instructions": "还是不够具体。"})),
        ]
        diag = _new_diagnostics()

        sp = await generate_skillpack_with_llm(
            client=mock_client,
            model="test-model",
            server_name="context7",
            normalized_name="context7",
            tools=[_make_mcp_tool("resolve-library-id"), _make_mcp_tool("query-docs")],
            diagnostics=diag,
        )

        assert sp is not None
        assert "调用前缀" in sp.instructions
        assert diag["step2_fail"] == 1
        assert diag["fallback_used"] == 1


# ══════════════════════════════════════════════════════════
# 测试 4b-0：_llm_generate_and_validate 辅助函数
# ══════════════════════════════════════════════════════════


class TestLLMGenerateAndValidate:

    _SYS_MSG = {"role": "system", "content": "你是 JSON 生成器。"}
    _VALIDATE_STEP1 = {
        "require_description": True,
        "require_instructions": False,
        "enforce_instruction_actionability": False,
    }

    @pytest.mark.asyncio
    async def test_first_attempt_success(self):
        """首次 LLM 调用成功且校验通过，应直接返回。"""
        mock_client = AsyncMock()
        mock_client.chat.completions.create.return_value = _make_llm_response(
            '{"description": "测试描述信息", "triggers": ["关键词"]}'
        )
        diag = _new_diagnostics()

        result, code, text = await _llm_generate_and_validate(
            mock_client, "m", self._SYS_MSG, "prompt",
            timeout=10.0, max_tokens=300, server_name="s",
            silent=False, validate_kwargs=self._VALIDATE_STEP1,
            diag=diag, fail_key="step1_fail",
        )

        assert result is not None
        assert code == "ok"
        assert result["description"] == "测试描述信息"
        # 只调了 1 次 LLM（无重试）
        assert mock_client.chat.completions.create.await_count == 1
        assert diag["step1_fail"] == 0
        assert diag["repair_success"] == 0

    @pytest.mark.asyncio
    async def test_first_fail_retry_succeeds(self):
        """首次校验失败后纠错重试成功。"""
        mock_client = AsyncMock()
        mock_client.chat.completions.create.side_effect = [
            _make_llm_response('{"bad": true}'),  # 首次校验不过
            _make_llm_response('{"description": "修复后", "triggers": ["a"]}'),
        ]
        diag = _new_diagnostics()

        result, code, _ = await _llm_generate_and_validate(
            mock_client, "m", self._SYS_MSG, "prompt",
            timeout=10.0, max_tokens=300, server_name="s",
            silent=False, validate_kwargs=self._VALIDATE_STEP1,
            diag=diag, fail_key="step1_fail",
        )

        assert result is not None
        assert code == "ok"
        assert mock_client.chat.completions.create.await_count == 2
        assert diag["step1_fail"] == 1
        assert diag["repair_success"] == 1

    @pytest.mark.asyncio
    async def test_llm_call_returns_none(self):
        """LLM 调用本身失败（超时等），不重试直接返回 None。"""
        mock_client = AsyncMock()
        mock_client.chat.completions.create.side_effect = TimeoutError("timeout")
        diag = _new_diagnostics()

        result, code, _ = await _llm_generate_and_validate(
            mock_client, "m", self._SYS_MSG, "prompt",
            timeout=0.01, max_tokens=300, server_name="s",
            silent=True, validate_kwargs=self._VALIDATE_STEP1,
            diag=diag, fail_key="step1_fail",
        )

        assert result is None
        assert code == "llm_call_failed"
        # fail_key 不应被计数（LLM 调用层失败，不是校验失败）
        assert diag["step1_fail"] == 0

    @pytest.mark.asyncio
    async def test_both_attempts_fail(self):
        """首次和纠错重试都校验失败，返回 None。"""
        mock_client = AsyncMock()
        mock_client.chat.completions.create.side_effect = [
            _make_llm_response('{"bad": true}'),
            _make_llm_response('{"still_bad": true}'),
        ]
        diag = _new_diagnostics()

        result, code, _ = await _llm_generate_and_validate(
            mock_client, "m", self._SYS_MSG, "prompt",
            timeout=10.0, max_tokens=300, server_name="s",
            silent=True, validate_kwargs=self._VALIDATE_STEP1,
            diag=diag, fail_key="step1_fail",
        )

        assert result is None
        assert mock_client.chat.completions.create.await_count == 2
        assert diag["step1_fail"] == 1
        assert diag["repair_success"] == 0


# ══════════════════════════════════════════════════════════
# 测试 4b：底层 LLM 调用降级（_llm_call）
# ══════════════════════════════════════════════════════════


class TestLLMCall:

    @pytest.mark.asyncio
    async def test_param_fallback_chain_reaches_minimal_call(self):
        """参数不兼容时应按降级链重试到最小参数调用。

        expect_json=True 降级链（4 条）：
        0: response_format + max_tokens
        1: response_format
        2: max_tokens
        3: {}（最小化）
        """
        mock_client = AsyncMock()
        mock_client.chat.completions.create.side_effect = [
            RuntimeError("invalid param: response_format"),
            RuntimeError("invalid param: response_format"),
            RuntimeError("invalid param: max_tokens"),
            _make_llm_response('{"ok": true}'),
        ]

        text = await _llm_call(
            client=mock_client,
            model="test-model",
            messages=[{"role": "user", "content": "x"}],
            max_tokens=123,
            server_name="context7",
            expect_json=True,
        )

        assert text == '{"ok": true}'
        assert mock_client.chat.completions.create.await_count == 4
        calls = mock_client.chat.completions.create.await_args_list
        # 第 0 条：response_format + max_tokens
        assert "response_format" in calls[0].kwargs
        assert "max_tokens" in calls[0].kwargs
        # 第 1 条：response_format（去掉 max_tokens）
        assert "response_format" in calls[1].kwargs
        assert "max_tokens" not in calls[1].kwargs
        # 第 2 条：max_tokens（去掉 response_format）
        assert "response_format" not in calls[2].kwargs
        assert "max_tokens" in calls[2].kwargs
        # 第 3 条：最小化
        assert "response_format" not in calls[3].kwargs
        assert "max_tokens" not in calls[3].kwargs

    @pytest.mark.asyncio
    async def test_list_content_is_joined_text(self):
        """content 为列表块时应正确拼接 text。"""
        mock_client = AsyncMock()
        mock_client.chat.completions.create.return_value = _make_llm_response(
            [{"type": "output_text", "text": '{"a":'}, {"type": "output_text", "text": '"b"}'}]
        )

        text = await _llm_call(
            client=mock_client,
            model="test-model",
            messages=[{"role": "user", "content": "x"}],
        )
        assert text == '{"a":"b"}'

    @pytest.mark.asyncio
    async def test_expect_json_empty_content_retries_next_param_chain(self):
        """expect_json=True 且返回空 content 时应继续降级重试。"""
        mock_client = AsyncMock()
        mock_client.chat.completions.create.side_effect = [
            _make_llm_response(""),
            _make_llm_response('{"ok": true}'),
        ]

        text = await _llm_call(
            client=mock_client,
            model="test-model",
            messages=[{"role": "user", "content": "x"}],
            expect_json=True,
            server_name="context7",
        )

        assert text == '{"ok": true}'
        assert mock_client.chat.completions.create.await_count == 2

    @pytest.mark.asyncio
    async def test_silent_mode_suppresses_error_logs(
        self, caplog: pytest.LogCaptureFixture
    ):
        """silent=True 时底层调用失败不应输出日志。"""
        mock_client = AsyncMock()
        mock_client.chat.completions.create.side_effect = RuntimeError("boom")

        with caplog.at_level(logging.WARNING, logger="excelmanus.mcp.skillpack_generator"):
            result = await _llm_call(
                client=mock_client,
                model="test-model",
                messages=[{"role": "user", "content": "x"}],
                server_name="context7",
                silent=True,
            )

        assert result is None
        assert len(caplog.records) == 0


# ══════════════════════════════════════════════════════════
# 测试 5：编排器
# ══════════════════════════════════════════════════════════


class TestMCPSkillpackGenerator:

    def _make_manager_with_server(
        self, server_name: str = "context7", tools: list | None = None
    ) -> MagicMock:
        """创建模拟的 MCPManager，带一个已连接 server。"""
        if tools is None:
            tools = [
                _make_mcp_tool("resolve-library-id", "解析库 ID"),
                _make_mcp_tool("query-docs", "查询文档"),
            ]
        mock_client = MagicMock()
        mock_client._tools = tools

        mock_manager = MagicMock()
        mock_manager._clients = {server_name: mock_client}
        mock_manager.generate_skillpacks.return_value = [
            _make_skillpack(f"mcp_{server_name}", description="程序化回退")
        ]
        return mock_manager

    def _make_manager_with_servers(
        self, servers: dict[str, list] | None = None
    ) -> MagicMock:
        """创建多 server 的模拟 MCPManager。"""
        if servers is None:
            servers = {
                "context7": [
                    _make_mcp_tool("resolve-library-id", "解析库 ID"),
                    _make_mcp_tool("query-docs", "查询文档"),
                ],
                "git": [
                    _make_mcp_tool("git_status", "查看状态"),
                ],
            }

        mock_manager = MagicMock()
        mock_manager._clients = {}
        basic_skillpacks: list[Skillpack] = []
        for server_name, tools in servers.items():
            mock_client = MagicMock()
            mock_client._tools = tools
            mock_manager._clients[server_name] = mock_client
            basic_skillpacks.append(
                _make_skillpack(f"mcp_{server_name}", description=f"{server_name} 程序化回退")
            )
        mock_manager.generate_skillpacks.return_value = basic_skillpacks
        return mock_manager

    @pytest.mark.asyncio
    async def test_first_run_calls_llm(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """首次运行（无缓存）应调用 LLM 生成。"""
        monkeypatch.setattr(
            "excelmanus.mcp.skillpack_generator._CACHE_FILE",
            tmp_path / "cache.json",
        )
        monkeypatch.setattr(
            "excelmanus.mcp.skillpack_generator._CACHE_DIR", tmp_path
        )

        llm_output = json.dumps({
            "description": "LLM 生成的描述",
            "triggers": ["文档"],
            "instructions": _actionable_instructions("mcp_context7_"),
        })
        mock_client = AsyncMock()
        mock_client.chat.completions.create.return_value = _make_llm_response(llm_output)

        manager = self._make_manager_with_server()
        gen = MCPSkillpackGenerator(manager, mock_client, "test-model")

        result = await gen.generate()

        assert len(result) == 1
        assert result[0].description == "LLM 生成的描述"
        # 应写入缓存
        assert (tmp_path / "cache.json").exists()
        # 两步 LLM 调用（Step1: description+triggers, Step2: instructions）
        assert mock_client.chat.completions.create.await_count == 2

    @pytest.mark.asyncio
    async def test_cache_hit_skips_llm(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """缓存命中且指纹匹配时应跳过 LLM 调用。"""
        monkeypatch.setattr(
            "excelmanus.mcp.skillpack_generator._CACHE_FILE",
            tmp_path / "cache.json",
        )
        monkeypatch.setattr(
            "excelmanus.mcp.skillpack_generator._CACHE_DIR", tmp_path
        )

        tools = [
            _make_mcp_tool("resolve-library-id", "解析库 ID"),
            _make_mcp_tool("query-docs", "查询文档"),
        ]
        fingerprint = compute_fingerprint(tools)

        # 预写缓存
        cache = {
            "version": _CACHE_VERSION,
            "servers": {
                "context7": {
                    "fingerprint": fingerprint,
                    "skillpack": _skillpack_to_cache_dict(
                        _make_skillpack("mcp_context7", description="缓存版本")
                    ),
                }
            },
        }
        save_cache(cache)

        mock_client = AsyncMock()
        manager = self._make_manager_with_server(tools=tools)
        gen = MCPSkillpackGenerator(manager, mock_client, "test-model")

        result = await gen.generate()

        assert len(result) == 1
        assert result[0].description == "缓存版本"
        # 不应调用 LLM
        mock_client.chat.completions.create.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_fingerprint_mismatch_triggers_llm(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """工具指纹变化时应重新调用 LLM。"""
        monkeypatch.setattr(
            "excelmanus.mcp.skillpack_generator._CACHE_FILE",
            tmp_path / "cache.json",
        )
        monkeypatch.setattr(
            "excelmanus.mcp.skillpack_generator._CACHE_DIR", tmp_path
        )

        # 旧缓存（指纹不匹配）
        cache = {
            "version": _CACHE_VERSION,
            "servers": {
                "context7": {
                    "fingerprint": "old_fingerprint",
                    "skillpack": _skillpack_to_cache_dict(
                        _make_skillpack("mcp_context7", description="旧版本")
                    ),
                }
            },
        }
        save_cache(cache)

        llm_output = json.dumps({
            "description": "更新后的描述",
            "triggers": ["新触发词"],
            "instructions": _actionable_instructions("mcp_context7_"),
        })
        mock_client = AsyncMock()
        mock_client.chat.completions.create.return_value = _make_llm_response(llm_output)

        manager = self._make_manager_with_server()
        gen = MCPSkillpackGenerator(manager, mock_client, "test-model")

        result = await gen.generate()

        assert len(result) == 1
        assert result[0].description == "更新后的描述"
        assert mock_client.chat.completions.create.await_count == 2

    @pytest.mark.asyncio
    async def test_llm_failure_falls_back_to_basic(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """LLM 失败时应回退到程序化生成。"""
        monkeypatch.setattr(
            "excelmanus.mcp.skillpack_generator._CACHE_FILE",
            tmp_path / "cache.json",
        )
        monkeypatch.setattr(
            "excelmanus.mcp.skillpack_generator._CACHE_DIR", tmp_path
        )

        mock_client = AsyncMock()
        mock_client.chat.completions.create.side_effect = RuntimeError("LLM down")

        manager = self._make_manager_with_server()
        gen = MCPSkillpackGenerator(manager, mock_client, "test-model")

        result = await gen.generate()

        assert len(result) == 1
        assert result[0].description == "程序化回退"
        assert gen._diagnostics["fallback_used"] >= 1

    @pytest.mark.asyncio
    async def test_first_server_llm_failure_circuit_breaker_skips_remaining_llm(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """首个 server LLM 失败后应熔断，剩余 server 直接程序化回退。"""
        monkeypatch.setattr(
            "excelmanus.mcp.skillpack_generator._CACHE_FILE",
            tmp_path / "cache.json",
        )
        monkeypatch.setattr(
            "excelmanus.mcp.skillpack_generator._CACHE_DIR", tmp_path
        )

        mock_client = AsyncMock()
        mock_client.chat.completions.create.side_effect = RuntimeError("LLM down")

        manager = self._make_manager_with_servers()
        gen = MCPSkillpackGenerator(manager, mock_client, "test-model")

        result = await gen.generate()

        assert len(result) == 2
        by_name = {sp.name: sp for sp in result}
        assert by_name["mcp_context7"].description == "context7 程序化回退"
        assert by_name["mcp_git"].description == "git 程序化回退"
        # 首个 server Step1 失败后即熔断，不应再为第二个 server 调 LLM
        assert mock_client.chat.completions.create.await_count == 1

    @pytest.mark.asyncio
    async def test_content_validation_failure_does_not_trip_global_circuit_breaker(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """内容校验失败只回退当前 server，不应阻断后续 server 的 LLM 尝试。"""
        monkeypatch.setattr(
            "excelmanus.mcp.skillpack_generator._CACHE_FILE",
            tmp_path / "cache.json",
        )
        monkeypatch.setattr(
            "excelmanus.mcp.skillpack_generator._CACHE_DIR", tmp_path
        )

        mock_client = AsyncMock()
        mock_client.chat.completions.create.side_effect = [
            _make_llm_response("not json"),  # context7 Step1 首次失败
            _make_llm_response("still not json"),  # context7 Step1 修复失败
            _make_llm_response(  # git Step1 成功
                json.dumps({
                    "description": "Git 工具集",
                    "triggers": ["git"],
                })
            ),
            _make_llm_response(  # git Step2 成功
                json.dumps({"instructions": _actionable_instructions("mcp_git_")})
            ),
        ]

        manager = self._make_manager_with_servers()
        gen = MCPSkillpackGenerator(manager, mock_client, "test-model")

        result = await gen.generate()

        assert len(result) == 2
        by_name = {sp.name: sp for sp in result}
        assert by_name["mcp_context7"].description == "context7 程序化回退"
        assert by_name["mcp_git"].description == "Git 工具集"
        assert mock_client.chat.completions.create.await_count == 4

    @pytest.mark.asyncio
    async def test_no_connected_servers_returns_empty(self):
        """无已连接 Server 时返回空列表。"""
        mock_manager = MagicMock()
        mock_manager._clients = {}

        mock_client = AsyncMock()
        gen = MCPSkillpackGenerator(mock_manager, mock_client, "test-model")

        result = await gen.generate()
        assert result == []

    @pytest.mark.asyncio
    async def test_background_refresh_creates_task(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """schedule_background_refresh 应创建后台任务。"""
        monkeypatch.setattr(
            "excelmanus.mcp.skillpack_generator._CACHE_FILE",
            tmp_path / "cache.json",
        )
        monkeypatch.setattr(
            "excelmanus.mcp.skillpack_generator._CACHE_DIR", tmp_path
        )

        mock_client = AsyncMock()
        mock_client.chat.completions.create.return_value = _make_llm_response(
            json.dumps({
                "description": "后台生成",
                "triggers": [],
                "instructions": _actionable_instructions("mcp_context7_"),
            })
        )

        manager = self._make_manager_with_server()
        gen = MCPSkillpackGenerator(manager, mock_client, "test-model")

        gen.schedule_background_refresh()
        assert gen._background_task is not None

        # 等待后台任务完成
        await gen._background_task

        # 验证缓存已更新
        cache = load_cache()
        assert "context7" in cache.get("servers", {})


# ══════════════════════════════════════════════════════════
# 测试 6：Token 浪费防护
# ══════════════════════════════════════════════════════════


class TestTokenWasteProtection:
    """验证后台刷新不会重复浪费 LLM token。"""

    def _setup_env(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(
            "excelmanus.mcp.skillpack_generator._CACHE_FILE",
            tmp_path / "cache.json",
        )
        monkeypatch.setattr(
            "excelmanus.mcp.skillpack_generator._CACHE_DIR", tmp_path
        )

    def _make_manager_with_server(
        self, server_name: str = "context7", tools: list | None = None
    ) -> MagicMock:
        if tools is None:
            tools = [
                _make_mcp_tool("resolve-library-id", "解析库 ID"),
                _make_mcp_tool("query-docs", "查询文档"),
            ]
        mock_client = MagicMock()
        mock_client._tools = tools
        mock_manager = MagicMock()
        mock_manager._clients = {server_name: mock_client}
        mock_manager.generate_skillpacks.return_value = [
            _make_skillpack(f"mcp_{server_name}", description="程序化回退")
        ]
        return mock_manager

    @pytest.mark.asyncio
    async def test_background_skips_llm_generated_this_session(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """generate() 已用 LLM 成功生成后，后台刷新不应再次调用 LLM。"""
        self._setup_env(tmp_path, monkeypatch)

        llm_output = json.dumps({
            "description": "LLM 生成",
            "triggers": ["测试"],
            "instructions": _actionable_instructions("mcp_context7_"),
        })
        mock_client = AsyncMock()
        mock_client.chat.completions.create.return_value = _make_llm_response(llm_output)

        manager = self._make_manager_with_server()
        gen = MCPSkillpackGenerator(manager, mock_client, "test-model")

        # generate() 调 LLM（首次无缓存，两步调用）
        await gen.generate()
        count_after_generate = mock_client.chat.completions.create.await_count
        assert count_after_generate == 2  # Step1 + Step2

        # 后台刷新不应再调 LLM
        await gen._background_refresh()
        assert mock_client.chat.completions.create.await_count == count_after_generate

    @pytest.mark.asyncio
    async def test_background_skips_llm_cache_in_cooldown(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """LLM 缓存在冷却期内（6h），后台刷新不应调用 LLM。"""
        self._setup_env(tmp_path, monkeypatch)

        import time as _time
        tools = [
            _make_mcp_tool("resolve-library-id", "解析库 ID"),
            _make_mcp_tool("query-docs", "查询文档"),
        ]
        fingerprint = compute_fingerprint(tools)

        # 预写 LLM 缓存，timestamp 是 1 小时前（在 6h 冷却期内）
        cache = {
            "version": _CACHE_VERSION,
            "servers": {
                "context7": {
                    "fingerprint": fingerprint,
                    "skillpack": _skillpack_to_cache_dict(
                        _make_skillpack("mcp_context7", description="LLM 生成")
                    ),
                    "generated_by": "llm",
                    "timestamp": _time.time() - 3600,  # 1 小时前
                }
            },
        }
        save_cache(cache)

        mock_client = AsyncMock()
        manager = self._make_manager_with_server(tools=tools)
        gen = MCPSkillpackGenerator(manager, mock_client, "test-model")

        # 后台刷新应跳过（冷却期内）
        await gen._background_refresh()
        mock_client.chat.completions.create.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_background_refreshes_basic_fallback(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """程序化回退的缓存条目应被后台刷新用 LLM 更新。"""
        self._setup_env(tmp_path, monkeypatch)

        import time as _time
        tools = [
            _make_mcp_tool("resolve-library-id", "解析库 ID"),
            _make_mcp_tool("query-docs", "查询文档"),
        ]
        fingerprint = compute_fingerprint(tools)

        # 预写 basic 回退缓存
        cache = {
            "version": _CACHE_VERSION,
            "servers": {
                "context7": {
                    "fingerprint": fingerprint,
                    "skillpack": _skillpack_to_cache_dict(
                        _make_skillpack("mcp_context7", description="程序化回退")
                    ),
                    "generated_by": "basic",
                    "timestamp": _time.time() - 3600,
                }
            },
        }
        save_cache(cache)

        llm_output = json.dumps({
            "description": "LLM 升级版",
            "triggers": ["升级"],
            "instructions": _actionable_instructions("mcp_context7_"),
        })
        mock_client = AsyncMock()
        mock_client.chat.completions.create.return_value = _make_llm_response(llm_output)

        manager = self._make_manager_with_server(tools=tools)
        gen = MCPSkillpackGenerator(manager, mock_client, "test-model")

        # 后台刷新应调用 LLM（basic 条目需要升级，两步调用）
        await gen._background_refresh()
        assert mock_client.chat.completions.create.await_count == 2

        # 验证缓存已更新为 LLM 版本
        updated_cache = load_cache()
        entry = updated_cache["servers"]["context7"]
        assert entry["generated_by"] == "llm"

    @pytest.mark.asyncio
    async def test_background_refreshes_after_cooldown_expired(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """冷却期过期后，后台刷新应正常调用 LLM。"""
        self._setup_env(tmp_path, monkeypatch)

        import time as _time
        tools = [
            _make_mcp_tool("resolve-library-id", "解析库 ID"),
            _make_mcp_tool("query-docs", "查询文档"),
        ]
        fingerprint = compute_fingerprint(tools)

        # 预写 LLM 缓存，但 timestamp 是 7 小时前（超过 6h 冷却期）
        cache = {
            "version": _CACHE_VERSION,
            "servers": {
                "context7": {
                    "fingerprint": fingerprint,
                    "skillpack": _skillpack_to_cache_dict(
                        _make_skillpack("mcp_context7", description="旧 LLM")
                    ),
                    "generated_by": "llm",
                    "timestamp": _time.time() - 7 * 3600,  # 7 小时前
                }
            },
        }
        save_cache(cache)

        llm_output = json.dumps({
            "description": "刷新后的描述",
            "triggers": [],
            "instructions": _actionable_instructions("mcp_context7_"),
        })
        mock_client = AsyncMock()
        mock_client.chat.completions.create.return_value = _make_llm_response(llm_output)

        manager = self._make_manager_with_server(tools=tools)
        gen = MCPSkillpackGenerator(manager, mock_client, "test-model")

        # 冷却过期，应调用 LLM（两步调用）
        await gen._background_refresh()
        assert mock_client.chat.completions.create.await_count == 2

    @pytest.mark.asyncio
    async def test_background_silent_retry_max_two_attempts_per_session(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """后台静默重试每会话最多 2 轮，超出后等待下次启动。"""
        self._setup_env(tmp_path, monkeypatch)

        mock_client = AsyncMock()
        mock_client.chat.completions.create.side_effect = RuntimeError("LLM down")

        manager = self._make_manager_with_server()
        gen = MCPSkillpackGenerator(manager, mock_client, "test-model")

        await gen._background_refresh()
        assert mock_client.chat.completions.create.await_count == 2
        assert gen._silent_refresh_attempts_this_session == 2

        # 会话内达到上限后，再次刷新不应继续调用 LLM
        await gen._background_refresh()
        assert mock_client.chat.completions.create.await_count == 2

    @pytest.mark.asyncio
    async def test_background_failure_is_silent(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ):
        """后台刷新失败时不输出任何日志。"""
        self._setup_env(tmp_path, monkeypatch)

        mock_client = AsyncMock()
        mock_client.chat.completions.create.side_effect = RuntimeError("LLM down")

        manager = self._make_manager_with_server()
        gen = MCPSkillpackGenerator(manager, mock_client, "test-model")

        with caplog.at_level(logging.INFO, logger="excelmanus.mcp.skillpack_generator"):
            await gen._background_refresh()

        assert len(caplog.records) == 0

    @pytest.mark.asyncio
    async def test_background_exception_from_generator_still_silent_and_limited(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ):
        """内部生成抛异常时仍应静默，并计入每会话最多 2 次尝试。"""
        self._setup_env(tmp_path, monkeypatch)

        manager = self._make_manager_with_server()
        gen = MCPSkillpackGenerator(manager, AsyncMock(), "test-model")

        async def _raise_unexpected(**kwargs):
            raise RuntimeError("unexpected")

        monkeypatch.setattr(
            "excelmanus.mcp.skillpack_generator.generate_skillpack_with_llm",
            _raise_unexpected,
        )

        with caplog.at_level(logging.INFO, logger="excelmanus.mcp.skillpack_generator"):
            await gen._background_refresh()

        assert gen._silent_refresh_attempts_this_session == 2
        assert len(caplog.records) == 0

    @pytest.mark.asyncio
    async def test_background_logs_when_success(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ):
        """后台刷新成功更新时应输出成功日志。"""
        self._setup_env(tmp_path, monkeypatch)

        import time as _time
        tools = [
            _make_mcp_tool("resolve-library-id", "解析库 ID"),
            _make_mcp_tool("query-docs", "查询文档"),
        ]
        fingerprint = compute_fingerprint(tools)

        cache = {
            "version": _CACHE_VERSION,
            "servers": {
                "context7": {
                    "fingerprint": fingerprint,
                    "skillpack": _skillpack_to_cache_dict(
                        _make_skillpack("mcp_context7", description="程序化回退")
                    ),
                    "generated_by": "basic",
                    "timestamp": _time.time() - 3600,
                }
            },
        }
        save_cache(cache)

        llm_output = json.dumps({
            "description": "LLM 升级版",
            "triggers": ["升级"],
            "instructions": _actionable_instructions("mcp_context7_"),
        })
        mock_client = AsyncMock()
        mock_client.chat.completions.create.return_value = _make_llm_response(llm_output)

        manager = self._make_manager_with_server(tools=tools)
        gen = MCPSkillpackGenerator(manager, mock_client, "test-model")

        with caplog.at_level(logging.INFO, logger="excelmanus.mcp.skillpack_generator"):
            await gen._background_refresh()

        assert any("后台刷新成功" in r.message for r in caplog.records)
        assert not any(r.levelno >= logging.WARNING for r in caplog.records)

    @pytest.mark.asyncio
    async def test_generate_writes_generated_by_and_timestamp(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """generate() 写入缓存时应包含 generated_by 和 timestamp 字段。"""
        self._setup_env(tmp_path, monkeypatch)

        llm_output = json.dumps({
            "description": "测试",
            "triggers": [],
            "instructions": _actionable_instructions("mcp_context7_"),
        })
        mock_client = AsyncMock()
        mock_client.chat.completions.create.return_value = _make_llm_response(llm_output)

        manager = self._make_manager_with_server()
        gen = MCPSkillpackGenerator(manager, mock_client, "test-model")

        await gen.generate()

        cache = load_cache()
        entry = cache["servers"]["context7"]
        assert entry["generated_by"] == "llm"
        assert isinstance(entry["timestamp"], float)
        assert entry["timestamp"] > 0

    @pytest.mark.asyncio
    async def test_llm_failure_writes_basic_generated_by(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """LLM 失败回退时，缓存的 generated_by 应为 'basic'。"""
        self._setup_env(tmp_path, monkeypatch)

        mock_client = AsyncMock()
        mock_client.chat.completions.create.side_effect = RuntimeError("fail")

        manager = self._make_manager_with_server()
        gen = MCPSkillpackGenerator(manager, mock_client, "test-model")

        await gen.generate()

        cache = load_cache()
        entry = cache["servers"]["context7"]
        assert entry["generated_by"] == "basic"
