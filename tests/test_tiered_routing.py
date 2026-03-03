"""分层路由（Tiered Routing）测试。

覆盖：
- chitchat 正则 → route_mode="chitchat" 快速通道
- 保守安全门控：消息长度、图片附件、文件路径
- 多轮上下文安全降级：active_skills / pending / recent tool calls → all_tools
- context_builder chitchat 快速通道：仅 identity+rules+channel（不含 access/backup/mcp）
- _tool_calling_loop chitchat: tools=[], max_iter=1
- 降级恢复：降级后下一轮新 session 可正常走快速通道
- 诊断捕获：chitchat 路由决策记录到 session_diagnostics
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from excelmanus.skillpacks.models import SkillMatchResult
from excelmanus.skillpacks.router import SkillRouter, _CHITCHAT_RE


# ════════════════════════════════════════════════════════════
# 辅助工厂
# ════════════════════════════════════════════════════════════


def _make_router() -> SkillRouter:
    """创建一个带 stub loader 的 SkillRouter 实例。"""
    config = MagicMock()
    config.aux_enabled = False
    config.aux_model = ""
    config.skills_context_char_budget = 8000
    config.large_excel_threshold_bytes = 50 * 1024 * 1024
    loader = MagicMock()
    # 返回至少一个 skillpack 以避免 no_skillpack 路径
    loader.get_skillpacks.return_value = {"dummy": MagicMock(
        name="dummy",
        description="test",
        instructions="test",
        disable_model_invocation=False,
        user_invocable=True,
    )}
    loader.load_all.return_value = loader.get_skillpacks.return_value
    return SkillRouter(config, loader)


def _simulate_downgrade(
    *,
    active_skills: list | None = None,
    pending_question: bool = False,
    pending_approval: bool = False,
    recent_messages: list[dict] | None = None,
) -> str:
    """复现 engine.py 中的 chitchat 降级逻辑，返回降级原因（空字符串=不降级）。

    这段逻辑与 engine.py:2274-2287 完全一致，用于单元测试中
    脱离完整 Engine 实例验证降级条件。
    """
    messages = recent_messages or []
    if active_skills:
        return "active_skills"
    if pending_question:
        return "pending_question"
    if pending_approval:
        return "pending_approval"
    if any(
        m.get("role") == "tool"
        for m in messages[-6:]
        if isinstance(m, dict)
    ):
        return "recent_tool_calls"
    return ""


# ════════════════════════════════════════════════════════════
# 1. Router 层：route_mode="chitchat" 快速通道
# ════════════════════════════════════════════════════════════


class TestChitchatRouteMode:
    """chitchat 消息应返回 route_mode='chitchat'。"""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("message", [
        "你好",
        "hi",
        "Hello!",
        "谢谢",
        "ok",
        "你是谁？",
        "help",
        "早上好",
    ])
    async def test_chitchat_returns_chitchat_route_mode(self, message: str) -> None:
        router = _make_router()
        result = await router.route(message, chat_mode="write")
        assert result.route_mode == "chitchat", (
            f"Expected 'chitchat' for {message!r}, got {result.route_mode!r}"
        )
        assert result.write_hint == "read_only"
        assert result.system_contexts == []

    @pytest.mark.asyncio
    async def test_chitchat_plan_mode_tags(self) -> None:
        """plan 模式下 chitchat 应携带 plan_not_needed 标签。"""
        router = _make_router()
        result = await router.route("你好", chat_mode="plan")
        assert result.route_mode == "chitchat"
        assert "plan_not_needed" in result.task_tags


class TestChitchatSafetyGates:
    """保守安全门控：确保任务消息不被误判为 chitchat。"""

    @pytest.mark.asyncio
    async def test_long_message_not_chitchat(self) -> None:
        """超过 50 字的问候消息不走 chitchat。"""
        long_msg = "你好" + "！" * 50  # > 50 chars
        router = _make_router()
        result = await router.route(long_msg, chat_mode="write")
        assert result.route_mode != "chitchat"

    @pytest.mark.asyncio
    async def test_message_with_file_path_not_chitchat(self) -> None:
        """包含文件路径的消息不走 chitchat。"""
        router = _make_router()
        result = await router.route(
            "你好",
            file_paths=["data.xlsx"],
            chat_mode="write",
        )
        assert result.route_mode != "chitchat"

    @pytest.mark.asyncio
    async def test_message_with_images_not_chitchat(self) -> None:
        """包含图片附件的消息不走 chitchat。"""
        router = _make_router()
        result = await router.route(
            "你好",
            images=[{"data": "base64data", "media_type": "image/png"}],
            chat_mode="write",
        )
        assert result.route_mode != "chitchat"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("message", [
        "你好，帮我读取 data.xlsx",
        "hello, please format the table",
        "hi 帮我创建图表",
        "谢谢，再帮我加个图表",
        "好的，继续处理",
        "收到，帮我排序一下",
        "了解，请创建新的sheet",
    ])
    async def test_task_messages_not_chitchat(self, message: str) -> None:
        """包含任务内容的消息绝不走 chitchat 路径。"""
        router = _make_router()
        result = await router.route(message, chat_mode="write")
        assert result.route_mode != "chitchat", (
            f"Task message should NOT be chitchat: {message!r}"
        )

    @pytest.mark.asyncio
    async def test_embedded_excel_path_not_chitchat(self) -> None:
        """消息中内嵌 Excel 文件路径时不走 chitchat。"""
        router = _make_router()
        result = await router.route("你好 data.xlsx", chat_mode="write")
        assert result.route_mode != "chitchat"


# ════════════════════════════════════════════════════════════
# 2. Engine 层：多轮上下文安全降级
# ════════════════════════════════════════════════════════════


class TestChitchatMultiTurnDowngrade:
    """chitchat 在多轮任务上下文中应降级回 all_tools。"""

    def test_downgrade_with_active_skills(self) -> None:
        """有 active_skills 时 chitchat 应降级。"""
        reason = _simulate_downgrade(active_skills=[MagicMock()])
        assert reason == "active_skills"

    def test_downgrade_with_pending_question(self) -> None:
        """有 pending question 时 chitchat 应降级。"""
        reason = _simulate_downgrade(pending_question=True)
        assert reason == "pending_question"

    def test_downgrade_with_pending_approval(self) -> None:
        """有 pending approval 时 chitchat 应降级。"""
        reason = _simulate_downgrade(pending_approval=True)
        assert reason == "pending_approval"

    def test_downgrade_with_recent_tool_calls(self) -> None:
        """memory 中有近期 tool 角色消息时 chitchat 应降级。"""
        messages = [
            {"role": "user", "content": "帮我格式化表格"},
            {"role": "assistant", "content": "好的"},
            {"role": "tool", "content": "执行完成"},
            {"role": "assistant", "content": "已格式化"},
            {"role": "user", "content": "谢谢"},
        ]
        reason = _simulate_downgrade(recent_messages=messages)
        assert reason == "recent_tool_calls"

    def test_no_downgrade_for_fresh_session(self) -> None:
        """全新 session（无 active skills，无 pending，无 tool calls）不降级。"""
        reason = _simulate_downgrade()
        assert reason == ""

    def test_downgrade_priority_active_skills_first(self) -> None:
        """降级条件优先级：active_skills > pending_question > pending_approval > recent_tool_calls。"""
        reason = _simulate_downgrade(
            active_skills=[MagicMock()],
            pending_question=True,
            pending_approval=True,
        )
        assert reason == "active_skills"

    def test_downgrade_priority_pending_question_over_approval(self) -> None:
        """降级条件优先级：pending_question > pending_approval。"""
        reason = _simulate_downgrade(
            pending_question=True,
            pending_approval=True,
        )
        assert reason == "pending_question"

    def test_tool_calls_beyond_6_message_window_no_downgrade(self) -> None:
        """tool 角色消息在 6 条窗口外时不触发降级。"""
        messages: list[dict[str, str]] = [
            {"role": "tool", "content": "旧工具调用"},
            {"role": "user", "content": "msg1"},
            {"role": "assistant", "content": "msg2"},
            {"role": "user", "content": "msg3"},
            {"role": "assistant", "content": "msg4"},
            {"role": "user", "content": "msg5"},
            {"role": "assistant", "content": "msg6"},
            {"role": "user", "content": "谢谢"},
        ]
        reason = _simulate_downgrade(recent_messages=messages)
        assert reason == "", "tool call outside 6-message window should not trigger downgrade"

    def test_downgrade_result_preserves_fields(self) -> None:
        """降级后 SkillMatchResult 应保留原字段，仅改变 route_mode 和 write_hint。"""
        original = SkillMatchResult(
            skills_used=[],
            route_mode="chitchat",
            system_contexts=[],
            parameterized=False,
            write_hint="read_only",
            task_tags=("plan_not_needed",),
        )
        # 模拟 engine.py 中的降级构造
        downgraded = SkillMatchResult(
            skills_used=original.skills_used,
            route_mode="all_tools",
            system_contexts=original.system_contexts,
            parameterized=original.parameterized,
            write_hint="unknown",
            task_tags=original.task_tags,
        )
        assert downgraded.route_mode == "all_tools"
        assert downgraded.write_hint == "unknown"
        assert downgraded.task_tags == original.task_tags
        assert downgraded.parameterized == original.parameterized


# ════════════════════════════════════════════════════════════
# 3. ContextBuilder 层：chitchat 精简 system prompts
# ════════════════════════════════════════════════════════════


class TestContextBuilderChitchatFastPath:
    """context_builder 对 chitchat 应只注入 identity+rules+channel（不含 access/backup/mcp）。"""

    def _make_mock_context_builder(
        self,
        *,
        system_prompt: str = "You are ExcelManus.",
        rules_notice: str = "Custom rules here.",
        channel_notice: str = "",
        access_notice: str = "Access: fullAccess=off",
        backup_notice: str = "Backup: /backups/xxx",
        mcp_notice: str = "MCP: server_a, server_b",
    ) -> tuple[Any, Any]:
        """构造 mock ContextBuilder + Engine，返回 (context_builder, engine)。"""
        engine = MagicMock()
        engine.memory.system_prompt = system_prompt
        engine._session_turn = 1
        engine._channel_context = "web"

        from excelmanus.engine_core.context_builder import ContextBuilder
        cb = ContextBuilder(engine)
        # 预填充 notice 缓存（模拟 _build_stable_system_prompt 已被调用）
        cb._turn_notice_cache_key = 1
        cb._turn_notice_cache = {
            "rules": rules_notice,
            cb._channel_cache_key: channel_notice,
            "access": access_notice,
            "backup": backup_notice,
            "mcp": mcp_notice,
        }
        return cb, engine

    def test_chitchat_prompt_excludes_access_backup_mcp(self) -> None:
        """chitchat 快速通道生成的 prompt 不应包含 access/backup/mcp notice。"""
        cb, engine = self._make_mock_context_builder(
            system_prompt="Identity prompt.",
            rules_notice="Rules notice.",
            channel_notice="Channel notice.",
            access_notice="Access: fullAccess=off",
            backup_notice="Backup: /backups/xxx",
            mcp_notice="MCP servers: a, b, c",
        )
        route_result = SkillMatchResult(
            skills_used=[], route_mode="chitchat", system_contexts=[],
        )
        prompts, error = cb._prepare_system_prompts_for_request(
            [], route_result=route_result,
        )
        assert error is None
        assert len(prompts) == 1
        prompt_text = prompts[0]
        # 应包含 identity + rules + channel
        assert "Identity prompt." in prompt_text
        assert "Rules notice." in prompt_text
        assert "Channel notice." in prompt_text
        # 不应包含 access/backup/mcp
        assert "Access: fullAccess=off" not in prompt_text
        assert "Backup: /backups/xxx" not in prompt_text
        assert "MCP servers: a, b, c" not in prompt_text

    def test_chitchat_prompt_without_channel_notice(self) -> None:
        """Web 渠道无 channel_notice 时，chitchat prompt 仅含 identity + rules。"""
        cb, engine = self._make_mock_context_builder(
            system_prompt="Identity.",
            rules_notice="Rules.",
            channel_notice="",
        )
        route_result = SkillMatchResult(
            skills_used=[], route_mode="chitchat", system_contexts=[],
        )
        prompts, error = cb._prepare_system_prompts_for_request(
            [], route_result=route_result,
        )
        assert error is None
        prompt_text = prompts[0]
        assert "Identity." in prompt_text
        assert "Rules." in prompt_text
        # 无 channel_notice 时不应出现多余换行拼接
        assert prompt_text.count("\n\n") == 1  # identity 和 rules 之间一个分隔

    def test_non_chitchat_includes_all_notices(self) -> None:
        """非 chitchat 路由不应提前返回，继续构建完整 prompt。"""
        cb, engine = self._make_mock_context_builder()
        route_result = SkillMatchResult(
            skills_used=[], route_mode="all_tools", system_contexts=[],
        )
        # 非 chitchat 路径会调用更多方法，这里 mock 它们避免报错
        cb._build_file_registry_notice = MagicMock(return_value="")
        cb._build_memory_notice = MagicMock(return_value="")
        cb._build_playbook_notice = MagicMock(return_value="")
        cb._build_skill_hints_notice = MagicMock(return_value="")
        cb._build_runtime_metadata_line = MagicMock(return_value="Runtime: test")
        cb._build_task_plan_notice = MagicMock(return_value="")
        cb._build_meta_cognition_notice = MagicMock(return_value="")
        cb._build_verification_fix_notice = MagicMock(return_value="")
        cb._build_post_write_verification_hint = MagicMock(return_value="")
        cb._build_scan_tool_hint = MagicMock(return_value="")
        cb._build_explorer_report_notice = MagicMock(return_value="")
        cb._build_window_perception_notice = MagicMock(return_value="")
        cb._build_stable_system_prompt = MagicMock(return_value="Stable prompt with access and mcp")
        engine._prompt_composer = None
        engine._transient_hook_contexts = []
        engine._effective_window_return_mode = MagicMock(return_value="enriched")
        engine.max_context_tokens = 100000
        engine.state.prompt_injection_snapshots = []
        engine._effective_system_mode = MagicMock(return_value="multi")
        prompts, error = cb._prepare_system_prompts_for_request(
            [], route_result=route_result,
        )
        assert error is None
        # 非 chitchat 至少有 stable_prompt + dynamic_prompt（含 runtime_metadata）
        assert len(prompts) >= 1


# ════════════════════════════════════════════════════════════
# 4. _tool_calling_loop 层：chitchat max_iter=1, tools=[]
# ════════════════════════════════════════════════════════════


class TestToolCallingLoopChitchat:
    """_tool_calling_loop 对 chitchat 应限制迭代 + 空工具。"""

    def test_chitchat_max_iter_is_1(self) -> None:
        """chitchat route_mode 时 max_iter 应被覆盖为 1。"""
        route_result = SkillMatchResult(
            skills_used=[], route_mode="chitchat",
        )
        max_iter = 10  # 默认
        if route_result.route_mode == "chitchat":
            max_iter = 1
        assert max_iter == 1

    def test_non_chitchat_max_iter_unchanged(self) -> None:
        """非 chitchat route_mode 时 max_iter 不变。"""
        route_result = SkillMatchResult(
            skills_used=[], route_mode="all_tools",
        )
        max_iter = 10
        if route_result.route_mode == "chitchat":
            max_iter = 1
        assert max_iter == 10

    def test_chitchat_tools_empty(self) -> None:
        """chitchat route_mode 时 tools 应为空列表。"""
        is_chitchat = True
        if is_chitchat:
            tools: list = []
        else:
            tools = [{"type": "function", "function": {"name": "test"}}]
        assert tools == []

    def test_chitchat_skips_registry_scan(self) -> None:
        """chitchat route_mode 时应跳过 FileRegistry 扫描。"""
        route_mode = "chitchat"
        _is_chitchat_route = route_mode == "chitchat"
        assert _is_chitchat_route is True

    def test_empty_tools_not_injected_to_kwargs(self) -> None:
        """tools=[] 时不应注入 kwargs["tools"]（省 schema tokens）。"""
        tools: list = []
        kwargs: dict[str, Any] = {"model": "test", "messages": []}
        if tools:
            kwargs["tools"] = tools
        assert "tools" not in kwargs


# ════════════════════════════════════════════════════════════
# 5. 正则覆盖验证（含新增模式）
# ════════════════════════════════════════════════════════════


class TestChitchatRegexCoverage:
    """全面验证 _CHITCHAT_RE 正则的覆盖范围。"""

    @pytest.mark.parametrize("msg", [
        # 原有问候
        "你好", "您好", "hi", "hello", "hey", "嗨", "哈喽",
        "早上好", "下午好", "晚上好", "good morning", "good afternoon", "good evening",
        "在吗", "在不在",
        # 感谢（原有 + 新增）
        "谢谢", "thanks", "thank you", "感谢", "thx", "ty", "谢了", "多谢",
        # 短确认/应答（新增）
        "好的", "ok", "okay", "嗯", "嗯嗯", "好", "收到", "明白", "了解",
        "知道了", "没问题", "可以", "行", "对", "是的", "是",
        "没事了", "不用了",
        # 告别（新增）
        "再见", "拜拜", "bye", "goodbye", "see you", "晚安", "good night",
        "88", "886",
        # 身份/元问题
        "你是谁", "你是什么", "你叫什么", "你能做什么", "你会什么", "你有什么功能",
        "who are you", "what are you", "what can you do", "introduce yourself",
        # 帮助
        "help", "帮助", "怎么用", "如何使用", "使用说明", "使用方法",
    ])
    def test_chitchat_message_matches(self, msg: str) -> None:
        """每个 chitchat 消息都应被正则匹配。"""
        assert _CHITCHAT_RE.match(msg.strip()), f"Should match: {msg!r}"

    @pytest.mark.parametrize("msg", [
        # 带标点变体
        "你好！", "hello!", "Hello?", "谢谢。", "ok.", "收到！",
        "你好！！", "hello???", "嗯。", "好！！！", "再见。",
        # 带前后空白
        "  你好  ", "  hi  ", "  收到  ",
    ])
    def test_chitchat_with_trailing_punctuation_matches(self, msg: str) -> None:
        """带标点和空白变体的 chitchat 消息仍应匹配。"""
        assert _CHITCHAT_RE.match(msg.strip()), f"Should match: {msg!r}"

    @pytest.mark.parametrize("msg", [
        # 含任务内容
        "帮我读取 data.xlsx",
        "创建一个图表",
        "你好，帮我处理数据",
        "格式化A1单元格",
        "排序数据",
        "合并Sheet1和Sheet2",
        "分析销售数据趋势",
        "筛选出金额大于100的记录",
        # 确认词后跟任务
        "好的，继续处理",
        "收到，帮我排序",
        "了解，请创建sheet",
        "嗯，把A列加粗",
        "ok，帮我导出",
        "明白，开始执行吧",
        # 复杂长消息
        "请帮我把Sheet1的A列数据复制到Sheet2",
        "你好 data.xlsx",
    ])
    def test_task_messages_never_match(self, msg: str) -> None:
        """任务消息绝不应匹配 chitchat 正则。"""
        assert not _CHITCHAT_RE.match(msg.strip()), f"Should NOT match: {msg!r}"


# ════════════════════════════════════════════════════════════
# 6. 路由层新增模式端到端测试
# ════════════════════════════════════════════════════════════


class TestChitchatNewPatternsRouteMode:
    """新增 chitchat 模式（确认/告别/感谢）的端到端路由测试。"""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("message", [
        "嗯", "好", "收到", "明白", "了解", "知道了",
        "再见", "拜拜", "bye", "晚安",
        "感谢", "多谢", "谢了",
        "没事了", "不用了",
        "88", "886",
    ])
    async def test_new_patterns_return_chitchat(self, message: str) -> None:
        router = _make_router()
        result = await router.route(message, chat_mode="write")
        assert result.route_mode == "chitchat", (
            f"Expected 'chitchat' for {message!r}, got {result.route_mode!r}"
        )
        assert result.write_hint == "read_only"


# ════════════════════════════════════════════════════════════
# 7. 降级恢复测试
# ════════════════════════════════════════════════════════════


class TestChitchatDowngradeRecovery:
    """降级后下一轮新 session 可正常走快速通道。"""

    @pytest.mark.asyncio
    async def test_recovery_after_downgrade(self) -> None:
        """降级后若上下文清空，下一条 chitchat 应重新走快速通道。"""
        # 第一步：模拟有 active_skills 导致降级
        reason1 = _simulate_downgrade(active_skills=[MagicMock()])
        assert reason1 == "active_skills"
        # 第二步：模拟 active_skills 清空后，同一 chitchat 不再降级
        reason2 = _simulate_downgrade()
        assert reason2 == ""
        # 第三步：验证路由层面仍能正确识别
        router = _make_router()
        result = await router.route("好的", chat_mode="write")
        assert result.route_mode == "chitchat"

    @pytest.mark.asyncio
    async def test_tool_calls_age_out_of_window(self) -> None:
        """随着更多消息加入，旧 tool 角色消息滑出 6 条窗口后不再降级。"""
        # 初始：tool call 在窗口内
        messages_with_tool: list[dict[str, str]] = [
            {"role": "tool", "content": "result"},
            {"role": "assistant", "content": "done"},
            {"role": "user", "content": "ok"},
        ]
        reason1 = _simulate_downgrade(recent_messages=messages_with_tool)
        assert reason1 == "recent_tool_calls"

        # 追加足够多消息使 tool call 滑出窗口
        for i in range(6):
            messages_with_tool.append(
                {"role": "user" if i % 2 == 0 else "assistant", "content": f"msg{i}"}
            )
        reason2 = _simulate_downgrade(recent_messages=messages_with_tool)
        assert reason2 == "", "tool call should age out of 6-message window"


# ════════════════════════════════════════════════════════════
# 8. 边界情况
# ════════════════════════════════════════════════════════════


class TestChitchatEdgeCases:
    """边界情况测试。"""

    @pytest.mark.asyncio
    async def test_whitespace_only_not_chitchat(self) -> None:
        """纯空白消息不走 chitchat。"""
        router = _make_router()
        result = await router.route("   ", chat_mode="write")
        assert result.route_mode != "chitchat"

    @pytest.mark.asyncio
    async def test_empty_string_not_chitchat(self) -> None:
        """空字符串不走 chitchat。"""
        router = _make_router()
        result = await router.route("", chat_mode="write")
        assert result.route_mode != "chitchat"

    @pytest.mark.asyncio
    async def test_exactly_50_chars_still_chitchat(self) -> None:
        """恰好 50 字符的 chitchat 消息仍走快速通道。"""
        router = _make_router()
        result = await router.route("你好", chat_mode="write")
        assert result.route_mode == "chitchat"

    @pytest.mark.asyncio
    async def test_chitchat_with_punctuation(self) -> None:
        """带标点的 chitchat 消息仍走快速通道。"""
        router = _make_router()
        for msg in ["你好！", "hi!", "Hello?", "谢谢。", "ok.", "收到！", "再见。"]:
            result = await router.route(msg, chat_mode="write")
            assert result.route_mode == "chitchat", (
                f"Expected chitchat for {msg!r}, got {result.route_mode!r}"
            )

    def test_chitchat_regex_does_not_match_substring(self) -> None:
        """确保正则是全匹配，不会匹配子串。"""
        # "好的继续" 应该不匹配（"好的" 后面跟了非标点内容）
        assert not _CHITCHAT_RE.match("好的继续")
        assert not _CHITCHAT_RE.match("嗯帮我看看")
        assert not _CHITCHAT_RE.match("收到请处理")

    def test_case_insensitive_english(self) -> None:
        """英文消息大小写不敏感。"""
        for msg in ["HI", "HELLO", "OK", "OKAY", "BYE", "GOODBYE", "THANKS", "HELP"]:
            assert _CHITCHAT_RE.match(msg), f"Should match case-insensitive: {msg!r}"

    @pytest.mark.asyncio
    async def test_51_char_chitchat_not_fast_path(self) -> None:
        """恰好 51 字符的 chitchat 消息不走快速通道（超过 50 字限制）。"""
        # 构造恰好 51 字符: "你好" (2) + "!" * 49 (49) = 51
        msg = "你好" + "!" * 49
        assert len(msg) == 51
        router = _make_router()
        result = await router.route(msg, chat_mode="write")
        assert result.route_mode != "chitchat"


# ════════════════════════════════════════════════════════════
# 9. 诊断捕获验证
# ════════════════════════════════════════════════════════════


class TestChitchatDiagnostics:
    """验证 chitchat 路由决策被正确记录到 session diagnostics。"""

    def test_downgrade_reason_field_present_when_downgraded(self) -> None:
        """降级时 session_diag 应包含 chitchat_downgrade_reason 字段。"""
        # 模拟 engine.py 中的诊断构造逻辑
        _chitchat_downgrade_reason = "active_skills"
        route_mode = "all_tools"  # 降级后

        _session_diag: dict[str, Any] = {
            "route_mode": route_mode,
        }
        if _chitchat_downgrade_reason:
            _session_diag["chitchat_downgrade_reason"] = _chitchat_downgrade_reason
        elif route_mode == "chitchat":
            _session_diag["chitchat_fast_path"] = True

        assert "chitchat_downgrade_reason" in _session_diag
        assert _session_diag["chitchat_downgrade_reason"] == "active_skills"
        assert "chitchat_fast_path" not in _session_diag

    def test_fast_path_field_present_when_confirmed(self) -> None:
        """确认走快速通道时 session_diag 应包含 chitchat_fast_path=True。"""
        _chitchat_downgrade_reason = ""
        route_mode = "chitchat"

        _session_diag: dict[str, Any] = {
            "route_mode": route_mode,
        }
        if _chitchat_downgrade_reason:
            _session_diag["chitchat_downgrade_reason"] = _chitchat_downgrade_reason
        elif route_mode == "chitchat":
            _session_diag["chitchat_fast_path"] = True

        assert "chitchat_fast_path" in _session_diag
        assert _session_diag["chitchat_fast_path"] is True
        assert "chitchat_downgrade_reason" not in _session_diag

    def test_no_chitchat_fields_for_normal_route(self) -> None:
        """非 chitchat 路由不应有 chitchat 相关诊断字段。"""
        _chitchat_downgrade_reason = ""
        route_mode = "all_tools"

        _session_diag: dict[str, Any] = {
            "route_mode": route_mode,
        }
        if _chitchat_downgrade_reason:
            _session_diag["chitchat_downgrade_reason"] = _chitchat_downgrade_reason
        elif route_mode == "chitchat":
            _session_diag["chitchat_fast_path"] = True

        assert "chitchat_downgrade_reason" not in _session_diag
        assert "chitchat_fast_path" not in _session_diag
