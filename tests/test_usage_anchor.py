"""Phase 5/6: provider usage 锚点计量 + 注入物遮蔽确定性重注。"""

from __future__ import annotations

from excelmanus.config import ExcelManusConfig
from excelmanus.memory import ConversationMemory
from excelmanus.session_log import SessionEventLog


def _cfg() -> ExcelManusConfig:
    return ExcelManusConfig(
        api_key="t", base_url="https://x.example/v1", model="m",
        max_context_tokens=100_000,
    )


class TestUsageAnchor:
    def test_anchor_hit_counts_delta_only(self) -> None:
        mem = ConversationMemory(_cfg())
        mem.add_user_message("q1")
        mem.add_assistant_message("a1")
        # 投影 → 记录发送快照
        mem.project_for_request(["sys"])
        mem.note_provider_prompt_tokens(50_000)

        # 锚点后追加的消息用启发式 delta
        mem.add_tool_result("c1", "x" * 400)
        total = mem._total_tokens_with_system_messages(None)
        delta = mem._count_message(mem.messages[-1])
        assert total == 50_000 + delta

    def test_anchor_invalidated_by_prefix_rewrite(self) -> None:
        mem = ConversationMemory(_cfg())
        mem.add_user_message("q1")
        mem.project_for_request(["sys"])
        mem.note_provider_prompt_tokens(50_000)
        # 压缩遮蔽前缀 → generation 变化 → 锚点失效
        mem.apply_compaction_summary(
            [{"role": "assistant", "content": "摘要"}], 1,
        )
        total = mem._total_tokens_with_system_messages(None)
        assert total != 50_000  # 回落启发式

    def test_anchor_invalidated_by_inplace_edit(self) -> None:
        mem = ConversationMemory(_cfg())
        mem.add_tool_result("c1", "short")
        mem.project_for_request(["sys"])
        mem.note_provider_prompt_tokens(50_000)
        # 原地改写已发送前缀（无日志时直接改 content）
        mem.messages[0]["content"] = "x" * 99999
        total = mem._total_tokens_with_system_messages(None)
        assert total != 50_000  # 指纹不匹配 → 锚点失效 → 启发式计入
        assert mem._usage_anchor is None

    def test_no_pending_no_anchor(self) -> None:
        mem = ConversationMemory(_cfg())
        mem.note_provider_prompt_tokens(50_000)  # 无投影快照 → 不绑定
        assert mem._usage_anchor is None


class TestCatalogShadowReinjection:
    def test_shadowed_catalog_reinjected(self) -> None:
        """catalog 注入被压缩遮蔽后，attach_skill_catalog 确定性重发。"""
        mem = ConversationMemory(_cfg())
        log = SessionEventLog("s1")
        mem.attach_event_log(log)

        mem.add_user_message("目录文本A", hidden=True, prompt_kind="skill_catalog")
        catalog_seq = mem.messages[-1]["_seq"]
        for i in range(3):
            mem.add_user_message(f"用户消息 {i}")
            mem.add_assistant_message(f"回复 {i}")

        assert mem.surface_contains_seq(catalog_seq)

        # 压缩遮蔽 catalog（seq 1）——synthetic 占据 seq 号段
        mem.apply_compaction_summary(
            [{"role": "assistant", "content": "[对话摘要] ..."}], 1,
        )
        assert not mem.surface_contains_seq(catalog_seq)
        # surface 上不再有 catalog 文本 → attach 路径会重发（previous 为空）
        kinds = [m.get("_prompt_kind") for m in mem.messages]
        assert "skill_catalog" not in kinds
        # durable 原文仍在日志里
        assert any(
            m.get("_seq") == catalog_seq or m.get("_shadowed_by")
            for m in log.durable_messages()
        )

    def test_surface_contains_seq_without_log(self) -> None:
        mem = ConversationMemory(_cfg())
        mem.add_user_message("hello")
        assert not mem.surface_contains_seq(999)
        mem.messages[0]["_seq"] = 7
        assert mem.surface_contains_seq(7)
