"""进程内伪造网页客户端：打字、附件、审批/问答，并导出前端可见对话。

行为对齐 ``web/src/lib/chat-actions.ts`` 的 sendMessage：
- 文件先落到工作区 ``uploads/``（同 ``POST /api/v1/upload``）
- 图片再 ``admit``（同 ``POST /api/v1/attachments``）
- 消息前缀 ``[已上传文件/图片: path]``
- 回合走 ``run_engine_followup``（同 ``/chat/stream``）
- 问答/审批走 InteractionRegistry（同 ``/answer`` ``/approve``）
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from excelmanus.chat_turn import (
    run_engine_followup,
    submit_approval,
    submit_question_answer,
)
from excelmanus.engine import ChatResult
from excelmanus.events import EventCallback, EventType, ToolCallEvent
from excelmanus.logger import get_logger
from excelmanus.session import SessionManager

logger = get_logger("fake_frontend")

_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
_MAX_RESOLVE_YIELDS = 80
_PUMP_INTERVAL_SECONDS = 0.05


def format_upload_notice(kind: str, path: str) -> str:
    """与前端 ``formatUploadNotice`` / 后端标题剥离保持一致。"""
    label = "已上传图片" if kind == "image" else "已上传文件"
    return f"[{label}: {path}]"


def is_image_path(path: str | Path) -> bool:
    return Path(path).suffix.lower() in _IMAGE_EXTS


@dataclass
class AttachedFile:
    """一次伪造前端上传的结果（对齐 /upload 响应）。"""

    filename: str
    path: str
    size: int
    kind: str
    source: str = ""
    attachment_id: str = ""
    media_type: str = ""

    def to_dict(self) -> dict[str, Any]:
        row = {
            "filename": self.filename,
            "path": self.path,
            "size": self.size,
            "kind": self.kind,
        }
        if self.source:
            row["source"] = self.source
        if self.attachment_id:
            row["attachment_id"] = self.attachment_id
            row["media_type"] = self.media_type
        return row


@dataclass
class FakeFrontend:
    """无人值守的网页用户：同一 session 上连续输入。"""

    manager: SessionManager
    session_id: str
    auto_replies: list[str] = field(default_factory=list)
    auto_approve: str = "fullaccess"
    chat_mode: str = "write"
    on_event: EventCallback | None = None
    on_engine: Callable[[Any], None] | None = None

    engine: Any | None = field(default=None, init=False)
    transcript: list[dict[str, Any]] = field(default_factory=list, init=False)
    attached: list[AttachedFile] = field(default_factory=list, init=False)
    auto_reply_count: int = field(default=0, init=False)
    auto_approve_count: int = field(default=0, init=False)
    _reply_queue: list[str] = field(default_factory=list, init=False)
    _tasks: list[asyncio.Task[Any]] = field(default_factory=list, init=False)
    # 已成功提交的问答/审批 id（事件路径与兜底轮询共享，防重复提交）
    _submitted_interactions: set[str] = field(default_factory=set, init=False)
    # 交互兜底轮询任务：持续 resolve 待审批/待问答，避免事件竞态导致卡死
    _pump_task: asyncio.Task[None] | None = field(default=None, init=False)
    _pump_stop: asyncio.Event | None = field(default=None, init=False)

    def __post_init__(self) -> None:
        self._reply_queue = list(self.auto_replies)

    def _apply_full_access(self, engine: Any) -> None:
        """对齐前端「允许本会话全部操作」：默认直接完整权限，不写用户全局配置。"""
        if self.auto_approve != "fullaccess":
            return
        if bool(getattr(engine, "_full_access_enabled", False)):
            return
        try:
            engine._full_access_enabled = True
        except AttributeError:
            return
        logger.info("伪造前端已开启本会话完整权限")

    def _try_answer_question(self, question_id: str) -> bool:
        """提交 ask_user 回答；失败（Future 未注册等）返回 False，由兜底轮询重试。"""
        engine = self.engine
        if engine is None:
            return False
        qid = str(question_id or "").strip()
        if not qid or qid in self._submitted_interactions:
            return True
        if not self._reply_queue:
            registry = getattr(engine, "interaction_registry", None)
            if registry is None:
                return False
            cancel = getattr(registry, "cancel", None)
            if not callable(cancel) or not cancel(qid):
                return False
            self._submitted_interactions.add(qid)
            logger.info("伪造前端未配置 auto_replies，已取消 ask_user: %r", qid)
            return True
        answer = self._reply_queue[0]
        if not submit_question_answer(engine, qid, answer):
            return False
        if self._reply_queue:
            self._reply_queue.pop(0)
        self._submitted_interactions.add(qid)
        self.auto_reply_count += 1
        logger.info("伪造前端应答 ask_user: %r → %r", qid, answer)
        return True

    def _try_resolve_approval(self, approval_id: str) -> bool:
        """提交审批决策；失败返回 False，由兜底轮询重试。"""
        engine = self.engine
        if engine is None:
            return False
        aid = str(approval_id or "").strip()
        if not aid or aid in self._submitted_interactions:
            return True
        if self.auto_approve not in {"accept", "reject", "fullaccess"}:
            return False
        if not submit_approval(engine, aid, self.auto_approve):
            return False
        self._submitted_interactions.add(aid)
        self.auto_approve_count += 1
        logger.info("伪造前端审批 %s: %s", self.auto_approve, aid)
        return True

    def _dispatch(self, event: ToolCallEvent) -> None:
        if self.on_event is not None:
            self.on_event(event)
        engine = self.engine
        if engine is None:
            return
        if event.event_type == EventType.USER_QUESTION and event.question_id:
            self._try_answer_question(str(event.question_id))
        elif event.event_type == EventType.PENDING_APPROVAL and event.approval_id:
            self._try_resolve_approval(str(event.approval_id))
        if event.event_type == EventType.TOOL_CALL_END:
            try:
                self.manager.flush_messages_sync(self.session_id)
            except Exception:
                logger.debug("中间消息持久化失败", exc_info=True)

    def _schedule(self, attempt: Callable[[], bool]) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._tasks.append(loop.create_task(self._resolve_when_ready(attempt)))

    async def _resolve_when_ready(self, attempt: Callable[[], bool]) -> None:
        for _ in range(_MAX_RESOLVE_YIELDS):
            if attempt():
                return
            await asyncio.sleep(0)
        logger.warning("伪造前端交互未命中 pending Future（兜底轮询会继续重试）")

    # ── 交互兜底轮询（防无人批准/应答卡死）─────────────────

    def _start_interaction_pump(self) -> None:
        """启动轮询任务：整个回合内持续代答待审批/待问答。"""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        if self._pump_task is not None and not self._pump_task.done():
            return
        self._pump_stop = asyncio.Event()
        self._pump_task = loop.create_task(self._interaction_pump())

    async def _interaction_pump(self) -> None:
        stop = self._pump_stop
        while stop is not None and not stop.is_set():
            try:
                self._resolve_pending_interactions()
            except Exception:
                logger.debug("交互兜底轮询异常", exc_info=True)
            try:
                await asyncio.wait_for(stop.wait(), timeout=_PUMP_INTERVAL_SECONDS)
            except asyncio.TimeoutError:
                pass

    def _resolve_pending_interactions(self) -> None:
        """读取引擎当前的待审批/待问答并提交决策（幂等，去重后提交）。"""
        engine = self.engine
        if engine is None:
            return
        if self.auto_approve in {"accept", "reject", "fullaccess"}:
            getter = getattr(engine, "current_pending_approval", None)
            approval = getter() if callable(getter) else None
            if approval is not None:
                self._try_resolve_approval(
                    str(getattr(approval, "approval_id", "") or ""),
                )
        getter = getattr(engine, "current_pending_question", None)
        question = getter() if callable(getter) else None
        if question is not None:
            self._try_answer_question(
                str(getattr(question, "question_id", "") or ""),
            )

    async def _stop_interaction_pump(self) -> None:
        if self._pump_stop is not None:
            self._pump_stop.set()
        task = self._pump_task
        self._pump_task = None
        if task is not None and not task.done():
            task.cancel()
        if task is not None:
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._pump_stop = None

    async def drain(self) -> None:
        pending = [task for task in self._tasks if not task.done()]
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        self._tasks.clear()

    def upload_file(self, engine: Any, src: str | Path) -> AttachedFile:
        """把本地文件写入会话工作区 uploads/，等价于前端预上传。"""
        from excelmanus.api_app_state import sanitize_upload_filename
        from excelmanus.workspace.file_service import WorkspaceFileService

        source = Path(src)
        if not source.is_file():
            raise FileNotFoundError(f"附件不存在: {source}")
        root = Path(engine.workspace.root_dir)
        content = source.read_bytes()
        safe_name = f"{uuid.uuid4().hex[:8]}_{sanitize_upload_filename(source.name)}"
        rel = f"uploads/{safe_name}"
        svc = WorkspaceFileService(root)
        svc.raise_if_failed(svc.create(rel, content))
        dest = root / rel
        kind = "image" if is_image_path(source) else "file"
        converted = False
        if kind == "file":
            try:
                from excelmanus.xls_converter import convert_to_xlsx, needs_conversion

                if needs_conversion(dest):
                    # 对齐 POST /api/v1/upload：转换成功后删除原始 .xls，
                    # 避免工作区残留前端用户看不到的文件。
                    converted_dest = convert_to_xlsx(
                        dest, overwrite=True, workspace_root=str(root),
                    )
                    if converted_dest != dest:
                        converted = True
                        try:
                            svc.delete(rel, expected_version=None, observe_live=True)
                        except Exception:
                            logger.debug("删除原始 .xls 失败: %s", rel, exc_info=True)
                        dest = converted_dest
            except Exception:
                logger.debug("上传文件转换失败，保留原格式: %s", source.name, exc_info=True)
        public = f"./{dest.relative_to(root).as_posix()}"
        registry = getattr(engine, "_file_registry", None)
        if registry is not None:
            try:
                entry = registry.register_upload(
                    canonical_path=str(dest.relative_to(root)).replace("\\", "/"),
                    original_name=source.name,
                    size_bytes=dest.stat().st_size,
                )
                # 对齐 /upload：转换后给原始扩展名加别名，方便按原名引用
                if converted and entry is not None:
                    registry.add_alias(
                        entry.id, "original_path", f"./{rel}",
                    )
            except Exception:
                logger.debug("FileRegistry register_upload 失败", exc_info=True)
        attached = AttachedFile(
            filename=source.name,
            path=public,
            size=dest.stat().st_size,
            kind=kind,
            source=str(source),
        )
        if kind == "image":
            try:
                from excelmanus.attachments.admit import admit_image_path

                ref = admit_image_path(source)
                attached.attachment_id = ref.attachment_id
                attached.media_type = ref.media_type
            except Exception:
                logger.warning("图片准入失败: %s", source, exc_info=True)
        self.attached.append(attached)
        logger.info("伪造前端上传 %s → %s", source.name, public)
        return attached

    def _compose_message(
        self,
        text: str,
        uploaded: list[AttachedFile],
    ) -> tuple[str, list[dict[str, str]]]:
        notices: list[str] = []
        images: list[dict[str, str]] = []
        rewritten = text
        for item in uploaded:
            notices.append(format_upload_notice(item.kind, item.path))
            if item.source:
                rewritten = rewritten.replace(item.source, item.path)
                rewritten = rewritten.replace(item.source.replace("\\", "/"), item.path)
            if item.attachment_id:
                row = {
                    "attachment_id": item.attachment_id,
                    "media_type": item.media_type or "image/png",
                    "name": item.filename,
                }
                images.append(row)
        if notices:
            rewritten = f"{chr(10).join(notices)}\n\n{rewritten}"
        return rewritten, images

    async def send(
        self,
        text: str,
        *,
        attachments: list[str] | None = None,
        images: list[str] | None = None,
        chat_mode: str | None = None,
    ) -> ChatResult:
        """像网页输入框一样发送一轮：可带附件/图片，并处理审批。"""
        session_id, engine = await self.manager.acquire_for_chat(self.session_id)
        self.session_id = session_id
        self.engine = engine
        self._apply_full_access(engine)
        if self.on_engine is not None:
            self.on_engine(engine)
        uploaded: list[AttachedFile] = []
        try:
            for path in list(attachments or []) + list(images or []):
                uploaded.append(self.upload_file(engine, path))
            display, image_payloads = self._compose_message(text, uploaded)
            self.transcript.append({
                "role": "user",
                "content": display,
                "typed": text,
                "attachments": [item.to_dict() for item in uploaded],
            })
            self._start_interaction_pump()
            outcome = await run_engine_followup(
                engine,
                display,
                on_event=self._dispatch,
                images=image_payloads,
                chat_mode=chat_mode or self.chat_mode,
            )
            reply = (outcome.result.reply or "").strip()
            self.transcript.append({
                "role": "assistant",
                "content": reply,
                "status": "ok",
            })
            return outcome.result
        except Exception as exc:
            self.transcript.append({
                "role": "assistant",
                "content": f"[ERROR] {exc}",
                "status": "error",
                "error": {"type": type(exc).__name__, "message": str(exc)},
            })
            raise
        finally:
            await self._stop_interaction_pump()
            await self.drain()
            await self.manager.release_for_chat(session_id)

    def export_conversation(self) -> dict[str, Any]:
        """导出伪造前端视角的对话历史（会话库 + 输入稿 + 引擎记忆）。"""
        session_messages: list[dict[str, Any]] = []
        history = getattr(self.manager, "chat_history", None)
        if history is not None:
            try:
                session_messages = history.load_messages(self.session_id)
            except Exception:
                logger.debug("读取会话历史失败", exc_info=True)
        engine_messages: list[dict[str, Any]] = []
        if self.engine is not None:
            try:
                raw = self.engine.memory.get_messages()
                engine_messages = [
                    m if isinstance(m, dict) else {"content": str(m)}
                    for m in raw
                ]
            except Exception:
                logger.debug("读取引擎记忆失败", exc_info=True)
        return {
            "session_id": self.session_id,
            "pipeline": "fake_frontend",
            "transcript": list(self.transcript),
            "session_messages": session_messages,
            "engine_messages": engine_messages,
            "attachments": [item.to_dict() for item in self.attached],
            "auto_reply_count": self.auto_reply_count,
            "auto_approve_count": self.auto_approve_count,
            "auto_approve": self.auto_approve,
            "full_access_enabled": bool(
                getattr(self.engine, "_full_access_enabled", False)
            ),
        }
