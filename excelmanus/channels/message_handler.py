"""通用消息处理器：渠道无关的命令路由、聊天流、审批/问答处理。

所有渠道适配器将入站消息归一化为 ChannelMessage 后，
统一交由 MessageHandler 处理，再通过 ChannelAdapter 回送响应。
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import time
import uuid as _uuid
from pathlib import Path

from typing import Any

from excelmanus.channels.api_client import ExcelManusAPIClient
from excelmanus.channels.base import ChannelAdapter, ChannelMessage
from excelmanus.channels.output_manager import ChunkedOutputManager
from excelmanus.channels.rate_limit import ChannelRateLimiter, RateLimitConfig
from excelmanus.channels.session_store import SessionStore

logger = logging.getLogger("excelmanus.channels.handler")

# 支持上传的文件扩展名
ALLOWED_UPLOAD_EXTENSIONS = {".xlsx", ".xls", ".csv", ".png", ".jpg", ".jpeg"}

# chat_mode 元信息：(图标, 中文名, 说明)
MODE_INFO: dict[str, tuple[str, str, str]] = {
    "write": ("✏️", "写入", "可读写 Excel、执行操作"),
    "read":  ("🔍", "读取", "只读分析，不修改文件"),
    "plan":  ("📋", "计划", "规划步骤，不立即执行"),
}
VALID_MODES = set(MODE_INFO.keys())

# 并发模式元信息：(图标, 中文名, 说明)
CONCURRENCY_INFO: dict[str, tuple[str, str, str]] = {
    "queue": ("⏳", "排队", "新消息排队等待，FIFO 串行执行"),
    "steer": ("🔄", "转向", "中断旧任务，立即处理新消息"),
    "guide": ("📨", "引导", "消息注入到运行中的 agent，不打断工具执行"),
}
VALID_CONCURRENCY = set(CONCURRENCY_INFO.keys())


# 待处理交互的 TTL（秒），超时自动过期
PENDING_TTL_SECONDS = 1800.0  # 30 分钟


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
        self.created_at: float = time.monotonic()


class PendingFile:
    """缓冲等待用户指令的已上传文件。"""

    def __init__(
        self,
        filename: str,
        is_image: bool = False,
        mime_type: str = "",
        image_data: bytes | None = None,
        workspace_path: str = "",
    ) -> None:
        self.filename = filename
        self.is_image = is_image
        self.mime_type = mime_type
        self.image_data = image_data  # 图片原始数据，用于 vision API
        self.workspace_path = workspace_path  # 上传后在工作区的实际路径
        self.created_at: float = time.monotonic()


class MessageHandler:
    """渠道无关的消息处理器。

    职责：
    - 命令路由 (/start, /help, /new, /mode, /model, /addmodel, /delmodel, /abort)
    - 文本消息 → 调用 API → 回送结果
    - 文件上传 → 写入工作区 → 调用 API
    - 审批/问答回调处理
    - 用户权限检查
    """

    # origin_channel → 用户可见标签映射
    _CHANNEL_LABELS: dict[str, str] = {
        "web": "Web 端",
        "telegram": "Telegram",
        "qq": "QQ",
        "feishu": "飞书",
    }

    # 速率限制豁免命令（即使被限流也允许执行）
    _EXEMPT_COMMANDS: frozenset[str] = frozenset({"abort", "new", "start", "help"})
    # 未绑定用户仍可执行的命令（绑定流程必需）
    _BIND_EXEMPT_COMMANDS: frozenset[str] = frozenset({"bind", "bindstatus", "help", "start", "admin"})

    def __init__(
        self,
        adapter: ChannelAdapter,
        api_client: ExcelManusAPIClient,
        session_store: SessionStore,
        allowed_users: set[str] | None = None,
        rate_limit_config: RateLimitConfig | None = None,
        bind_manager: Any | None = None,
        event_bridge: Any | None = None,
        config_store: Any | None = None,
    ) -> None:
        self.adapter = adapter
        self.api = api_client
        self.sessions = session_store
        self.allowed_users = allowed_users or set()
        self._rate_limiter = ChannelRateLimiter(rate_limit_config)
        self._bind_manager = bind_manager  # ChannelBindManager 实例
        self._event_bridge = event_bridge  # EventBridge 实例
        self._config_store = config_store  # GlobalConfigStore 实例（动态读取设置）
        # (chat_id:user_id) → PendingInteraction
        self._pending: dict[str, PendingInteraction] = {}
        # per-user async locks: 防止同一用户并发 stream_chat 导致 session 覆盖/竞争
        self._user_locks: dict[str, asyncio.Lock] = {}
        # staged 文件索引缓存: pending_key → [{original_path, ...}, ...]
        self._staged_cache: dict[str, list[dict]] = {}
        # 最近 apply 结果缓存: pending_key → [{original_path, undo_path}, ...]
        self._last_apply: dict[str, list[dict]] = {}
        # 并发模式: "queue" | "steer" | "guide"，per (chat_id:user_id) 覆盖
        self._default_concurrency: str = "queue"
        self._user_concurrency: dict[str, str] = {}
        # steer/guide 模式：跟踪每个用户的 in-flight task
        self._user_tasks: dict[str, asyncio.Task] = {}  # type: ignore[type-arg]
        # 渠道用户 → auth user_id 缓存（避免每条消息都查 DB）
        # 值为 (auth_uid, monotonic_timestamp)
        self._auth_user_cache: dict[str, tuple[str | None, float]] = {}
        self._AUTH_CACHE_TTL: float = 60.0  # 缓存有效期（秒），bind/unbind 会主动失效
        # EventBridge: 记录每个 chat/user 当前绑定到哪个 auth_user_id（用于解绑/重绑后的订阅切换）
        self._bridge_registered: dict[str, str] = {}
        # 待处理文件缓冲：用户发送附件后等待进一步指令
        self._pending_files: dict[str, list[PendingFile]] = {}
        # 群聊拒绝消息冷却：chat_id → 上次发送时间（monotonic）
        self._group_deny_last: dict[str, float] = {}
        # 过期状态清理计数器：每 N 次 handle_message 触发一次全量清理
        self._msg_count: int = 0
        self._STALE_CLEANUP_INTERVAL: int = 200  # 每 200 条消息清理一次
        self._STALE_TTL: float = 3600.0  # 1 小时无活动视为过期

    @property
    def _require_bind(self) -> bool:
        """是否强制要求渠道用户绑定前端账号。

        优先级: 环境变量 > config_kv 数据库配置 > 默认 False
        """
        env_val = os.environ.get("EXCELMANUS_CHANNEL_REQUIRE_BIND", "").strip().lower()
        if env_val:
            return env_val in ("1", "true", "yes")
        if self._config_store is not None:
            try:
                db_val = self._config_store.get("channel_require_bind", "")
                if db_val:
                    return db_val.strip().lower() in ("1", "true", "yes")
            except Exception:
                pass
        return False

    # ── 群聊策略 ──

    # 群聊拒绝消息的冷却时间（每个 chat_id 5 分钟内只回复一次）
    _GROUP_DENY_COOLDOWN = 300.0

    @property
    def _group_policy(self) -> str:
        """群聊准入策略。优先级: 环境变量 > config_kv > 智能默认值。

        可选值: "deny" | "allow" | "whitelist" | "blacklist"
        """
        _VALID = ("deny", "allow", "whitelist", "blacklist")
        env_val = os.environ.get("EXCELMANUS_CHANNEL_GROUP_POLICY", "").strip().lower()
        if env_val in _VALID:
            return env_val
        if self._config_store is not None:
            try:
                db_val = self._config_store.get("channel_group_policy", "")
                if db_val.strip().lower() in _VALID:
                    return db_val.strip().lower()
            except Exception:
                pass
        # 智能默认: 强制绑定模式下默认 deny, 否则 allow
        return "deny" if self._require_bind else "allow"

    @property
    def _group_whitelist(self) -> set[str]:
        """白名单群 chat_id 集合。"""
        if self._config_store is None:
            return set()
        try:
            raw = self._config_store.get("channel_group_whitelist", "")
            if raw:
                return set(json.loads(raw))
        except Exception:
            pass
        return set()

    @property
    def _group_blacklist(self) -> set[str]:
        """黑名单群 chat_id 集合。"""
        if self._config_store is None:
            return set()
        try:
            raw = self._config_store.get("channel_group_blacklist", "")
            if raw:
                return set(json.loads(raw))
        except Exception:
            pass
        return set()

    @property
    def _admin_users(self) -> set[str]:
        """管理员平台用户 ID 集合。环境变量 + config_kv 合并。"""
        result: set[str] = set()
        env_val = os.environ.get("EXCELMANUS_CHANNEL_ADMINS", "").strip()
        if env_val:
            result.update(uid.strip() for uid in env_val.split(",") if uid.strip())
        if self._config_store is not None:
            try:
                db_val = self._config_store.get("channel_admin_users", "")
                if db_val:
                    result.update(uid.strip() for uid in db_val.split(",") if uid.strip())
            except Exception:
                pass
        return result

    def _is_admin(self, user_id: str) -> bool:
        """检查用户是否为 Bot 管理员。"""
        admins = self._admin_users
        if not admins:
            return False
        return user_id in admins

    def _check_group_access(self, msg: ChannelMessage) -> bool:
        """检查群聊准入。返回 True 表示应拒绝（已处理回复）。"""
        # 管理员始终放行
        if self._is_admin(msg.user.user_id):
            return False

        policy = self._group_policy
        if policy == "allow":
            return False

        if policy == "deny":
            self._send_group_deny_once(msg.chat_id, "🔒 此 Bot 仅支持私聊使用，不支持群聊。")
            return True

        if policy == "whitelist":
            if msg.chat_id in self._group_whitelist:
                return False
            self._send_group_deny_once(msg.chat_id, "🔒 此群未获授权使用 Bot。请联系管理员。")
            return True

        if policy == "blacklist":
            if msg.chat_id in self._group_blacklist:
                self._send_group_deny_once(msg.chat_id, "🚫 此群已被禁止使用 Bot。")
                return True
            return False

        return False

    def _send_group_deny_once(self, chat_id: str, text: str) -> None:
        """发送群聊拒绝消息，带冷却避免刷屏。"""
        now = time.monotonic()
        last = self._group_deny_last.get(chat_id, 0.0)
        if (now - last) < self._GROUP_DENY_COOLDOWN:
            return
        self._group_deny_last[chat_id] = now
        task = asyncio.create_task(self.adapter.send_text(chat_id, text))
        task.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)

    @staticmethod
    def _pending_key(chat_id: str, user_id: str) -> str:
        return f"{chat_id}:{user_id}"

    def _cleanup_expired_pending(self) -> None:
        """移除超过 TTL 的待处理交互和待处理文件缓冲。"""
        now = time.monotonic()
        expired = [
            k for k, v in self._pending.items()
            if (now - v.created_at) > PENDING_TTL_SECONDS
        ]
        for k in expired:
            self._pending.pop(k, None)
        if expired:
            logger.debug("清理 %d 个过期的待处理交互", len(expired))

        # 清理过期的待处理文件缓冲
        expired_files = [
            k for k, flist in self._pending_files.items()
            if flist and (now - flist[0].created_at) > PENDING_TTL_SECONDS
        ]
        for k in expired_files:
            self._pending_files.pop(k, None)
        if expired_files:
            logger.debug("清理 %d 个过期的待处理文件缓冲", len(expired_files))

    def _cleanup_stale_state(self) -> None:
        """清理长时间无活动用户的内存状态，防止 dict 无界增长。

        清理对象：_user_locks（无 waiter 的锁）、_staged_cache、_last_apply、
        _user_concurrency、_user_tasks（已完成）、_auth_user_cache、
        _bridge_registered、_group_deny_last。
        """
        now = time.monotonic()
        cleaned = 0

        # _user_locks: 移除未锁定且无 waiter 的锁
        stale = [k for k, lock in self._user_locks.items() if not lock.locked()]
        for k in stale:
            del self._user_locks[k]
        cleaned += len(stale)

        # _user_tasks: 移除已完成的 task
        done = [k for k, t in self._user_tasks.items() if t.done()]
        for k in done:
            del self._user_tasks[k]
        cleaned += len(done)

        # _staged_cache / _last_apply: 直接清空（短期缓存，下次 /staged 会重建）
        if self._staged_cache:
            cleaned += len(self._staged_cache)
            self._staged_cache.clear()
        if self._last_apply:
            cleaned += len(self._last_apply)
            self._last_apply.clear()

        # _group_deny_last: 移除超过冷却时间的条目
        stale_deny = [
            k for k, ts in self._group_deny_last.items()
            if (now - ts) > self._GROUP_DENY_COOLDOWN
        ]
        for k in stale_deny:
            del self._group_deny_last[k]
        cleaned += len(stale_deny)

        # _auth_user_cache: 清除过期条目（TTL 已过期的）
        stale_auth = [
            k for k, (_, ts) in self._auth_user_cache.items()
            if (now - ts) > self._AUTH_CACHE_TTL * 10  # 10x TTL 才清理条目本身
        ]
        for k in stale_auth:
            del self._auth_user_cache[k]
        cleaned += len(stale_auth)

        # _user_concurrency: 移除默认值条目（等于 _default_concurrency 的无需保留）
        default_cc = [
            k for k, v in self._user_concurrency.items()
            if v == self._default_concurrency
        ]
        for k in default_cc:
            del self._user_concurrency[k]
        cleaned += len(default_cc)

        if cleaned:
            logger.debug("清理 %d 个过期的用户状态条目", cleaned)

    @property
    def _dynamic_allowed_users(self) -> set[str]:
        """从 config_kv 读取的动态允许用户列表。"""
        if self._config_store is None:
            return set()
        try:
            raw = self._config_store.get("channel_allowed_users", "")
            if raw:
                return set(json.loads(raw))
        except Exception:
            pass
        return set()

    def check_user(self, user_id: str) -> bool:
        """检查用户是否有权使用。空集合 = 不限制。管理员始终放行。"""
        if self._is_admin(user_id):
            return True
        if not self.allowed_users:
            return True
        if user_id in self.allowed_users:
            return True
        # 检查动态允许用户
        dynamic = self._dynamic_allowed_users
        if dynamic and user_id in dynamic:
            return True
        return False

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

    def _get_user_concurrency(self, chat_id: str, user_id: str) -> str:
        """获取用户当前并发模式。"""
        key = self._pending_key(chat_id, user_id)
        return self._user_concurrency.get(key, self._default_concurrency)

    async def _dispatch_by_concurrency(
        self, msg: ChannelMessage, coro_func, *args,
    ) -> None:
        """根据用户并发模式分发：queue / steer / guide。"""
        mode = self._get_user_concurrency(msg.chat_id, msg.user.user_id)
        if mode == "steer":
            await self._with_steer(msg, coro_func, *args)
        elif mode == "guide":
            await self._with_guide(msg, coro_func, *args)
        else:
            await self._with_user_lock(msg, coro_func, *args)

    async def _with_steer(
        self, msg: ChannelMessage, coro_func, *args,
    ) -> None:
        """Steer 模式：中断旧任务，立即处理新消息。"""
        key = self._pending_key(msg.chat_id, msg.user.user_id)

        # 1) 如果存在 in-flight task → 中断
        old_task = self._user_tasks.get(key)
        if old_task and not old_task.done():
            session_id = self.sessions.get(
                self.adapter.name, msg.chat_id, msg.user.user_id,
            )
            if session_id:
                obo = self._resolve_on_behalf_of(msg.user.user_id)
                await self._safe_abort(session_id, on_behalf_of=obo)
            old_task.cancel()
            try:
                await old_task
            except (asyncio.CancelledError, Exception):
                pass
            self._pending.pop(key, None)
            await self.adapter.send_text(
                msg.chat_id, "🔄 已中断上一任务，处理新请求…",
            )

        # 2) 在可追踪的 Task 中执行新请求
        task = asyncio.create_task(coro_func(msg, *args))
        self._user_tasks[key] = task
        try:
            await task
        except asyncio.CancelledError:
            pass  # 被更新的消息取消，正常退出

    async def _with_guide(
        self, msg: ChannelMessage, coro_func, *args,
    ) -> None:
        """Guide 模式：agent 执行中注入追加指令，无 in-flight 时正常处理。"""
        lock = self._get_user_lock(msg.chat_id, msg.user.user_id)

        if lock.locked():
            # agent 正在执行 → 注入引导消息，不启动新 stream_chat
            session_id = self.sessions.get(
                self.adapter.name, msg.chat_id, msg.user.user_id,
            )
            if session_id:
                guide_text = msg.text.strip()
                if not guide_text:
                    guide_text = "(用户发送了非文本内容)"
                obo = self._resolve_on_behalf_of(msg.user.user_id)
                try:
                    await self.api.guide_message(session_id, guide_text, on_behalf_of=obo)
                    await self.adapter.send_text(
                        msg.chat_id,
                        "📨 消息已送达 agent，将在下次迭代时处理",
                    )
                except Exception as e:
                    logger.warning("Guide 消息投递失败: %s", e)
                    await self.adapter.send_text(
                        msg.chat_id, f"❌ 引导消息投递失败: {e}",
                    )
            else:
                await self.adapter.send_text(
                    msg.chat_id, "⚠️ 无活跃会话，消息未送达",
                )
            return

        # 无 in-flight → 正常处理（加锁防并发）
        async with lock:
            await coro_func(msg, *args)

    async def _safe_abort(self, session_id: str, on_behalf_of: str | None = None) -> None:
        """安全终止后端任务，吞异常。"""
        try:
            await self.api.abort(session_id, on_behalf_of=on_behalf_of)
        except Exception:
            logger.debug("Steer abort 失败（已忽略）", exc_info=True)

    @staticmethod
    def _classify_action(msg: ChannelMessage) -> str:
        """将入站消息分类为限流桶类型。"""
        if msg.is_command or msg.callback_data:
            return "command"
        if msg.files:
            return "upload"
        # 图片或普通文本 → chat（触发 LLM）
        return "chat"

    def _is_exempt(self, msg: ChannelMessage) -> bool:
        """检查消息是否豁免速率限制。"""
        # 豁免命令始终放行
        if msg.is_command and msg.command.lower() in self._EXEMPT_COMMANDS:
            return True
        # 有匹配的待处理审批/问答回调时放行（用户在完成已发起的交互）
        if msg.callback_data:
            pk = self._pending_key(msg.chat_id, msg.user.user_id)
            if pk in self._pending:
                return True
        # 有待回答问题时，自由文本回复也放行
        if msg.text.strip() and not msg.is_command and not msg.files and not msg.images:
            pk = self._pending_key(msg.chat_id, msg.user.user_id)
            pending = self._pending.get(pk)
            if pending and pending.type == "question":
                return True
        return False

    def _ensure_bridge_subscription(self, chat_id: str, user_id: str) -> None:
        """为已绑定用户在 EventBridge 上注册回调（懒注册，每 chat 仅一次）。

        设计说明：匿名用户（channel_anon:*）不注册 EventBridge 订阅。
        这是有意的限制——匿名用户在 Bot 与 Web 之间无法建立身份关联，
        EventBridge 按 auth_user_id 路由事件，匿名 ID 包含渠道前缀
        (如 ``channel_anon:telegram:12345``)，与 Web 端的匿名 ID 不同，
        事件无法正确路由。用户需通过 /bind 绑定账号后才能获得跨渠道通知。
        """
        if self._event_bridge is None:
            return
        auth_uid = self._resolve_auth_user_id(user_id)
        if auth_uid is None or auth_uid.startswith("channel_anon:"):
            return
        reg_key = f"{self.adapter.name}:{chat_id}:{user_id}"
        prev_uid = self._bridge_registered.get(reg_key)
        if prev_uid == auth_uid:
            return
        # 绑定用户发生变化（如解绑后重绑）时，先移除旧订阅，避免串号推送
        if prev_uid:
            self._event_bridge.unsubscribe(prev_uid, self.adapter.name, chat_id)

        _chat_id = chat_id
        _user_id = user_id

        async def _on_bridge_event(event_type: str, data: dict) -> None:
            """EventBridge 回调：处理跨渠道推送的事件。

            支持事件类型：
            - approval / question：审批/问答卡片推送
            - approval_resolved：审批已被其他渠道处理，清除本地待处理状态
            - chat_started：其他渠道开始聊天通知
            - chat_completed：其他渠道完成聊天通知（含回复摘要）
            """
            try:
                # 过滤自身渠道发出的事件，防止回声
                # 例外：approval_resolved 需要所有渠道处理（清除待处理状态）
                origin = data.get("origin_channel", "")
                if origin == self.adapter.name and event_type != "approval_resolved":
                    return

                origin_label = self._CHANNEL_LABELS.get(origin, origin or "其他渠道")

                if event_type == "approval":
                    await self.adapter.send_approval_card(
                        _chat_id,
                        data.get("approval_id", ""),
                        data.get("approval_tool_name", "unknown"),
                        data.get("risk_level", "yellow"),
                        data.get("args_summary", {}),
                    )
                    pk = self._pending_key(_chat_id, _user_id)
                    self._pending[pk] = PendingInteraction(
                        interaction_type="approval",
                        interaction_id=data.get("approval_id", ""),
                        session_id=data.get("session_id", ""),
                        chat_id=_chat_id,
                    )
                elif event_type == "question":
                    await self.adapter.send_question_card(
                        _chat_id,
                        data.get("id", ""),
                        data.get("header", ""),
                        data.get("text", ""),
                        data.get("options", []),
                    )
                    pk = self._pending_key(_chat_id, _user_id)
                    self._pending[pk] = PendingInteraction(
                        interaction_type="question",
                        interaction_id=data.get("id", ""),
                        session_id=data.get("session_id", ""),
                        chat_id=_chat_id,
                    )
                elif event_type == "approval_resolved":
                    # 审批已被其他渠道处理，清除本地待处理状态
                    pk = self._pending_key(_chat_id, _user_id)
                    pending = self._pending.get(pk)
                    resolved_id = data.get("approval_id", "")
                    if (
                        pending
                        and pending.type == "approval"
                        and (not resolved_id or pending.id == resolved_id)
                    ):
                        self._pending.pop(pk, None)
                        await self.adapter.send_text(
                            _chat_id, f"✅ 审批已由{origin_label}处理",
                        )
                elif event_type == "chat_started":
                    event_sid = data.get("session_id", "")
                    current_sid = self.sessions.get(
                        self.adapter.name, _chat_id, _user_id,
                    )
                    # 仅在本地无活跃会话时自动同步，避免意外切换用户正在使用的会话
                    if event_sid and not current_sid:
                        self.sessions.set(
                            self.adapter.name, _chat_id, _user_id, event_sid,
                        )
                    # 多群聊去重：仅对会话匹配或无会话的群发送通知，
                    # 有不同活跃会话的群跳过（用户在那里做其他事）
                    if current_sid and event_sid and current_sid != event_sid:
                        return
                    preview = data.get("message_preview", "")
                    hint = f"🌐 {origin_label}正在处理: {preview}" if preview else f"🌐 {origin_label}正在处理请求..."
                    await self.adapter.send_text(_chat_id, hint)
                elif event_type == "chat_completed":
                    event_sid = data.get("session_id", "")
                    current_sid = self.sessions.get(
                        self.adapter.name, _chat_id, _user_id,
                    )
                    # 仅在本地无活跃会话时自动同步
                    if event_sid and not current_sid:
                        self.sessions.set(
                            self.adapter.name, _chat_id, _user_id, event_sid,
                        )
                    # 清除残留的待处理交互（审批/问答已随 chat 完成而隐式解决）
                    pk = self._pending_key(_chat_id, _user_id)
                    self._pending.pop(pk, None)

                    # 多群聊去重：同上
                    if current_sid and event_sid and current_sid != event_sid:
                        return

                    reply_summary = data.get("reply_summary", "")
                    tool_count = data.get("tool_count", 0)
                    has_error = data.get("has_error", False)
                    # 构建通知消息
                    parts: list[str] = []
                    if has_error:
                        parts.append(f"🌐 {origin_label}操作出现异常")
                    else:
                        parts.append(f"🌐 {origin_label}已完成操作")
                    if tool_count:
                        parts.append(f"（{tool_count} 个工具调用）")
                    if reply_summary:
                        # 截断过长的回复摘要
                        summary = reply_summary[:200]
                        if len(reply_summary) > 200:
                            summary += "…"
                        parts.append(f"\n{summary}")
                    await self.adapter.send_text(_chat_id, "".join(parts))
            except Exception:
                logger.warning("EventBridge callback error", exc_info=True)

        self._event_bridge.subscribe(auth_uid, self.adapter.name, _chat_id, _on_bridge_event)
        self._bridge_registered[reg_key] = auth_uid

    async def handle_message(self, msg: ChannelMessage) -> None:
        """处理入站消息的统一入口。"""
        self._cleanup_expired_pending()
        self._msg_count += 1
        if self._msg_count % self._STALE_CLEANUP_INTERVAL == 0:
            self._cleanup_stale_state()
        self._ensure_bridge_subscription(msg.chat_id, msg.user.user_id)
        if not self.check_user(msg.user.user_id):
            if self._rate_limiter.check_reject_cooldown(msg.user.user_id):
                await self.adapter.send_text(msg.chat_id, "⛔ 无权限使用此 Bot")
            return

        # 群聊策略检查
        if msg.chat_type in ("group", "channel"):
            if self._check_group_access(msg):
                return

        # 强制绑定检查：未绑定用户只允许执行绑定相关命令
        if self._require_bind and self._bind_manager is not None:
            is_bind_exempt = (
                msg.is_command
                and msg.command.lower() in self._BIND_EXEMPT_COMMANDS
            )
            if not is_bind_exempt:
                auth_uid = self._resolve_auth_user_id(msg.user.user_id)
                if auth_uid is None:
                    await self.adapter.send_text(
                        msg.chat_id,
                        "🔒 此 Bot 要求绑定账号后才能使用。\n"
                        "请使用 /bind 获取绑定码，在 Web 端完成绑定。",
                    )
                    return

        # 速率限制（豁免消息跳过检查）
        if not self._is_exempt(msg):
            action_type = self._classify_action(msg)
            result = self._rate_limiter.check(msg.user.user_id, action_type)
            if not result.allowed:
                await self.adapter.send_text(msg.chat_id, result.message)
                return

        # 回调数据（按钮点击）— 需要锁保护以防与进行中的 chat 竞争
        if msg.callback_data:
            await self._with_user_lock(msg, self._handle_callback)
            return

        # 文件上传 — 按并发模式分发（优先于 images，因为图片文件同时有 files + images）
        if msg.files:
            await self._dispatch_by_concurrency(msg, self._handle_file_upload)
            return

        # 纯图片消息（无文件附件，如 Telegram 压缩照片）— 按并发模式分发
        if msg.images:
            await self._dispatch_by_concurrency(msg, self._handle_image_message)
            return

        # 命令 — 不加锁，确保 /abort /new 等在处理中仍可执行
        if msg.is_command:
            await self._handle_command(msg)
            return

        # 普通文本 — 按并发模式分发
        if msg.text.strip():
            await self._dispatch_by_concurrency(msg, self._handle_text)

    # ── 命令路由 ──

    async def _handle_command(self, msg: ChannelMessage) -> None:
        """路由 /command 到对应处理函数。"""
        cmd = msg.command.lower()
        handlers = {
            "start": self._cmd_start,
            "help": self._cmd_help,
            "new": self._cmd_new,
            "mode": self._cmd_mode,
            "model": self._cmd_model,
            "addmodel": self._cmd_addmodel,
            "delmodel": self._cmd_delmodel,
            "abort": self._cmd_abort,
            "sessions": self._cmd_sessions,
            "history": self._cmd_history,
            "rollback": self._cmd_rollback,
            "undo": self._cmd_undo,
            "quota": self._cmd_quota,
            "concurrency": self._cmd_concurrency,
            "staged": self._cmd_staged,
            "apply": self._cmd_apply,
            "discard": self._cmd_discard,
            "undoapply": self._cmd_undoapply,
            "bind": self._cmd_bind,
            "bindstatus": self._cmd_bindstatus,
            "unbind": self._cmd_unbind,
            "admin": self._cmd_admin,
            "approve": self._cmd_approve,
            "reject": self._cmd_reject,
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
            "  /delmodel <名称> — 删除模型\n"
            "  /quota — 查看 token 用量和配额\n\n"
            "⚙️ 模式切换\n"
            "  /mode — 查看当前模式\n"
            "  /mode <write|read|plan> — 切换对话模式\n\n"
            "📂 会话管理\n"
            "  /sessions — 列出历史会话\n"
            "  /sessions <编号> — 切换到指定会话\n"
            "  /history — 查看当前会话轮次\n"
            "  /rollback [轮次号] — 回退到指定轮次\n"
            "  /undo — 撤销最近操作\n\n"
            "⚡ 并发控制\n"
            "  /concurrency — 查看当前并发模式\n"
            "  /concurrency <queue|steer|guide> — 切换并发模式\n\n"
            "📦 文件管理\n"
            "  /staged — 查看待确认文件\n"
            "  /apply [编号|all] — 确认应用文件变更\n"
            "  /discard [编号|all] — 丢弃文件变更\n"
            "  /undoapply — 撤销最近一次 apply\n\n"
            "🔗 渠道绑定\n"
            "  /bind — 获取绑定码（关联 Web 账号）\n"
            "  /bindstatus — 查看绑定状态\n"
            "  /unbind — 解除绑定\n\n"
            "🔐 管理员\n"
            "  /admin — 查看/管理访问策略\n\n"
            "📄 支持的文件\n"
            "  Excel: .xlsx .xls .csv\n"
            "  图片: .png .jpg .jpeg",
        )

    async def _cmd_new(self, msg: ChannelMessage) -> None:
        pk = self._pending_key(msg.chat_id, msg.user.user_id)
        self.sessions.remove(self.adapter.name, msg.chat_id, msg.user.user_id)
        self._pending.pop(pk, None)
        self._pending_files.pop(pk, None)
        await self.adapter.send_text(msg.chat_id, "🆕 已新建对话，历史已清除。")

    async def _cmd_mode(self, msg: ChannelMessage) -> None:
        args = msg.command_args
        user_id = msg.user.user_id
        current = self.sessions.get_mode(self.adapter.name, msg.chat_id, user_id)

        if not args:
            icon, label, desc = MODE_INFO.get(current, ("", current, ""))
            lines = [f"当前模式: {icon} {label} — {desc}\n"]
            lines.append("可选模式：")
            for key, (mi, ml, md) in MODE_INFO.items():
                marker = " ✅" if key == current else ""
                lines.append(f"  {mi} {key} — {ml}: {md}{marker}")
            lines.append(f"\n切换: /mode <write|read|plan>")
            await self.adapter.send_text(msg.chat_id, "\n".join(lines))
            return

        target = args[0].lower()
        if target not in VALID_MODES:
            await self.adapter.send_text(
                msg.chat_id,
                f"❌ 无效模式: {target}\n可选: write / read / plan",
            )
            return

        if target == current:
            icon, label, _ = MODE_INFO[target]
            await self.adapter.send_text(msg.chat_id, f"{icon} 当前已是{label}模式")
            return

        self.sessions.set_mode(self.adapter.name, msg.chat_id, user_id, target)
        icon, label, desc = MODE_INFO[target]
        await self.adapter.send_text(
            msg.chat_id, f"{icon} 已切换到{label}模式 — {desc}",
        )

    async def _cmd_concurrency(self, msg: ChannelMessage) -> None:
        """查看/切换并发模式: queue / steer / guide。"""
        args = msg.command_args
        user_id = msg.user.user_id
        key = self._pending_key(msg.chat_id, user_id)
        current = self._user_concurrency.get(key, self._default_concurrency)

        if not args:
            icon, label, desc = CONCURRENCY_INFO.get(current, ("", current, ""))
            lines = [f"当前并发模式: {icon} {label} — {desc}\n"]
            lines.append("可选模式：")
            for k, (ci, cl, cd) in CONCURRENCY_INFO.items():
                marker = " ✅" if k == current else ""
                lines.append(f"  {ci} {k} — {cl}: {cd}{marker}")
            lines.append("\n切换: /concurrency <queue|steer|guide>")
            await self.adapter.send_text(msg.chat_id, "\n".join(lines))
            return

        target = args[0].lower()
        if target not in VALID_CONCURRENCY:
            await self.adapter.send_text(
                msg.chat_id,
                f"❌ 无效并发模式: {target}\n可选: queue / steer / guide",
            )
            return

        if target == current:
            icon, label, _ = CONCURRENCY_INFO[target]
            await self.adapter.send_text(msg.chat_id, f"{icon} 当前已是{label}模式")
            return

        self._user_concurrency[key] = target
        icon, label, desc = CONCURRENCY_INFO[target]
        await self.adapter.send_text(
            msg.chat_id, f"{icon} 已切换到{label}并发模式 — {desc}",
        )

    async def _cmd_abort(self, msg: ChannelMessage) -> None:
        session_id = self.sessions.get(self.adapter.name, msg.chat_id, msg.user.user_id)
        if not session_id:
            await self.adapter.send_text(msg.chat_id, "⚠️ 当前没有活跃的会话")
            return
        obo = self._resolve_on_behalf_of(msg.user.user_id)
        try:
            await self.api.abort(session_id, on_behalf_of=obo)
            await self.adapter.send_text(msg.chat_id, "🛑 已终止当前任务")
        except Exception as e:
            await self.adapter.send_text(msg.chat_id, f"❌ 终止失败: {e}")

    async def _cmd_approve(self, msg: ChannelMessage) -> None:
        """通过命令批准审批（QQ 等无 inline keyboard 的平台使用）。"""
        await self._cmd_approval_decision(msg, "approve")

    async def _cmd_reject(self, msg: ChannelMessage) -> None:
        """通过命令拒绝审批（QQ 等无 inline keyboard 的平台使用）。"""
        await self._cmd_approval_decision(msg, "reject")

    async def _cmd_approval_decision(self, msg: ChannelMessage, decision: str) -> None:
        """处理 /approve <id> 或 /reject <id> 命令。"""
        args = msg.command_args
        user_id = msg.user.user_id
        pk = self._pending_key(msg.chat_id, user_id)

        # 无参数时，尝试从 pending 中取 approval_id
        if args:
            approval_id = args[0]
        else:
            pending = self._pending.get(pk)
            if pending and pending.type == "approval":
                approval_id = pending.id
            else:
                await self.adapter.send_text(
                    msg.chat_id,
                    f"用法: /{decision} <approval_id>\n（或在有待处理审批时直接 /{decision}）",
                )
                return

        session_id = self.sessions.get(self.adapter.name, msg.chat_id, user_id)
        if not session_id:
            await self.adapter.send_text(msg.chat_id, "⚠️ 当前没有活跃的会话")
            return

        result_text = "✅ 已批准" if decision == "approve" else "❌ 已拒绝"

        # 清除 pending 状态
        pending = self._pending.pop(pk, None)
        if pending and pending.message_id:
            await self.adapter.update_approval_result(
                msg.chat_id, pending.message_id, result_text,
            )

        try:
            await self.adapter.show_typing(msg.chat_id)
            obo = self._resolve_on_behalf_of(user_id)
            await self.api.approve(session_id, approval_id, decision, on_behalf_of=obo)
            await self.adapter.send_text(msg.chat_id, result_text)
        except Exception as e:
            logger.exception("Approval command error for /%s", decision)
            await self.adapter.send_text(msg.chat_id, f"❌ 处理审批失败: {e}")

    async def _cmd_model(self, msg: ChannelMessage) -> None:
        args = msg.command_args
        obo = self._resolve_on_behalf_of(msg.user.user_id)
        try:
            if args:
                target = " ".join(args)
                await self.api.switch_model(target, on_behalf_of=obo)
                await self.adapter.send_text(msg.chat_id, f"✅ 已切换到模型: {target}")
                return

            models = await self.api.list_models(on_behalf_of=obo)
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
        # S1: 群聊中禁用此命令，避免 API key 明文暴露给其他群成员
        if msg.chat_type in ("group", "channel"):
            await self.adapter.send_text(
                msg.chat_id,
                "🔒 /addmodel 涉及 API Key，请在私聊中使用此命令。",
            )
            return

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

        obo = self._resolve_on_behalf_of(msg.user.user_id)
        try:
            await self.api.add_model(name, model_id, base_url, api_key, description, on_behalf_of=obo)
            await self.adapter.send_text(
                msg.chat_id,
                f"✅ 已添加: {name}\n   {model_id}\n\n切换: /model {name}",
            )
        except Exception as e:
            err_msg = str(e)
            if "403" in err_msg:
                await self.adapter.send_text(msg.chat_id, "⛔ 添加模型需要管理员权限")
            else:
                await self.adapter.send_text(msg.chat_id, f"❌ 添加失败: {e}")

    async def _cmd_delmodel(self, msg: ChannelMessage) -> None:
        args = msg.command_args
        if not args:
            await self.adapter.send_text(msg.chat_id, "用法: /delmodel <模型名称>")
            return
        obo = self._resolve_on_behalf_of(msg.user.user_id)
        try:
            await self.api.delete_model(args[0], on_behalf_of=obo)
            await self.adapter.send_text(msg.chat_id, f"🗑 已删除: {args[0]}")
        except Exception as e:
            err_msg = str(e)
            if "403" in err_msg:
                await self.adapter.send_text(msg.chat_id, "⛔ 删除模型需要管理员权限")
            else:
                await self.adapter.send_text(msg.chat_id, f"❌ 删除失败: {e}")

    async def _cmd_quota(self, msg: ChannelMessage) -> None:
        """查看当前用户的 token 用量和配额。"""
        obo = self._resolve_on_behalf_of(msg.user.user_id)
        try:
            usage = await self.api.get_usage(on_behalf_of=obo)
        except Exception as e:
            err_msg = str(e)
            if "401" in err_msg or "403" in err_msg:
                await self.adapter.send_text(
                    msg.chat_id, "⚠️ 配额查询需要绑定账号，请先 /bind",
                )
            else:
                await self.adapter.send_text(msg.chat_id, f"❌ 获取配额失败: {e}")
            return

        daily_tokens = usage.get("daily_tokens", 0)
        monthly_tokens = usage.get("monthly_tokens", 0)
        daily_limit = usage.get("daily_limit", 0)
        monthly_limit = usage.get("monthly_limit", 0)
        daily_remaining = usage.get("daily_remaining", -1)
        monthly_remaining = usage.get("monthly_remaining", -1)

        lines = ["📊 Token 用量\n"]
        # 日用量
        if daily_limit > 0:
            lines.append(f"  今日: {daily_tokens:,} / {daily_limit:,}")
            if daily_remaining >= 0:
                lines.append(f"  剩余: {daily_remaining:,}")
        else:
            lines.append(f"  今日: {daily_tokens:,}（无上限）")
        # 月用量
        if monthly_limit > 0:
            lines.append(f"  本月: {monthly_tokens:,} / {monthly_limit:,}")
            if monthly_remaining >= 0:
                lines.append(f"  剩余: {monthly_remaining:,}")
        else:
            lines.append(f"  本月: {monthly_tokens:,}（无上限）")

        await self.adapter.send_text(msg.chat_id, "\n".join(lines))

    async def _cmd_sessions(self, msg: ChannelMessage) -> None:
        """列出历史会话 / 切换会话。"""
        args = msg.command_args
        obo = self._resolve_on_behalf_of(msg.user.user_id)
        try:
            sessions = await self.api.list_sessions(on_behalf_of=obo)
        except Exception as e:
            await self.adapter.send_text(msg.chat_id, f"❌ 获取会话列表失败: {e}")
            return

        if not sessions:
            await self.adapter.send_text(msg.chat_id, "暂无历史会话")
            return

        # 带参数 → 切换会话
        if args:
            try:
                idx = int(args[0]) - 1
            except ValueError:
                await self.adapter.send_text(msg.chat_id, "⚠️ 请输入会话编号（数字）")
                return
            if idx < 0 or idx >= len(sessions):
                await self.adapter.send_text(
                    msg.chat_id, f"⚠️ 编号超出范围，有效范围: 1-{len(sessions)}",
                )
                return
            target = sessions[idx]
            sid = target.get("session_id", "")
            self.sessions.set(self.adapter.name, msg.chat_id, msg.user.user_id, sid)
            title = target.get("title", sid[:8])
            await self.adapter.send_text(msg.chat_id, f"✅ 已切换到会话: {title}")
            return

        # 无参数 → 列出
        current_sid = self.sessions.get(self.adapter.name, msg.chat_id, msg.user.user_id)
        lines = ["📂 历史会话：\n"]
        for i, s in enumerate(sessions[:20], 1):
            sid = s.get("session_id", "")
            title = s.get("title") or sid[:8]
            msg_count = s.get("message_count", 0)
            marker = " ← 当前" if sid == current_sid else ""
            lines.append(f"  {i}. {title}  ({msg_count}条){marker}")
        lines.append(f"\n切换: /sessions <编号>")
        await self.adapter.send_text(msg.chat_id, "\n".join(lines))

    async def _cmd_history(self, msg: ChannelMessage) -> None:
        """查看当前会话轮次摘要。"""
        session_id = self.sessions.get(self.adapter.name, msg.chat_id, msg.user.user_id)
        if not session_id:
            await self.adapter.send_text(msg.chat_id, "⚠️ 当前没有活跃的会话")
            return
        obo = self._resolve_on_behalf_of(msg.user.user_id)
        try:
            turns = await self.api.list_turns(session_id, on_behalf_of=obo)
        except Exception as e:
            await self.adapter.send_text(msg.chat_id, f"❌ 获取轮次失败: {e}")
            return
        if not turns:
            await self.adapter.send_text(msg.chat_id, "当前会话暂无对话轮次")
            return

        lines = ["📜 对话轮次：\n"]
        for t in turns:
            idx = t.get("turn_index", "?")
            user_msg = t.get("user_message", "")[:60]
            tool_names = t.get("tool_names", [])
            tools_str = f"  🔧 {', '.join(tool_names)}" if tool_names else ""
            lines.append(f"  [{idx}] {user_msg}{tools_str}")
        lines.append(f"\n回退: /rollback <轮次号>")
        await self.adapter.send_text(msg.chat_id, "\n".join(lines))

    async def _cmd_rollback(self, msg: ChannelMessage) -> None:
        """回退到指定用户轮次。"""
        session_id = self.sessions.get(self.adapter.name, msg.chat_id, msg.user.user_id)
        if not session_id:
            await self.adapter.send_text(msg.chat_id, "⚠️ 当前没有活跃的会话")
            return

        args = msg.command_args
        if not args:
            # 无参数 → 先展示轮次列表
            await self._cmd_history(msg)
            return

        try:
            turn_index = int(args[0])
        except ValueError:
            await self.adapter.send_text(msg.chat_id, "⚠️ 请输入轮次号（数字）")
            return

        try:
            obo = self._resolve_on_behalf_of(msg.user.user_id)
            result = await self.api.rollback(session_id, turn_index, on_behalf_of=obo)
            removed = result.get("removed_messages", 0)
            file_results = result.get("file_rollback_results", [])
            file_count = len(file_results) if isinstance(file_results, list) else 0
            parts = [f"⏪ 已回退到轮次 {turn_index}"]
            if removed:
                parts.append(f"移除 {removed} 条消息")
            if file_count:
                parts.append(f"回滚 {file_count} 个文件")
            await self.adapter.send_text(msg.chat_id, "，".join(parts))
        except Exception as e:
            await self.adapter.send_text(msg.chat_id, f"❌ 回退失败: {e}")

    async def _cmd_undo(self, msg: ChannelMessage) -> None:
        """撤销最近一次可撤销操作。"""
        session_id = self.sessions.get(self.adapter.name, msg.chat_id, msg.user.user_id)
        if not session_id:
            await self.adapter.send_text(msg.chat_id, "⚠️ 当前没有活跃的会话")
            return

        obo = self._resolve_on_behalf_of(msg.user.user_id)
        try:
            operations = await self.api.list_operations(session_id, limit=20, on_behalf_of=obo)
        except Exception as e:
            await self.adapter.send_text(msg.chat_id, f"❌ 获取操作历史失败: {e}")
            return

        # 找最近一条 undoable 操作（后端返回最近在前）
        target = None
        for op in operations:
            if op.get("undoable"):
                target = op
                break

        if target is None:
            await self.adapter.send_text(msg.chat_id, "⚠️ 没有可撤销的操作")
            return

        approval_id = target.get("approval_id", "")
        tool_name = target.get("tool_name", "unknown")
        try:
            result = await self.api.undo_operation(session_id, approval_id, on_behalf_of=obo)
            status = result.get("status", "")
            result_msg = result.get("message", "")
            if status == "ok":
                await self.adapter.send_text(
                    msg.chat_id, f"↩️ 已撤销操作: {tool_name}\n{result_msg}",
                )
            else:
                await self.adapter.send_text(
                    msg.chat_id, f"⚠️ 撤销失败: {result_msg}",
                )
        except Exception as e:
            await self.adapter.send_text(msg.chat_id, f"❌ 撤销失败: {e}")

    # ── Staged 文件管理命令 ──

    async def _cmd_staged(self, msg: ChannelMessage) -> None:
        """查看待确认的 staged 文件列表。"""
        session_id = self.sessions.get(self.adapter.name, msg.chat_id, msg.user.user_id)
        if not session_id:
            await self.adapter.send_text(msg.chat_id, "⚠️ 当前没有活跃的会话")
            return
        obo = self._resolve_on_behalf_of(msg.user.user_id)
        try:
            data = await self.api.list_staged(session_id, on_behalf_of=obo)
        except Exception as e:
            await self.adapter.send_text(msg.chat_id, f"❌ 获取 staged 文件失败: {e}")
            return

        if not data.get("backup_enabled"):
            await self.adapter.send_text(msg.chat_id, "ℹ️ 当前会话未启用备份模式")
            return

        files = data.get("files", [])
        if not files:
            await self.adapter.send_text(msg.chat_id, "📂 暂无待确认文件")
            return

        # 缓存文件列表供 /apply /discard 按索引引用
        pk = self._pending_key(msg.chat_id, msg.user.user_id)
        self._staged_cache[pk] = files

        await self.adapter.send_staged_card(
            msg.chat_id, files, len(files), session_id,
        )

    def _resolve_staged_files(
        self, msg: ChannelMessage, args: list[str],
    ) -> list[str] | None:
        """根据命令参数解析要操作的 staged 文件路径列表。

        返回 None 表示操作全部，返回空列表表示参数无效。
        """
        if not args or args[0].lower() == "all":
            return None  # 操作全部

        pk = self._pending_key(msg.chat_id, msg.user.user_id)
        cached = self._staged_cache.get(pk, [])

        try:
            idx = int(args[0]) - 1
        except ValueError:
            return []  # 无效参数

        if idx < 0 or idx >= len(cached):
            return []  # 超出范围

        original_path = cached[idx].get("original_path", "")
        return [original_path] if original_path else []

    async def _cmd_apply(self, msg: ChannelMessage) -> None:
        """确认应用 staged 文件变更。"""
        session_id = self.sessions.get(self.adapter.name, msg.chat_id, msg.user.user_id)
        if not session_id:
            await self.adapter.send_text(msg.chat_id, "⚠️ 当前没有活跃的会话")
            return

        files = self._resolve_staged_files(msg, msg.command_args)
        if files is not None and not files:
            pk = self._pending_key(msg.chat_id, msg.user.user_id)
            cached = self._staged_cache.get(pk, [])
            if not cached:
                await self.adapter.send_text(
                    msg.chat_id,
                    "⚠️ 请先使用 /staged 查看文件列表，再按编号操作",
                )
            else:
                await self.adapter.send_text(
                    msg.chat_id,
                    f"⚠️ 无效编号，有效范围: 1-{len(cached)}",
                )
            return

        try:
            obo = self._resolve_on_behalf_of(msg.user.user_id)
            result = await self.api.apply_staged(session_id, files, on_behalf_of=obo)
        except Exception as e:
            err_msg = str(e)
            if "409" in err_msg:
                await self.adapter.send_text(
                    msg.chat_id, "⏳ 会话正在处理中，请等待完成后再应用",
                )
            else:
                await self.adapter.send_text(msg.chat_id, f"❌ 应用失败: {e}")
            return

        if result.get("status") != "ok":
            await self.adapter.send_text(
                msg.chat_id, f"⚠️ 应用失败: {result.get('message', '未知错误')}",
            )
            return

        count = result.get("count", 0)
        pending = result.get("pending_count", 0)
        applied = result.get("applied", [])

        # 缓存 undo 信息
        pk = self._pending_key(msg.chat_id, msg.user.user_id)
        self._save_apply_undo(pk, applied)
        await self._send_apply_result(msg.chat_id, count, pending, bool(self._last_apply.get(pk)))
        self._staged_cache.pop(pk, None)

    async def _cmd_discard(self, msg: ChannelMessage) -> None:
        """丢弃 staged 文件变更。"""
        session_id = self.sessions.get(self.adapter.name, msg.chat_id, msg.user.user_id)
        if not session_id:
            await self.adapter.send_text(msg.chat_id, "⚠️ 当前没有活跃的会话")
            return

        files = self._resolve_staged_files(msg, msg.command_args)
        if files is not None and not files:
            pk = self._pending_key(msg.chat_id, msg.user.user_id)
            cached = self._staged_cache.get(pk, [])
            if not cached:
                await self.adapter.send_text(
                    msg.chat_id,
                    "⚠️ 请先使用 /staged 查看文件列表，再按编号操作",
                )
            else:
                await self.adapter.send_text(
                    msg.chat_id,
                    f"⚠️ 无效编号，有效范围: 1-{len(cached)}",
                )
            return

        try:
            obo = self._resolve_on_behalf_of(msg.user.user_id)
            result = await self.api.discard_staged(session_id, files, on_behalf_of=obo)
        except Exception as e:
            err_msg = str(e)
            if "409" in err_msg:
                await self.adapter.send_text(
                    msg.chat_id, "⏳ 会话正在处理中，请等待完成后再丢弃",
                )
            else:
                await self.adapter.send_text(msg.chat_id, f"❌ 丢弃失败: {e}")
            return

        discarded = result.get("discarded", 0)
        pending = result.get("pending_count", 0)
        await self._send_discard_result(msg.chat_id, discarded, pending)
        pk = self._pending_key(msg.chat_id, msg.user.user_id)
        self._staged_cache.pop(pk, None)

    # ── Staged 共享辅助方法 ──

    def _save_apply_undo(self, pk: str, applied: list[dict]) -> None:
        """从 apply 响应中提取 undo 信息并缓存。"""
        undo_items = [
            {"original_path": a.get("original", ""), "undo_path": a.get("undo_path", "")}
            for a in applied if a.get("undo_path")
        ]
        if undo_items:
            self._last_apply[pk] = undo_items

    async def _send_apply_result(
        self, chat_id: str, count: int, pending: int, has_undo: bool,
    ) -> None:
        """发送 apply 操作结果消息。"""
        parts = [f"✅ 已应用 {count} 个文件"]
        if pending > 0:
            parts.append(f"剩余 {pending} 个待确认")
        if has_undo:
            parts.append("如需撤销，请发送 /undoapply")
        await self.adapter.send_text(chat_id, "\n".join(parts))

    async def _send_discard_result(
        self, chat_id: str, discarded: int | str, pending: int,
    ) -> None:
        """发送 discard 操作结果消息。"""
        parts = [f"🗑 已丢弃{'全部' if discarded == 'all' else f' {discarded} 个'}文件"]
        if pending > 0:
            parts.append(f"剩余 {pending} 个待确认")
        await self.adapter.send_text(chat_id, "\n".join(parts))

    async def _cmd_undoapply(self, msg: ChannelMessage) -> None:
        """撤销最近一次 apply 操作。"""
        session_id = self.sessions.get(self.adapter.name, msg.chat_id, msg.user.user_id)
        if not session_id:
            await self.adapter.send_text(msg.chat_id, "⚠️ 当前没有活跃的会话")
            return

        pk = self._pending_key(msg.chat_id, msg.user.user_id)
        undo_items = self._last_apply.pop(pk, [])
        if not undo_items:
            await self.adapter.send_text(msg.chat_id, "⚠️ 没有可撤销的 apply 操作")
            return

        restored = 0
        failed: list[str] = []
        for item in undo_items:
            try:
                obo = self._resolve_on_behalf_of(msg.user.user_id)
                result = await self.api.undo_backup(
                    session_id,
                    item["original_path"],
                    item["undo_path"],
                    on_behalf_of=obo,
                )
                if result.get("status") == "ok":
                    restored += 1
                else:
                    failed.append(item["original_path"])
            except Exception:
                failed.append(item["original_path"])

        parts: list[str] = []
        if restored:
            parts.append(f"↩️ 已撤销 {restored} 个文件的 apply")
        if failed:
            parts.append(f"⚠️ {len(failed)} 个文件撤销失败: {', '.join(failed)}")
        await self.adapter.send_text(msg.chat_id, "\n".join(parts) or "⚠️ 撤销失败")

    # ── 文本消息 ──

    # P4b: 根据 chat_mode 分化的处理提示
    _PROCESSING_HINTS: dict[str, str] = {
        "write": "✏️ 收到！正在处理写入请求...",
        "read":  "🔍 收到！正在分析中...",
        "plan":  "📋 收到！正在制定计划...",
    }
    _PROCESSING_HINT_DEFAULT = "✨ ExcelManus 正在处理中，请稍等..."
    _PROCESSING_HINT_FILE = "📎 文件已收到，正在处理..."

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
                obo = self._resolve_on_behalf_of(user_id)
                await self.api.answer_question(session_id, question_id, msg.text, on_behalf_of=obo)
            except Exception as e:
                logger.exception("Free-text answer error for user %s", user_id)
                await self.adapter.send_text(msg.chat_id, f"❌ 处理回答失败: {e}")
            return

        # 拦截：若有缓冲的待处理文件，将文本作为用户指令，合并处理
        if pk in self._pending_files:
            await self._process_pending_files(msg.chat_id, user_id, msg.text)
            return

        chat_mode = self.sessions.get_mode(self.adapter.name, msg.chat_id, user_id)
        # P4b/P1b: 延迟处理提示 — 快速响应（<2s）时不发送，避免刷屏
        hint = self._PROCESSING_HINTS.get(chat_mode, self._PROCESSING_HINT_DEFAULT)
        hint_task = asyncio.create_task(
            self._delayed_hint(msg.chat_id, hint, delay=2.0),
        )
        session_id = self.sessions.get(self.adapter.name, msg.chat_id, user_id)
        try:
            result = await self._stream_chat_chunked(
                msg.chat_id, user_id, msg.text, session_id, chat_mode=chat_mode,
            )
            await self._dispatch_non_text_results(msg.chat_id, user_id, result)
        except Exception as e:
            logger.exception("Chat error for user %s", user_id)
            await self.adapter.send_text(msg.chat_id, f"❌ 出错了: {type(e).__name__}: {e}")
        finally:
            hint_task.cancel()

    # ── 图片消息 ──

    # mime → 扩展名映射（纯压缩照片无原始文件名，需推断）
    _MIME_TO_EXT: dict[str, str] = {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/gif": ".gif",
        "image/webp": ".webp",
        "image/bmp": ".bmp",
        "image/tiff": ".tiff",
    }

    async def _handle_image_message(self, msg: ChannelMessage) -> None:
        """处理纯图片消息 → 上传到工作区 → 缓冲等待用户指令或立即处理。

        若消息携带文本（如 Telegram 照片 caption），立即处理。
        否则缓冲图片数据，等待用户下一条文本指令。
        """
        user_id = msg.user.user_id
        pk = self._pending_key(msg.chat_id, user_id)
        obo = self._resolve_on_behalf_of(user_id)

        for img in msg.images:
            ext = self._MIME_TO_EXT.get(img.media_type, ".jpg")
            photo_filename = f"photo_{_uuid.uuid4().hex[:6]}{ext}"

            # 上传到工作区，使 agent 文件工具可操作；失败时降级为仅 vision
            ws_path = ""
            try:
                await self.adapter.show_typing(msg.chat_id)
                ws_path = await self.api.upload_to_workspace(
                    photo_filename, img.data, on_behalf_of=obo,
                )
            except Exception:
                logger.warning("照片上传到工作区失败，降级为仅 vision 模式", exc_info=True)

            pending = PendingFile(
                filename=photo_filename,
                is_image=True,
                mime_type=img.media_type,
                image_data=img.data,
                workspace_path=ws_path,
            )
            self._pending_files.setdefault(pk, []).append(pending)

        # 有文本 → 立即处理
        if msg.text.strip():
            await self._process_pending_files(msg.chat_id, user_id, msg.text)
            return

        # 无文本 → 等待用户指令
        count = len(msg.images)
        if count == 1:
            prompt = "📷 已收到图片，请问你想让我怎么处理？"
        else:
            prompt = f"📷 已收到 {count} 张图片，请问你想让我怎么处理？"
        await self.adapter.send_text(msg.chat_id, prompt)

    # ── 文件上传 ──

    _IMAGE_MIME_PREFIX = "image/"

    async def _handle_file_upload(self, msg: ChannelMessage) -> None:
        """处理文件上传 → 写入工作区 → 缓冲等待用户指令。

        文件先上传到工作区，然后缓冲文件元信息等待用户下一条文本指令。
        若消息本身携带文本（如 Telegram 文件 caption），则视为已有指令，立即处理。
        """
        user_id = msg.user.user_id
        pk = self._pending_key(msg.chat_id, user_id)

        uploaded_names: list[str] = []
        for file_att in msg.files:
            ext = Path(file_att.filename).suffix.lower()
            if ext not in ALLOWED_UPLOAD_EXTENSIONS:
                await self.adapter.send_text(
                    msg.chat_id,
                    f"⚠️ 不支持的文件类型: {ext}\n仅支持 Excel (.xlsx/.xls/.csv) 和图片文件",
                )
                continue

            await self.adapter.show_typing(msg.chat_id)
            obo = self._resolve_on_behalf_of(user_id)
            ws_path = await self.api.upload_to_workspace(file_att.filename, file_att.data, on_behalf_of=obo)

            is_image = file_att.mime_type.startswith(self._IMAGE_MIME_PREFIX)
            pending = PendingFile(
                filename=file_att.filename,
                is_image=is_image,
                mime_type=file_att.mime_type,
                image_data=file_att.data if is_image else None,
                workspace_path=ws_path,
            )
            self._pending_files.setdefault(pk, []).append(pending)
            uploaded_names.append(file_att.filename)

        if not uploaded_names:
            return

        # 消息本身有文本 → 视为用户已给出指令，立即处理
        if msg.text.strip():
            await self._process_pending_files(msg.chat_id, user_id, msg.text)
            return

        # 无文本 → 告知用户已收到文件，等待进一步指令
        if len(uploaded_names) == 1:
            prompt = f"📎 已收到文件 {uploaded_names[0]}，请问你想让我怎么处理？"
        else:
            names = "、".join(uploaded_names)
            prompt = f"📎 已收到 {len(uploaded_names)} 个文件（{names}），请问你想让我怎么处理？"
        await self.adapter.send_text(msg.chat_id, prompt)

    async def _process_pending_files(
        self, chat_id: str, user_id: str, text: str,
    ) -> None:
        """消费缓冲的待处理文件，结合用户文本指令一起发送给 AI。"""
        pk = self._pending_key(chat_id, user_id)
        files = self._pending_files.pop(pk, [])
        if not files:
            return

        await self.adapter.show_typing(chat_id)
        await self.adapter.send_text(chat_id, self._PROCESSING_HINT_FILE)

        # 构建消息：@file:引用 + 用户文本
        file_refs = [f for f in files if f.workspace_path]
        if file_refs:
            refs_str = " ".join(
                f"@file:{f.workspace_path}" if f.workspace_path else f"@file:{f.filename}"
                for f in file_refs
            )
            chat_msg = f"{refs_str} {text}"
        else:
            chat_msg = text

        # 收集 vision 图片（文件中的图片 + 纯图片）
        api_images: list[dict[str, str]] = []
        for f in files:
            if f.is_image and f.image_data:
                b64 = base64.b64encode(f.image_data).decode("ascii")
                api_images.append({
                    "data": b64,
                    "media_type": f.mime_type,
                    "detail": "auto",
                })

        session_id = self.sessions.get(self.adapter.name, chat_id, user_id)
        chat_mode = self.sessions.get_mode(self.adapter.name, chat_id, user_id)
        try:
            result = await self._stream_chat_chunked(
                chat_id, user_id, chat_msg, session_id,
                chat_mode=chat_mode, images=api_images or None,
            )
            await self._dispatch_non_text_results(chat_id, user_id, result)
        except Exception as e:
            logger.exception("Pending file chat error for user %s", user_id)
            await self.adapter.send_text(chat_id, f"❌ 出错了: {e}")

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

        if data.startswith("apply_staged:") or data.startswith("discard_staged:"):
            await self._handle_staged_callback(msg, data)
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
            obo = self._resolve_on_behalf_of(user_id)
            await self.api.approve(session_id, approval_id, decision, on_behalf_of=obo)
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
            obo = self._resolve_on_behalf_of(msg.user.user_id)
            await self.api.answer_question(session_id, question_id, answer, on_behalf_of=obo)
        except Exception as e:
            logger.exception("Question callback error")
            await self.adapter.send_text(msg.chat_id, f"❌ 处理回答失败: {e}")

    async def _handle_staged_callback(self, msg: ChannelMessage, data: str) -> None:
        """处理 staged 文件 apply/discard 按钮回调。"""
        parts = data.split(":")
        if len(parts) < 3:
            return
        action = parts[0]  # "apply_staged" | "discard_staged"
        session_id = parts[1]
        file_index_str = parts[2]

        if file_index_str == "all":
            files_param = None
        else:
            pk = self._pending_key(msg.chat_id, msg.user.user_id)
            cached = self._staged_cache.get(pk, [])
            try:
                idx = int(file_index_str)
            except ValueError:
                return
            if idx < 0 or idx >= len(cached):
                await self.adapter.send_text(msg.chat_id, "⚠️ 文件索引已过期，请重新 /staged")
                return
            original_path = cached[idx].get("original_path", "")
            if not original_path:
                return
            files_param = [original_path]

        pk = self._pending_key(msg.chat_id, msg.user.user_id)
        obo = self._resolve_on_behalf_of(msg.user.user_id)
        try:
            if action == "apply_staged":
                result = await self.api.apply_staged(session_id, files_param, on_behalf_of=obo)
                count = result.get("count", 0)
                pending = result.get("pending_count", 0)
                applied = result.get("applied", [])
                self._save_apply_undo(pk, applied)
                self._staged_cache.pop(pk, None)
                await self._send_apply_result(
                    msg.chat_id, count, pending, bool(self._last_apply.get(pk)),
                )
            else:
                result = await self.api.discard_staged(session_id, files_param, on_behalf_of=obo)
                discarded = result.get("discarded", 0)
                pending = result.get("pending_count", 0)
                self._staged_cache.pop(pk, None)
                await self._send_discard_result(msg.chat_id, discarded, pending)
        except Exception as e:
            err_msg = str(e)
            if "409" in err_msg:
                await self.adapter.send_text(
                    msg.chat_id, "⏳ 会话正在处理中，请等待完成后操作",
                )
            else:
                await self.adapter.send_text(msg.chat_id, f"❌ 操作失败: {e}")

    # ── 延迟处理提示 ──

    async def _delayed_hint(self, chat_id: str, hint: str, delay: float = 2.0) -> None:
        """延迟发送处理提示。快速响应（<delay 秒）完成时 task 被 cancel，不发送。"""
        try:
            await asyncio.sleep(delay)
            await self.adapter.send_text(chat_id, hint)
        except asyncio.CancelledError:
            pass

    # ── 流式分块输出 ──

    async def _stream_chat_chunked(
        self,
        chat_id: str,
        user_id: str,
        message: str,
        session_id: str | None,
        *,
        chat_mode: str = "write",
        images: list[dict[str, str]] | None = None,
    ) -> dict:
        """通过 ChunkedOutputManager 流式处理聊天并实时推送到渠道。

        替代原来的 stream_chat() + _send_chat_result() 流程，
        文本回复已由 ChunkedOutputManager 实时推送，
        返回的 dict 仅含非文本结果（审批/问答/文件等）。
        """
        # 解析 on_behalf_of：已绑定→真实 user_id；未绑定→匿名隔离 ID
        auth_uid = self._resolve_on_behalf_of(user_id)

        manager = ChunkedOutputManager(self.adapter, chat_id)
        manager.start_heartbeat()
        async for event_type, data in self.api.stream_chat_events(
            message, session_id, chat_mode=chat_mode, images=images,
            on_behalf_of=auth_uid, channel=self.adapter.name,
        ):
            await manager.feed(event_type, data)
        result = await manager.finalize()

        # 更新 session_id
        new_session_id = result.get("session_id", "")
        if new_session_id:
            self.sessions.set(self.adapter.name, chat_id, user_id, new_session_id)

        return result

    # ── 结果分发 ──

    async def _dispatch_non_text_results(
        self, chat_id: str, user_id: str, result: dict,
    ) -> None:
        """分发非文本结果：文件下载、审批、问答、staging。

        文本回复已由 ChunkedOutputManager 处理，此方法处理其余结构化结果。
        """
        file_downloads = result.get("file_downloads", [])
        approval = result.get("approval")
        question = result.get("question")
        staging_event = result.get("staging_event")
        reply = result.get("reply", "")
        error = result.get("error")

        # 流式输出中途出错：已有部分文本输出但后端报错，补充通知用户
        if error and reply:
            await self.adapter.send_text(chat_id, f"⚠️ 处理未完成: {error}")
        # P5b: 错误后操作引导
        if error and not approval and not question:
            await self.adapter.send_text(
                chat_id,
                "💡 可尝试: /abort 终止 → /undo 撤销 → 重新发送请求",
            )

        # 文件下载（通过 API 获取字节，支持 Bot 与 API 不同机部署）
        for dl in file_downloads:
            file_path = dl.get("file_path", "")
            filename = dl.get("filename", "") or Path(file_path).name
            if not file_path:
                continue

            obo = self._resolve_on_behalf_of(user_id)

            # 1) 预生成下载链接（即使直接发送成功也可能需要）
            download_url: str | None = None
            try:
                download_url = await self.api.generate_download_link(
                    file_path, user_id=user_id, on_behalf_of=obo,
                )
            except Exception:
                logger.debug("生成下载链接失败: %s", file_path, exc_info=True)

            # 2) 尝试直接发送文件
            file_sent = False
            try:
                file_bytes, _ = await self.api.download_file(file_path, on_behalf_of=obo)
                await self.adapter.send_file(chat_id, file_bytes, filename)
                file_sent = True
            except Exception:
                logger.warning("发送文件失败: %s", file_path, exc_info=True)

            # 3) 未成功发送文件 → 发送下载链接文本
            if not file_sent:
                if download_url:
                    await self.adapter.send_text(
                        chat_id,
                        f"📎 文件已生成: {filename}\n🔗 下载链接（30 分钟有效）:\n{download_url}",
                    )
                else:
                    await self.adapter.send_text(
                        chat_id,
                        f"📎 文件已生成: {filename}\n请通过 Web 界面下载。",
                    )

        # 审批请求
        if approval:
            approval_id = approval.get("approval_id", "")
            tool_name = approval.get("approval_tool_name", "unknown")
            risk_level = approval.get("risk_level", "yellow")
            args_summary = approval.get("args_summary", {})

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
        if question:
            question_id = question.get("id", "")
            header = question.get("header", "")
            text = question.get("text", "")
            options = question.get("options", [])

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

        # staging 自动通知
        if staging_event:
            evt = staging_event
            action = evt.get("action", "")
            staging_files = evt.get("files", [])
            pending_count = evt.get("pending_count", 0)
            if action in ("new", "finish_hint") and staging_files and pending_count > 0:
                session_id = self.sessions.get(self.adapter.name, chat_id, user_id) or ""
                # 缓存文件列表
                pk = self._pending_key(chat_id, user_id)
                try:
                    obo = self._resolve_on_behalf_of(user_id)
                    data = await self.api.list_staged(session_id, on_behalf_of=obo)
                    full_files = data.get("files", [])
                    if full_files:
                        self._staged_cache[pk] = full_files
                        await self.adapter.send_staged_card(
                            chat_id, full_files, len(full_files), session_id,
                        )
                except Exception:
                    # 降级：简要文本通知
                    from pathlib import PurePosixPath
                    names = [PurePosixPath(f.get("path", "?")).name for f in staging_files[:5]]
                    lines = [f"📦 {pending_count} 个文件待确认:"]
                    for n in names:
                        lines.append(f"  • {n}")
                    lines.append("输入 /staged 查看详情，/apply 确认应用")
                    await self.adapter.send_text(chat_id, "\n".join(lines))

        # P5c: 无任何内容 — 语义化提示
        if (
            not reply
            and not approval
            and not question
            and not file_downloads
            and not staging_event
        ):
            tool_calls = result.get("tool_calls", [])
            if tool_calls:
                await self.adapter.send_text(chat_id, "✅ 操作已完成")
            else:
                await self.adapter.send_text(chat_id, "（未获得回复内容，请重试或 /abort 后再试）")

    # ── 身份桥接 ──

    # 匿名用户 ID 前缀（用于未绑定渠道用户的工作区隔离）
    _ANON_PREFIX = "channel_anon:"

    def _resolve_auth_user_id(self, platform_user_id: str) -> str | None:
        """解析渠道用户对应的 auth user_id。

        带 TTL 缓存：命中且未过期时直接返回（跳过 DB 查询）。
        bind/unbind 通过 invalidate_auth_cache() 主动失效，保证即时生效。
        """
        cache_key = f"{self.adapter.name}:{platform_user_id}"
        now = time.monotonic()
        cached = self._auth_user_cache.get(cache_key)

        # 缓存命中且未过期 → 直接返回
        if cached is not None:
            cached_uid, cached_ts = cached
            if (now - cached_ts) < self._AUTH_CACHE_TTL:
                return cached_uid

        # 缓存未命中或已过期 → 回源查 DB
        if self._bind_manager is None:
            return None
        auth_uid: str | None = self._bind_manager.check_bind_status(
            self.adapter.name, platform_user_id,
        )

        # 未绑定 → 清理缓存
        if auth_uid is None:
            self._auth_user_cache.pop(cache_key, None)
            return None

        # 写入/更新缓存
        prev_uid = cached[0] if cached else None
        self._auth_user_cache[cache_key] = (auth_uid, now)
        # 绑定关系变化时回填 session store
        if prev_uid != auth_uid:
            self.sessions.backfill_auth_user_id(
                self.adapter.name, platform_user_id, auth_uid,
            )
        return auth_uid

    def _resolve_on_behalf_of(self, platform_user_id: str) -> str | None:
        """解析 on_behalf_of header 值。

        已绑定用户 → 真实 auth user_id
        未绑定用户 → 合成匿名 ID ``channel_anon:<channel>:<platform_id>``
                      使后端为其分配隔离工作区
        """
        auth_uid = self._resolve_auth_user_id(platform_user_id)
        if auth_uid is not None:
            return auth_uid
        # 未绑定 → 合成匿名 ID，确保工作区隔离
        return f"{self._ANON_PREFIX}{self.adapter.name}:{platform_user_id}"

    def invalidate_auth_cache(self, platform_user_id: str) -> None:
        """绑定/解绑后清除缓存。"""
        cache_key = f"{self.adapter.name}:{platform_user_id}"
        self._auth_user_cache.pop(cache_key, None)

    # ── 绑定命令 ──

    async def _cmd_bind(self, msg: ChannelMessage) -> None:
        """生成绑定码，用户在 Web 前端输入后完成渠道绑定。"""
        if self._bind_manager is None:
            await self.adapter.send_text(
                msg.chat_id, "⚠️ 绑定功能未启用（认证未开启）",
            )
            return

        # 已绑定检查
        existing = self._resolve_auth_user_id(msg.user.user_id)
        if existing:
            await self.adapter.send_text(
                msg.chat_id,
                "✅ 已绑定 ExcelManus 用户，如需解绑请使用 /unbind",
            )
            return

        try:
            code = self._bind_manager.create_bind_code(
                channel=self.adapter.name,
                platform_id=msg.user.user_id,
                platform_display_name=msg.user.display_name or msg.user.username,
            )
        except RuntimeError as e:
            await self.adapter.send_text(msg.chat_id, f"❌ {e}")
            return

        await self.adapter.send_markdown(
            msg.chat_id,
            f"🔗 绑定码: <b>{code}</b>\n"
            f"请在 Web 端「个人中心 → 渠道绑定」中输入此码。\n"
            f"有效期 5 分钟，过期请重新 /bind",
        )

    async def _cmd_bindstatus(self, msg: ChannelMessage) -> None:
        """查询当前渠道账号的绑定状态。"""
        if self._bind_manager is None:
            await self.adapter.send_text(msg.chat_id, "⚠️ 绑定功能未启用")
            return

        auth_uid = self._resolve_auth_user_id(msg.user.user_id)
        if auth_uid:
            await self.adapter.send_text(
                msg.chat_id,
                f"✅ 已绑定 ExcelManus 用户（ID: {auth_uid[:8]}…）\n"
                f"解绑: /unbind",
            )
        else:
            await self.adapter.send_text(
                msg.chat_id,
                "❌ 未绑定 ExcelManus 用户，使用 /bind 获取绑定码",
            )

    async def _cmd_unbind(self, msg: ChannelMessage) -> None:
        """解除当前渠道账号的绑定。"""
        if self._bind_manager is None:
            await self.adapter.send_text(msg.chat_id, "⚠️ 绑定功能未启用")
            return

        auth_uid = self._resolve_auth_user_id(msg.user.user_id)
        if not auth_uid:
            await self.adapter.send_text(
                msg.chat_id, "❌ 当前渠道账号未绑定，无需解绑",
            )
            return

        ok = self._bind_manager.unbind_channel(
            self.adapter.name, msg.user.user_id,
        )
        self.invalidate_auth_cache(msg.user.user_id)
        if ok:
            if self._event_bridge is not None:
                self._event_bridge.unsubscribe(auth_uid, self.adapter.name, msg.chat_id)
            reg_key = f"{self.adapter.name}:{msg.chat_id}:{msg.user.user_id}"
            self._bridge_registered.pop(reg_key, None)
            await self.adapter.send_text(
                msg.chat_id, "✅ 已解绑，后续消息将以匿名身份处理。重新绑定: /bind",
            )
        else:
            await self.adapter.send_text(msg.chat_id, "❌ 解绑失败，请稍后重试")

    # ── 管理员命令 ──

    async def _cmd_admin(self, msg: ChannelMessage) -> None:
        """管理员命令入口。"""
        if not self._is_admin(msg.user.user_id):
            await self.adapter.send_text(msg.chat_id, "⛔ 此命令需要管理员权限")
            return

        args = msg.command_args
        if not args:
            await self._admin_status(msg)
            return

        sub = args[0].lower()
        sub_args = args[1:]

        sub_handlers: dict[str, Any] = {
            "group": self._admin_group,
            "allowgroup": self._admin_allowgroup,
            "blockgroup": self._admin_blockgroup,
            "removegroup": self._admin_removegroup,
            "listgroups": self._admin_listgroups,
            "adduser": self._admin_adduser,
            "removeuser": self._admin_removeuser,
            "listusers": self._admin_listusers,
            "addadmin": self._admin_addadmin,
            "removeadmin": self._admin_removeadmin,
        }
        handler_fn = sub_handlers.get(sub)
        if handler_fn:
            await handler_fn(msg, sub_args)
        else:
            await self.adapter.send_text(
                msg.chat_id,
                "❓ 未知子命令。可用:\n"
                "  /admin — 查看状态\n"
                "  /admin group <deny|allow|whitelist|blacklist>\n"
                "  /admin allowgroup — 白名单当前群\n"
                "  /admin blockgroup — 黑名单当前群\n"
                "  /admin removegroup [chat_id]\n"
                "  /admin listgroups\n"
                "  /admin adduser <user_id>\n"
                "  /admin removeuser <user_id>\n"
                "  /admin listusers\n"
                "  /admin addadmin <user_id>\n"
                "  /admin removeadmin <user_id>",
            )

    async def _admin_status(self, msg: ChannelMessage) -> None:
        """显示当前管理状态汇总。"""
        policy = self._group_policy
        wl = self._group_whitelist
        bl = self._group_blacklist
        admins = self._admin_users
        dynamic_users = self._dynamic_allowed_users
        static_users = self.allowed_users

        lines = ["🔐 Bot 管理状态\n"]
        lines.append(f"群聊策略: {policy}")
        lines.append(f"白名单群: {len(wl)} 个")
        lines.append(f"黑名单群: {len(bl)} 个")
        lines.append(f"管理员: {', '.join(sorted(admins)) if admins else '(未设置)'}")
        total_users = len(static_users | dynamic_users) if static_users or dynamic_users else 0
        lines.append(f"允许用户: {'不限制' if not static_users and not dynamic_users else f'{total_users} 个'}")
        lines.append(f"强制绑定: {'开启' if self._require_bind else '关闭'}")
        lines.append(f"\n当前 chat_id: {msg.chat_id}")
        lines.append(f"当前 chat_type: {msg.chat_type}")
        await self.adapter.send_text(msg.chat_id, "\n".join(lines))

    async def _admin_group(self, msg: ChannelMessage, args: list[str]) -> None:
        """设置群聊策略。"""
        if not args:
            await self.adapter.send_text(
                msg.chat_id,
                f"当前群聊策略: {self._group_policy}\n"
                "用法: /admin group <deny|allow|whitelist|blacklist>",
            )
            return
        target = args[0].lower()
        if target not in ("deny", "allow", "whitelist", "blacklist"):
            await self.adapter.send_text(
                msg.chat_id,
                f"❌ 无效策略: {target}\n可选: deny / allow / whitelist / blacklist",
            )
            return
        if self._config_store is None:
            await self.adapter.send_text(msg.chat_id, "❌ 配置存储不可用")
            return
        self._config_store.set("channel_group_policy", target)
        await self.adapter.send_text(msg.chat_id, f"✅ 群聊策略已设为: {target}")

    async def _admin_allowgroup(self, msg: ChannelMessage, args: list[str]) -> None:
        """将当前群或指定 chat_id 加入白名单。"""
        chat_id = args[0] if args else msg.chat_id
        if self._config_store is None:
            await self.adapter.send_text(msg.chat_id, "❌ 配置存储不可用")
            return
        wl = self._group_whitelist
        if chat_id in wl:
            await self.adapter.send_text(msg.chat_id, f"ℹ️ {chat_id} 已在白名单中")
            return
        wl.add(chat_id)
        self._config_store.set("channel_group_whitelist", json.dumps(sorted(wl)))
        await self.adapter.send_text(msg.chat_id, f"✅ 已将 {chat_id} 加入白名单")

    async def _admin_blockgroup(self, msg: ChannelMessage, args: list[str]) -> None:
        """将当前群或指定 chat_id 加入黑名单。"""
        chat_id = args[0] if args else msg.chat_id
        if self._config_store is None:
            await self.adapter.send_text(msg.chat_id, "❌ 配置存储不可用")
            return
        bl = self._group_blacklist
        if chat_id in bl:
            await self.adapter.send_text(msg.chat_id, f"ℹ️ {chat_id} 已在黑名单中")
            return
        bl.add(chat_id)
        self._config_store.set("channel_group_blacklist", json.dumps(sorted(bl)))
        await self.adapter.send_text(msg.chat_id, f"✅ 已将 {chat_id} 加入黑名单")

    async def _admin_removegroup(self, msg: ChannelMessage, args: list[str]) -> None:
        """从白名单和黑名单中移除群。"""
        chat_id = args[0] if args else msg.chat_id
        if self._config_store is None:
            await self.adapter.send_text(msg.chat_id, "❌ 配置存储不可用")
            return
        removed = False
        wl = self._group_whitelist
        if chat_id in wl:
            wl.discard(chat_id)
            self._config_store.set("channel_group_whitelist", json.dumps(sorted(wl)))
            removed = True
        bl = self._group_blacklist
        if chat_id in bl:
            bl.discard(chat_id)
            self._config_store.set("channel_group_blacklist", json.dumps(sorted(bl)))
            removed = True
        if removed:
            await self.adapter.send_text(msg.chat_id, f"✅ 已将 {chat_id} 从白/黑名单中移除")
        else:
            await self.adapter.send_text(msg.chat_id, f"ℹ️ {chat_id} 不在任何名单中")

    async def _admin_listgroups(self, msg: ChannelMessage, args: list[str]) -> None:
        """列出白名单和黑名单群。"""
        wl = self._group_whitelist
        bl = self._group_blacklist
        lines = ["📋 群名单\n"]
        if wl:
            lines.append("白名单:")
            for cid in sorted(wl):
                lines.append(f"  ✅ {cid}")
        else:
            lines.append("白名单: (空)")
        if bl:
            lines.append("\n黑名单:")
            for cid in sorted(bl):
                lines.append(f"  🚫 {cid}")
        else:
            lines.append("\n黑名单: (空)")
        await self.adapter.send_text(msg.chat_id, "\n".join(lines))

    async def _admin_adduser(self, msg: ChannelMessage, args: list[str]) -> None:
        """添加允许用户。"""
        if not args:
            await self.adapter.send_text(msg.chat_id, "用法: /admin adduser <user_id>")
            return
        if self._config_store is None:
            await self.adapter.send_text(msg.chat_id, "❌ 配置存储不可用")
            return
        uid = args[0].strip()
        users = self._dynamic_allowed_users
        if uid in users:
            await self.adapter.send_text(msg.chat_id, f"ℹ️ {uid} 已在允许用户列表中")
            return
        users.add(uid)
        self._config_store.set("channel_allowed_users", json.dumps(sorted(users)))
        await self.adapter.send_text(msg.chat_id, f"✅ 已添加允许用户: {uid}")

    async def _admin_removeuser(self, msg: ChannelMessage, args: list[str]) -> None:
        """移除允许用户。"""
        if not args:
            await self.adapter.send_text(msg.chat_id, "用法: /admin removeuser <user_id>")
            return
        if self._config_store is None:
            await self.adapter.send_text(msg.chat_id, "❌ 配置存储不可用")
            return
        uid = args[0].strip()
        users = self._dynamic_allowed_users
        if uid not in users:
            await self.adapter.send_text(msg.chat_id, f"ℹ️ {uid} 不在动态允许用户列表中")
            return
        users.discard(uid)
        self._config_store.set("channel_allowed_users", json.dumps(sorted(users)))
        await self.adapter.send_text(msg.chat_id, f"✅ 已移除允许用户: {uid}")

    async def _admin_listusers(self, msg: ChannelMessage, args: list[str]) -> None:
        """列出允许用户。"""
        static = self.allowed_users
        dynamic = self._dynamic_allowed_users
        lines = ["📋 允许用户列表\n"]
        if not static and not dynamic:
            lines.append("(未设置限制，所有用户可用)")
        else:
            if static:
                lines.append("启动配置:")
                for uid in sorted(static):
                    lines.append(f"  {uid}")
            if dynamic:
                lines.append("\n动态添加:")
                for uid in sorted(dynamic):
                    lines.append(f"  {uid}")
        await self.adapter.send_text(msg.chat_id, "\n".join(lines))

    async def _admin_addadmin(self, msg: ChannelMessage, args: list[str]) -> None:
        """添加管理员。"""
        if not args:
            await self.adapter.send_text(msg.chat_id, "用法: /admin addadmin <user_id>")
            return
        if self._config_store is None:
            await self.adapter.send_text(msg.chat_id, "❌ 配置存储不可用")
            return
        uid = args[0].strip()
        # 读取现有 config_kv 管理员（不含环境变量管理员）
        try:
            raw = self._config_store.get("channel_admin_users", "")
            existing = set(u.strip() for u in raw.split(",") if u.strip()) if raw else set()
        except Exception:
            existing = set()
        if uid in existing:
            await self.adapter.send_text(msg.chat_id, f"ℹ️ {uid} 已是管理员")
            return
        existing.add(uid)
        self._config_store.set("channel_admin_users", ",".join(sorted(existing)))
        await self.adapter.send_text(msg.chat_id, f"✅ 已添加管理员: {uid}")

    async def _admin_removeadmin(self, msg: ChannelMessage, args: list[str]) -> None:
        """移除管理员（仅可移除 config_kv 中的，环境变量管理员不可移除）。"""
        if not args:
            await self.adapter.send_text(msg.chat_id, "用法: /admin removeadmin <user_id>")
            return
        if self._config_store is None:
            await self.adapter.send_text(msg.chat_id, "❌ 配置存储不可用")
            return
        uid = args[0].strip()
        # 检查是否为环境变量管理员
        env_admins = set()
        env_val = os.environ.get("EXCELMANUS_CHANNEL_ADMINS", "").strip()
        if env_val:
            env_admins = set(u.strip() for u in env_val.split(",") if u.strip())
        if uid in env_admins:
            await self.adapter.send_text(
                msg.chat_id, f"⛔ {uid} 是环境变量管理员，不可通过命令移除",
            )
            return
        try:
            raw = self._config_store.get("channel_admin_users", "")
            existing = set(u.strip() for u in raw.split(",") if u.strip()) if raw else set()
        except Exception:
            existing = set()
        if uid not in existing:
            await self.adapter.send_text(msg.chat_id, f"ℹ️ {uid} 不在动态管理员列表中")
            return
        existing.discard(uid)
        self._config_store.set("channel_admin_users", ",".join(sorted(existing)))
        await self.adapter.send_text(msg.chat_id, f"✅ 已移除管理员: {uid}")
