"""通用消息处理器：渠道无关的命令路由、聊天流、审批/问答处理。

所有渠道适配器将入站消息归一化为 ChannelMessage 后，
统一交由 MessageHandler 处理，再通过 ChannelAdapter 回送响应。
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path

from excelmanus.channels.api_client import ChatResult, ExcelManusAPIClient, ProgressCallback
from excelmanus.channels.base import ChannelAdapter, ChannelMessage
from excelmanus.channels.session_store import SessionStore

logger = logging.getLogger("channels.handler")

# 支持上传的文件扩展名
ALLOWED_UPLOAD_EXTENSIONS = {".xlsx", ".xls", ".csv", ".png", ".jpg", ".jpeg"}


class PendingInteraction:
    """跟踪待处理的审批/问答。"""

    def __init__(
        self,
        interaction_type: str,  # "approval" | "question"
        interaction_id: str,
        session_id: str,
        chat_id: str,
        message_id: str = "",
    ) -> None:
        self.type = interaction_type
        self.id = interaction_id
        self.session_id = session_id
        self.chat_id = chat_id
        self.message_id = message_id


class MessageHandler:
    """渠道无关的消息处理器。

    职责：
    - 命令路由 (/start, /help, /new, /model, /addmodel, /delmodel, /abort)
    - 文本消息 → 调用 API → 回送结果
    - 文件上传 → 写入工作区 → 调用 API
    - 审批/问答回调处理
    - 用户权限检查
    """

    def __init__(
        self,
        adapter: ChannelAdapter,
        api_client: ExcelManusAPIClient,
        session_store: SessionStore,
        allowed_users: set[str] | None = None,
    ) -> None:
        self.adapter = adapter
        self.api = api_client
        self.sessions = session_store
        self.allowed_users = allowed_users or set()
        # (chat_id:user_id) → PendingInteraction
        self._pending: dict[str, PendingInteraction] = {}
        # per-user async locks: 防止同一用户并发 stream_chat 导致 session 覆盖/竞争
        self._user_locks: dict[str, asyncio.Lock] = {}

    @staticmethod
    def _pending_key(chat_id: str, user_id: str) -> str:
        return f"{chat_id}:{user_id}"

    def check_user(self, user_id: str) -> bool:
        """检查用户是否有权使用。空集合 = 不限制。"""
        if not self.allowed_users:
            return True
        return user_id in self.allowed_users

    # ── 入口 ──

    def _get_user_lock(self, chat_id: str, user_id: str) -> asyncio.Lock:
        """获取或创建 per-user 异步锁。"""
        key = self._pending_key(chat_id, user_id)
        lock = self._user_locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._user_locks[key] = lock
        return lock

    async def _with_user_lock(
        self, msg: ChannelMessage, coro_func, *args,
    ) -> None:
        """在 per-user 锁保护下执行协程，排队时通知用户。"""
        lock = self._get_user_lock(msg.chat_id, msg.user.user_id)
        if lock.locked():
            await self.adapter.send_text(
                msg.chat_id, "⏳ 上一条消息正在处理中，已排队等待…",
            )
        async with lock:
            await coro_func(msg, *args)

    async def handle_message(self, msg: ChannelMessage) -> None:
        """处理入站消息的统一入口。"""
        if not self.check_user(msg.user.user_id):
            await self.adapter.send_text(msg.chat_id, "⛔ 无权限使用此 Bot")
            return

        # 回调数据（按钮点击）— 需要锁保护以防与进行中的 chat 竞争
        if msg.callback_data:
            await self._with_user_lock(msg, self._handle_callback)
            return

        # 文件上传 — 需要锁保护
        if msg.files:
            await self._with_user_lock(msg, self._handle_file_upload)
            return

        # 命令 — 不加锁，确保 /abort /new 等在处理中仍可执行
        if msg.is_command:
            await self._handle_command(msg)
            return

        # 普通文本 — 需要锁保护
        if msg.text.strip():
            await self._with_user_lock(msg, self._handle_text)

    # ── 命令路由 ──

    async def _handle_command(self, msg: ChannelMessage) -> None:
        """路由 /command 到对应处理函数。"""
        cmd = msg.command.lower()
        handlers = {
            "start": self._cmd_start,
            "help": self._cmd_help,
            "new": self._cmd_new,
            "model": self._cmd_model,
            "addmodel": self._cmd_addmodel,
            "delmodel": self._cmd_delmodel,
            "abort": self._cmd_abort,
        }
        handler = handlers.get(cmd)
        if handler:
            await handler(msg)
        else:
            await self.adapter.send_text(msg.chat_id, f"❓ 未知命令: /{cmd}\n输入 /help 查看所有命令")

    async def _cmd_start(self, msg: ChannelMessage) -> None:
        await self.adapter.send_text(
            msg.chat_id,
            "👋 ExcelManus Bot 已就绪！\n\n"
            "直接发消息即可与 ExcelManus 对话。\n"
            "发送 Excel 文件（.xlsx/.xls/.csv）可上传到工作区。\n\n"
            "输入 /help 查看所有命令",
        )

    async def _cmd_help(self, msg: ChannelMessage) -> None:
        await self.adapter.send_text(
            msg.chat_id,
            "📖 ExcelManus Bot 命令\n\n"
            "💬 对话\n"
            "  直接发文字 → 与 AI 对话\n"
            "  发送文件 → 上传到工作区并分析\n"
            "  /new — 新建对话（清除历史）\n"
            "  /abort — 终止当前任务\n\n"
            "🤖 模型管理\n"
            "  /model — 查看模型列表\n"
            "  /model <名称> — 切换模型\n"
            "  /addmodel — 添加新模型（查看格式）\n"
            "  /delmodel <名称> — 删除模型\n\n"
            "📎 支持的文件\n"
            "  Excel: .xlsx .xls .csv\n"
            "  图片: .png .jpg .jpeg",
        )

    async def _cmd_new(self, msg: ChannelMessage) -> None:
        self.sessions.remove(self.adapter.name, msg.chat_id, msg.user.user_id)
        self._pending.pop(self._pending_key(msg.chat_id, msg.user.user_id), None)
        await self.adapter.send_text(msg.chat_id, "🆕 已新建对话，历史已清除。")

    async def _cmd_abort(self, msg: ChannelMessage) -> None:
        session_id = self.sessions.get(self.adapter.name, msg.chat_id, msg.user.user_id)
        if not session_id:
            await self.adapter.send_text(msg.chat_id, "⚠️ 当前没有活跃的会话")
            return
        try:
            await self.api.abort(session_id)
            await self.adapter.send_text(msg.chat_id, "🛑 已终止当前任务")
        except Exception as e:
            await self.adapter.send_text(msg.chat_id, f"❌ 终止失败: {e}")

    async def _cmd_model(self, msg: ChannelMessage) -> None:
        args = msg.command_args
        try:
            if args:
                target = " ".join(args)
                await self.api.switch_model(target)
                await self.adapter.send_text(msg.chat_id, f"✅ 已切换到模型: {target}")
                return

            models = await self.api.list_models()
            if not models:
                await self.adapter.send_text(msg.chat_id, "暂无可用模型")
                return

            lines = ["🤖 可用模型：\n"]
            for m in models:
                name = m["name"]
                model_id = m.get("model", "")
                desc = m.get("description", "")
                line = f"  {'→ ' if m.get('active') else '   '}{name}"
                if m.get("active"):
                    line += " ✅"
                line += f"\n    {model_id}"
                if desc:
                    line += f"\n    {desc}"
                lines.append(line)

            lines.append(f"\n切换: /model <名称>")
            lines.append(f"添加: /addmodel")
            await self.adapter.send_text(msg.chat_id, "\n".join(lines))

        except Exception as e:
            await self.adapter.send_text(msg.chat_id, f"❌ 出错了: {e}")

    async def _cmd_addmodel(self, msg: ChannelMessage) -> None:
        args = msg.command_args
        if len(args) < 4:
            await self.adapter.send_text(
                msg.chat_id,
                "📝 添加模型格式：\n\n"
                "/addmodel <名称> <模型ID> <base_url> <api_key> [描述]\n\n"
                "示例：\n"
                "/addmodel gpt4 gpt-4o https://api.openai.com/v1 sk-xxx 我的GPT4",
            )
            return

        name, model_id, base_url, api_key = args[0], args[1], args[2], args[3]
        description = " ".join(args[4:]) if len(args) > 4 else ""

        try:
            await self.api.add_model(name, model_id, base_url, api_key, description)
            await self.adapter.send_text(
                msg.chat_id,
                f"✅ 已添加: {name}\n   {model_id}\n\n切换: /model {name}",
            )
        except Exception as e:
            await self.adapter.send_text(msg.chat_id, f"❌ 添加失败: {e}")

    async def _cmd_delmodel(self, msg: ChannelMessage) -> None:
        args = msg.command_args
        if not args:
            await self.adapter.send_text(msg.chat_id, "用法: /delmodel <模型名称>")
            return
        try:
            await self.api.delete_model(args[0])
            await self.adapter.send_text(msg.chat_id, f"🗑 已删除: {args[0]}")
        except Exception as e:
            await self.adapter.send_text(msg.chat_id, f"❌ 删除失败: {e}")

    # ── 文本消息 ──

    async def _handle_text(self, msg: ChannelMessage) -> None:
        """处理普通文本消息 → 调用聊天 API。"""
        user_id = msg.user.user_id
        pk = self._pending_key(msg.chat_id, user_id)
        await self.adapter.show_typing(msg.chat_id)

        # 拦截：若有待回答的问题，将自由文本路由到 answer_question
        pending = self._pending.get(pk)
        if pending and pending.type == "question":
            self._pending.pop(pk, None)
            session_id = pending.session_id
            question_id = pending.id

            if pending.message_id:
                await self.adapter.update_question_result(
                    msg.chat_id, pending.message_id, f"💬 已回答: {msg.text}",
                )

            try:
                await self.api.answer_question(session_id, question_id, msg.text)
            except Exception as e:
                logger.exception("Free-text answer error for user %s", user_id)
                await self.adapter.send_text(msg.chat_id, f"❌ 处理回答失败: {e}")
            return

        session_id = self.sessions.get(self.adapter.name, msg.chat_id, user_id)
        progress_cb = self._make_progress_callback(msg.chat_id)
        try:
            result = await self.api.stream_chat(msg.text, session_id, on_progress=progress_cb)
            self.sessions.set(self.adapter.name, msg.chat_id, user_id, result.session_id)
            await self._send_chat_result(msg.chat_id, user_id, result)
        except Exception as e:
            logger.exception("Chat error for user %s", user_id)
            await self.adapter.send_text(msg.chat_id, f"❌ 出错了: {type(e).__name__}: {e}")

    # ── 文件上传 ──

    async def _handle_file_upload(self, msg: ChannelMessage) -> None:
        """处理文件上传 → 写入工作区 → 调用聊天 API。"""
        user_id = msg.user.user_id

        for file_att in msg.files:
            ext = Path(file_att.filename).suffix.lower()
            if ext not in ALLOWED_UPLOAD_EXTENSIONS:
                await self.adapter.send_text(
                    msg.chat_id,
                    f"⚠️ 不支持的文件类型: {ext}\n仅支持 Excel (.xlsx/.xls/.csv) 和图片文件",
                )
                continue

            await self.adapter.show_typing(msg.chat_id)
            await self.api.upload_to_workspace(file_att.filename, file_att.data)

            caption = msg.text or f"帮我分析 {file_att.filename}"
            chat_msg = f"@file:{file_att.filename} {caption}"

            session_id = self.sessions.get(self.adapter.name, msg.chat_id, user_id)
            progress_cb = self._make_progress_callback(msg.chat_id)
            try:
                result = await self.api.stream_chat(chat_msg, session_id, on_progress=progress_cb)
                self.sessions.set(self.adapter.name, msg.chat_id, user_id, result.session_id)
                await self._send_chat_result(msg.chat_id, user_id, result)
            except Exception as e:
                logger.exception("Document chat error for user %s", user_id)
                await self.adapter.send_text(msg.chat_id, f"❌ 出错了: {e}")

    # ── 回调处理 ──

    async def _handle_callback(self, msg: ChannelMessage) -> None:
        """处理按钮回调数据。"""
        data = msg.callback_data or ""
        user_id = msg.user.user_id

        if data == "noop":
            return

        if data.startswith("approve:") or data.startswith("reject:"):
            await self._handle_approval_callback(msg, data)
            return

        if data.startswith("answer:"):
            await self._handle_answer_callback(msg, data)
            return

    async def _handle_approval_callback(self, msg: ChannelMessage, data: str) -> None:
        """处理审批回调。"""
        action, approval_id = data.split(":", 1)
        user_id = msg.user.user_id
        session_id = self.sessions.get(self.adapter.name, msg.chat_id, user_id)

        if not session_id:
            await self.adapter.send_text(msg.chat_id, "⚠️ 会话已过期，请重新开始对话")
            return

        decision = "approve" if action == "approve" else "reject"
        result_text = "✅ 已批准" if action == "approve" else "❌ 已拒绝"

        # 更新卡片状态
        pk = self._pending_key(msg.chat_id, user_id)
        pending = self._pending.pop(pk, None)
        if pending and pending.message_id:
            await self.adapter.update_approval_result(
                msg.chat_id, pending.message_id, result_text,
            )

        try:
            await self.adapter.show_typing(msg.chat_id)
            await self.api.approve(session_id, approval_id, decision)
            await self.adapter.send_text(msg.chat_id, result_text)
        except Exception as e:
            logger.exception("Approval callback error")
            await self.adapter.send_text(msg.chat_id, f"❌ 处理审批失败: {e}")

    async def _handle_answer_callback(self, msg: ChannelMessage, data: str) -> None:
        """处理问答回调。"""
        parts = data.split(":", 2)
        if len(parts) < 3:
            return
        _, question_id, answer = parts

        user_id = msg.user.user_id
        session_id = self.sessions.get(self.adapter.name, msg.chat_id, user_id)

        if not session_id:
            await self.adapter.send_text(msg.chat_id, "⚠️ 会话已过期，请重新开始对话")
            return

        # 更新卡片状态
        pk = self._pending_key(msg.chat_id, user_id)
        pending = self._pending.pop(pk, None)
        if pending and pending.message_id:
            await self.adapter.update_question_result(
                msg.chat_id, pending.message_id, f"💬 已回答: {answer}",
            )

        try:
            await self.adapter.show_typing(msg.chat_id)
            await self.api.answer_question(session_id, question_id, answer)
        except Exception as e:
            logger.exception("Question callback error")
            await self.adapter.send_text(msg.chat_id, f"❌ 处理回答失败: {e}")

    # ── 进度回调 ──

    def _make_progress_callback(
        self, chat_id: str, min_interval: float = 5.0,
    ) -> ProgressCallback:
        """创建节流进度回调，避免高频消息轰炸渠道。

        Args:
            chat_id: 目标聊天 ID。
            min_interval: 两次进度消息之间的最小间隔（秒）。
        """
        last_ts: list[float] = [0.0]  # mutable container for closure
        last_stage: list[str] = [""]

        async def _on_progress(stage: str, message: str) -> None:
            now = time.monotonic()
            # 同一阶段的更新需要间隔 min_interval 秒；阶段切换立即推送
            if stage == last_stage[0] and (now - last_ts[0]) < min_interval:
                return
            last_ts[0] = now
            last_stage[0] = stage
            try:
                await self.adapter.send_progress(chat_id, stage, message)
            except Exception:
                logger.debug("send_progress failed for chat %s", chat_id, exc_info=True)

        return _on_progress

    # ── 结果分发 ──

    async def _send_chat_result(
        self, chat_id: str, user_id: str, result: ChatResult,
    ) -> None:
        """将 ChatResult 分发到渠道：文本回复、审批卡片、问答卡片、文件下载。"""

        # 文本回复
        if result.reply:
            await self.adapter.send_markdown(chat_id, result.reply)

        # 文件下载
        for dl in result.file_downloads:
            file_path = dl.get("file_path", "")
            filename = dl.get("filename", "") or Path(file_path).name
            if file_path:
                try:
                    await self.adapter.send_file(chat_id, file_path, filename)
                except Exception:
                    logger.warning("发送文件失败: %s", file_path, exc_info=True)

        # 审批请求
        if result.approval:
            approval_id = result.approval.get("approval_id", "")
            tool_name = result.approval.get("approval_tool_name", "unknown")
            risk_level = result.approval.get("risk_level", "yellow")
            args_summary = result.approval.get("args_summary", {})

            await self.adapter.send_approval_card(
                chat_id, approval_id, tool_name, risk_level, args_summary,
            )

            session_id = self.sessions.get(self.adapter.name, chat_id, user_id) or ""
            pk = self._pending_key(chat_id, user_id)
            self._pending[pk] = PendingInteraction(
                interaction_type="approval",
                interaction_id=approval_id,
                session_id=session_id,
                chat_id=chat_id,
            )

        # 问答请求
        if result.question:
            question_id = result.question.get("id", "")
            header = result.question.get("header", "")
            text = result.question.get("text", "")
            options = result.question.get("options", [])

            await self.adapter.send_question_card(
                chat_id, question_id, header, text, options,
            )

            pk = self._pending_key(chat_id, user_id)
            session_id = self.sessions.get(self.adapter.name, chat_id, user_id) or ""
            self._pending[pk] = PendingInteraction(
                interaction_type="question",
                interaction_id=question_id,
                session_id=session_id,
                chat_id=chat_id,
            )

        # 无任何内容
        if (
            not result.reply
            and not result.approval
            and not result.question
            and not result.file_downloads
        ):
            await self.adapter.send_text(chat_id, "（无回复内容）")
