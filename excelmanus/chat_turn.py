"""前端直聊的共享回合：提及解析 + followup 参数 + /answer /approve 同源提交。

API 与 bench 必须走这里，避免评测链路和网页聊天漂移。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from excelmanus.engine import ChatResult
from excelmanus.events import EventCallback
from excelmanus.logger import get_logger
from excelmanus.mentions import MentionParser, MentionResolver
from excelmanus.mentions.parser import ResolvedMention

logger = get_logger("chat_turn")


def engine_workspace_root(engine: Any) -> str:
    """读取引擎工作区根。优先 ``config``，再退到 IsolatedWorkspace。"""
    getter = getattr(engine, "get_config", None)
    if callable(getter):
        try:
            cfg = getter()
            root = getattr(cfg, "workspace_root", None)
            if root:
                return str(root)
        except Exception:
            logger.debug("engine.get_config() 读取工作区失败", exc_info=True)
    cfg = getattr(engine, "config", None) or getattr(engine, "_config", None)
    if cfg is not None:
        root = getattr(cfg, "workspace_root", None)
        if root:
            return str(root)
    workspace = getattr(engine, "workspace", None)
    if workspace is not None:
        root_dir = getattr(workspace, "root_dir", None)
        if root_dir:
            return str(root_dir)
    return "."


async def resolve_mentions(
    message: str,
    engine: Any,
) -> tuple[str, list[ResolvedMention] | None]:
    """解析 @ 提及，返回 (display_text, mention_contexts)。

    display_text 将 ``@file:name`` 替换为 ``name``，与网页聊天一致。
    """
    try:
        parse_result = MentionParser.parse(message)
        if not parse_result.mentions:
            return message, None

        from excelmanus.security.guard import FileAccessGuard

        workspace_root = engine_workspace_root(engine)
        guard = FileAccessGuard(workspace_root)
        skill_loader = getattr(engine, "_skill_loader", None)
        if skill_loader is None:
            router = getattr(engine, "_skill_router", None)
            if router is not None:
                skill_loader = getattr(router, "_loader", None)
        resolver = MentionResolver(
            workspace_root=workspace_root,
            guard=guard,
            skill_loader=skill_loader,
            mcp_manager=getattr(engine, "_mcp_manager", None),
        )
        mention_contexts = await resolver.resolve(list(parse_result.mentions))
        return parse_result.display_text, mention_contexts
    except Exception:
        logger.debug("提及解析失败，回退到原始消息", exc_info=True)
        return message, None


@dataclass(frozen=True)
class ChatTurnOutcome:
    """一次与前端相同的 followup 结果。"""

    result: ChatResult
    display_text: str
    mention_contexts: list[ResolvedMention] | None


async def run_engine_followup(
    engine: Any,
    message: str,
    *,
    on_event: EventCallback | None = None,
    images: list[dict[str, Any]] | None = None,
    chat_mode: str = "write",
    present_as: str | None = None,
    display_text: str | None = None,
    mention_contexts: list[ResolvedMention] | None = None,
) -> ChatTurnOutcome:
    """按网页直聊参数调用 ``engine.followup``。

    未预解析时在此解析 @ 提及。``chat_stream`` 可先解析再传入，避免重复。
    """
    if display_text is None:
        display_text, mention_contexts = await resolve_mentions(message, engine)
    result = await engine.followup(
        display_text,
        on_event=on_event,
        mention_contexts=mention_contexts,
        images=images or [],
        chat_mode=chat_mode,
        present_as=present_as,
    )
    return ChatTurnOutcome(
        result=result,
        display_text=display_text,
        mention_contexts=mention_contexts,
    )


def submit_question_answer(
    engine: Any,
    question_id: str,
    answer: str,
) -> bool:
    """提交 ask_user 回答，载荷与 ``POST /api/v1/chat/{id}/answer`` 相同。"""
    qid = str(question_id or "").strip()
    if not qid:
        return False
    registry = getattr(engine, "interaction_registry", None)
    if registry is None:
        return False
    payload: dict[str, Any] = {"raw_input": answer, "question_id": qid}
    try:
        question_flow = getattr(engine, "_question_flow", None)
        pending = question_flow.current() if question_flow is not None else None
        if pending is not None and pending.question_id == qid:
            parsed = question_flow.parse_answer(answer, pending)
            payload = parsed.to_tool_result()
    except Exception:
        logger.debug("解析回答失败，使用原始文本", exc_info=True)
    return bool(registry.resolve(qid, payload))


def submit_approval(
    engine: Any,
    approval_id: str,
    decision: str = "accept",
) -> bool:
    """提交审批决策，载荷与 ``POST /api/v1/chat/{id}/approve`` 相同。"""
    aid = str(approval_id or "").strip()
    if not aid:
        return False
    normalized = str(decision or "accept").strip().lower()
    if normalized not in {"accept", "reject", "fullaccess"}:
        normalized = "accept"
    registry = getattr(engine, "interaction_registry", None)
    if registry is None:
        return False
    return bool(registry.resolve(aid, {"decision": normalized, "approval_id": aid}))
