"""渠道协同启动器：在 API 进程内可选启动渠道 Bot。

支持三种配置方式（优先级从高到低）：
  1. create_app(channels=["qq"]) 编程参数
  2. EXCELMANUS_CHANNELS=qq,telegram 环境变量
  3. 前端热配置（通过 ChannelConfigStore 持久化，运行时动态启停）

渠道 Bot 仍通过 HTTP 调用本地 API（ExcelManusAPIClient），
架构与独立进程模式完全一致，仅省去手动启动第二个终端。
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

logger = logging.getLogger("excelmanus.channels.launcher")

# 渠道名 → 启动函数的延迟映射（避免在无对应依赖时 import 报错）
_CHANNEL_BUILDERS: dict[str, str] = {
    "telegram": "excelmanus.channels.telegram.handlers:build_telegram_app",
    "qq": "excelmanus.channels.qq.handlers:build_qq_app",
    "feishu": "excelmanus.channels.feishu.handlers:build_feishu_handler",
}

# 渠道名 → (pip 包名, import 模块名) 的依赖映射
_CHANNEL_DEPENDENCIES: dict[str, tuple[str, str]] = {
    "telegram": ("python-telegram-bot", "telegram"),
    "qq": ("qq-botpy", "botpy"),
    "feishu": ("lark-oapi", "lark_oapi"),
}


def parse_channels_config(
    channels: list[str] | None = None,
) -> list[str]:
    """解析要启动的渠道列表。

    优先使用显式传入的 channels 参数；
    否则从 EXCELMANUS_CHANNELS 环境变量读取（逗号分隔）。
    返回去重、规范化后的渠道名列表。
    """
    if channels:
        names = channels
    else:
        raw = os.environ.get("EXCELMANUS_CHANNELS", "").strip()
        if not raw:
            return []
        names = [n.strip().lower() for n in raw.split(",") if n.strip()]

    # 去重保序
    seen: set[str] = set()
    result: list[str] = []
    for name in names:
        normalized = name.strip().lower()
        if normalized and normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return result


class ChannelLauncher:
    """管理多个渠道 Bot 的异步生命周期。

    用法::

        launcher = ChannelLauncher(["qq"], api_port=8000)
        await launcher.start()   # 在 lifespan startup 中调用
        ...
        await launcher.stop()    # 在 lifespan shutdown 中调用
    """

    def __init__(
        self,
        channels: list[str],
        *,
        api_port: int = 8000,
        bind_manager: Any | None = None,
        service_token: str | None = None,
        event_bridge: Any | None = None,
        config_store: Any | None = None,
    ) -> None:
        self._channel_names = channels
        self._api_port = api_port
        self._bind_manager = bind_manager
        self._service_token = service_token
        self._event_bridge = event_bridge
        self._config_store = config_store
        self._tasks: dict[str, asyncio.Task] = {}
        self._apps: dict[str, Any] = {}
        self._handlers: dict[str, Any] = {}  # 渠道名 → MessageHandler 实例

    @staticmethod
    def check_dependency(name: str) -> tuple[bool, str]:
        """检查渠道的 Python 依赖是否已安装。

        Returns:
            (installed, install_hint) — installed=True 表示可用，
            install_hint 为安装命令（仅在 installed=False 时有意义）。
        """
        dep = _CHANNEL_DEPENDENCIES.get(name)
        if dep is None:
            return True, ""
        pip_pkg, import_name = dep
        try:
            import importlib
            importlib.import_module(import_name)
            return True, ""
        except ImportError:
            return False, f"pip install {pip_pkg}"

    @property
    def active_channels(self) -> list[str]:
        """当前活跃的渠道名列表。"""
        return [name for name, task in self._tasks.items() if not task.done()]

    def channel_status(self, name: str) -> str:
        """查询单个渠道状态: running / stopped / error / unknown。"""
        task = self._tasks.get(name)
        if task is None:
            return "stopped"
        if not task.done():
            return "running"
        exc = task.exception() if not task.cancelled() else None
        if exc is not None:
            return "error"
        return "stopped"

    def all_channel_status(self) -> dict[str, str]:
        """返回所有已知渠道的状态。"""
        known = set(list(_CHANNEL_BUILDERS.keys()) + list(self._tasks.keys()))
        return {name: self.channel_status(name) for name in sorted(known)}

    async def start_channel(
        self,
        name: str,
        credentials: dict[str, str] | None = None,
    ) -> tuple[bool, str]:
        """热启动单个渠道 Bot。

        Args:
            name: 渠道名 (telegram / qq)
            credentials: 运行时凭证，覆盖环境变量

        Returns:
            (success, message)
        """
        if name not in _CHANNEL_BUILDERS:
            return False, f"未知渠道: {name}。可用: {', '.join(sorted(_CHANNEL_BUILDERS.keys()))}"

        # 先停止已有实例
        if name in self._tasks and not self._tasks[name].done():
            await self.stop_channel(name)

        api_url = f"http://127.0.0.1:{self._api_port}"
        try:
            task = asyncio.create_task(
                self._run_channel(name, api_url, credentials=credentials),
                name=f"channel-{name}",
            )
            self._tasks[name] = task
            # 等待短暂时间检测是否立即失败
            await asyncio.sleep(0.5)
            if task.done():
                exc = task.exception() if not task.cancelled() else None
                msg = str(exc) if exc else "启动后立即退出"
                return False, f"渠道 {name} 启动失败: {msg}"
            logger.info("渠道 %s 已热启动", name)
            return True, f"渠道 {name} 已启动"
        except Exception as e:
            logger.error("渠道 %s 热启动失败", name, exc_info=True)
            return False, f"启动异常: {e}"

    async def stop_channel(self, name: str) -> tuple[bool, str]:
        """热停止单个渠道 Bot。

        Returns:
            (success, message)
        """
        task = self._tasks.get(name)
        if task is None:
            return False, f"渠道 {name} 未启动"
        if task.done():
            self._tasks.pop(name, None)
            self._apps.pop(name, None)
            return True, f"渠道 {name} 已停止（之前已退出）"

        task.cancel()
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=10)
        except (asyncio.CancelledError, asyncio.TimeoutError, Exception):
            pass
        self._tasks.pop(name, None)
        self._apps.pop(name, None)
        self._handlers.pop(name, None)
        logger.info("渠道 %s 已热停止", name)
        return True, f"渠道 {name} 已停止"

    @staticmethod
    def _on_channel_task_done(task: asyncio.Task) -> None:
        """Done-callback: 记录渠道任务异常，防止 'Task exception was never retrieved'。"""
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.error("渠道任务 %s 异常退出: %s", task.get_name(), exc)

    async def start(self) -> None:
        """启动所有配置的渠道 Bot。"""
        if not self._channel_names:
            return

        api_url = f"http://127.0.0.1:{self._api_port}"

        for name in self._channel_names:
            if name not in _CHANNEL_BUILDERS:
                logger.warning(
                    "未知渠道 %r，跳过。可用渠道: %s",
                    name, ", ".join(sorted(_CHANNEL_BUILDERS.keys())),
                )
                continue

            try:
                task = asyncio.create_task(
                    self._run_channel(name, api_url),
                    name=f"channel-{name}",
                )
                task.add_done_callback(ChannelLauncher._on_channel_task_done)
                self._tasks[name] = task
                logger.info("渠道 %s 已启动（协同模式）", name)
            except Exception:
                logger.error("渠道 %s 启动失败", name, exc_info=True)

        if self._tasks:
            logger.info(
                "渠道协同启动完成: %s",
                ", ".join(sorted(self._tasks.keys())),
            )

    async def send_notification(
        self,
        channel: str,
        platform_id: str,
        text: str,
    ) -> bool:
        """向渠道用户主动发送通知消息（HTML 格式）。

        Args:
            channel: 渠道名 (telegram / qq)
            platform_id: 平台用户 ID（Telegram chat_id 等）
            text: 消息文本（支持 HTML 标签如 <b>bold</b>）

        Returns:
            True 发送成功，False 渠道未运行或发送失败。
        """
        app = self._apps.get(channel)
        if app is None:
            logger.debug("渠道 %s 未运行，无法发送通知", channel)
            return False

        try:
            if channel == "telegram":
                await app.bot.send_message(
                    chat_id=int(platform_id),
                    text=text,
                    parse_mode="HTML",
                )
                return True
            if channel == "feishu":
                await app.send_text(str(platform_id), text)
                return True
            logger.debug("渠道 %s 暂不支持主动通知", channel)
            return False
        except Exception:
            logger.warning("渠道 %s 发送通知失败: platform_id=%s", channel, platform_id, exc_info=True)
            return False

    async def stop(self) -> None:
        """优雅停止所有渠道 Bot。"""
        for name, task in self._tasks.items():
            if not task.done():
                task.cancel()
                logger.info("正在停止渠道 %s...", name)

        if self._tasks:
            await asyncio.gather(
                *self._tasks.values(), return_exceptions=True,
            )
            logger.info("所有渠道已停止")

        self._tasks.clear()
        self._apps.clear()
        self._handlers.clear()

    async def _run_channel(
        self,
        name: str,
        api_url: str,
        credentials: dict[str, str] | None = None,
    ) -> None:
        """在 asyncio task 中运行单个渠道 Bot。"""
        builder_path = _CHANNEL_BUILDERS[name]
        module_path, func_name = builder_path.rsplit(":", 1)

        try:
            import importlib
            mod = importlib.import_module(module_path)
            builder = getattr(mod, func_name)
        except ImportError as exc:
            pip_pkg, _ = _CHANNEL_DEPENDENCIES.get(name, (name, name))
            hint = f"pip install {pip_pkg}"
            msg = (
                f"渠道 {name} 的 Python 依赖未安装（{exc}）。"
                f"请运行: {hint}"
            )
            logger.error(msg)
            raise RuntimeError(msg) from exc
        except AttributeError:
            msg = f"渠道 {name} 构建函数 {builder_path} 不存在"
            logger.error(msg)
            raise RuntimeError(msg)

        if name == "telegram":
            await self._run_telegram(builder, api_url, credentials=credentials)
        elif name == "qq":
            await self._run_qq(builder, api_url, credentials=credentials)
        elif name == "feishu":
            await self._run_feishu(builder, api_url, credentials=credentials)
        else:
            logger.warning("渠道 %s 的协同启动尚未实现", name)

    async def _wait_api_ready(self, api_url: str, timeout: float = 30) -> bool:
        """等待本地 API 就绪（health 端点可达）。"""
        import httpx

        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        health_url = f"{api_url}/api/v1/health"
        while loop.time() < deadline:
            try:
                async with httpx.AsyncClient(timeout=3) as client:
                    resp = await client.get(health_url)
                    if resp.status_code == 200:
                        return True
            except Exception:
                pass
            await asyncio.sleep(0.5)
        logger.warning("等待 API 就绪超时（%ss），渠道 Bot 仍将尝试启动", timeout)
        return False

    async def _run_telegram(
        self,
        builder: Any,
        api_url: str,
        credentials: dict[str, str] | None = None,
    ) -> None:
        """运行 Telegram Bot（非阻塞 polling 模式）。"""
        creds = credentials or {}
        token = creds.get("token") or os.environ.get("EXCELMANUS_TG_TOKEN", "")
        if not token:
            raise ValueError(
                "未提供 Telegram Bot Token。请在设置中填写并保存 Token，或设置 EXCELMANUS_TG_TOKEN 环境变量。"
            )

        allowed_users: set[str] | None = None
        raw = creds.get("allowed_users") or os.environ.get("EXCELMANUS_TG_USERS", "")
        if raw.strip():
            allowed_users = {uid.strip() for uid in raw.split(",") if uid.strip()}

        from excelmanus.channels.rate_limit import RateLimitConfig
        rate_limit_config = RateLimitConfig.from_store(self._config_store)

        tg_app, adapter, handler = builder(
            token=token,
            api_url=api_url,
            allowed_users=allowed_users,
            rate_limit_config=rate_limit_config,
            bind_manager=self._bind_manager,
            service_token=self._service_token,
            event_bridge=self._event_bridge,
            config_store=self._config_store,
        )
        self._apps["telegram"] = tg_app
        self._handlers["telegram"] = handler

        logger.info("Telegram Bot 启动中（协同模式）...")
        logger.info("  API: %s", api_url)
        if allowed_users:
            logger.info("  允许的用户: %s", allowed_users)
        else:
            logger.info("  ⚠️ 未设置用户限制，所有人可用")

        # 等待本地 API 就绪后再开始接收消息
        await self._wait_api_ready(api_url)

        # 使用 python-telegram-bot 的异步 start/updater 而非阻塞 run_polling
        _initialized = False
        _started = False
        _polling = False
        try:
            await tg_app.initialize()
            _initialized = True
            await tg_app.start()
            _started = True
            await tg_app.updater.start_polling(
                drop_pending_updates=True,
                bootstrap_retries=-1,
            )
            _polling = True

            logger.info("Telegram Bot 已就绪（协同模式）")

            # 保持运行直到被 cancel
            try:
                while True:
                    await asyncio.sleep(3600)
            except asyncio.CancelledError:
                pass
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.error("Telegram Bot 运行异常", exc_info=True)
            raise
        finally:
            logger.info("Telegram Bot 正在停止...")
            try:
                if _polling:
                    await tg_app.updater.stop()
                if _started:
                    await tg_app.stop()
                if _initialized:
                    await tg_app.shutdown()
            except Exception:
                logger.debug("Telegram Bot 清理异常", exc_info=True)

    async def _run_qq(
        self,
        builder: Any,
        api_url: str,
        credentials: dict[str, str] | None = None,
    ) -> None:
        """运行 QQ Bot（非阻塞异步模式）。"""
        creds = credentials or {}
        app_id = creds.get("app_id") or os.environ.get("EXCELMANUS_QQ_APPID", "")
        secret = creds.get("secret") or os.environ.get("EXCELMANUS_QQ_SECRET", "")
        if not app_id or not secret:
            missing = []
            if not app_id:
                missing.append("app_id")
            if not secret:
                missing.append("secret")
            raise ValueError(
                f"未提供 QQ Bot 凭证（{', '.join(missing)}）。请在设置中填写并保存，或设置对应环境变量。"
            )

        allowed_users: set[str] | None = None
        raw = creds.get("allowed_users") or os.environ.get("EXCELMANUS_QQ_USERS", "")
        if raw.strip():
            allowed_users = {uid.strip() for uid in raw.split(",") if uid.strip()}

        sandbox_val = creds.get("sandbox", "") or os.environ.get("EXCELMANUS_QQ_SANDBOX", "")
        is_sandbox = sandbox_val.lower() in ("true", "1", "yes")

        from excelmanus.channels.rate_limit import RateLimitConfig
        rate_limit_config = RateLimitConfig.from_store(self._config_store)

        # 抑制 botpy 对 root logger 的污染（避免日志重复输出）
        _root = logging.getLogger()
        _root_handlers_before = list(_root.handlers)

        qq_client, adapter, handler = builder(
            app_id=app_id,
            secret=secret,
            api_url=api_url,
            allowed_users=allowed_users,
            rate_limit_config=rate_limit_config,
            bind_manager=self._bind_manager,
            service_token=self._service_token,
            is_sandbox=is_sandbox,
            config_store=self._config_store,
        )
        # 移除 botpy 添加的 root handler，防止日志重复输出
        for _h in _root.handlers:
            if _h not in _root_handlers_before:
                _root.removeHandler(_h)

        self._apps["qq"] = qq_client
        self._handlers["qq"] = handler

        logger.info("QQ Bot 启动中（协同模式）...")
        logger.info("  API: %s", api_url)
        logger.info("  AppID: %s", app_id)
        if is_sandbox:
            logger.info("  沙盒模式: 已启用")
        if allowed_users:
            logger.info("  允许的用户: %s", allowed_users)
        else:
            logger.info("  ⚠️ 未设置用户限制，所有人可用")

        # 等待本地 API 就绪后再开始接收消息
        await self._wait_api_ready(api_url)

        # botpy Client 通过 async with 管理生命周期（__aexit__ 会调用 close）
        # start() 阻塞直到 WebSocket 断开，CancelledError 终止运行
        try:
            async with qq_client:
                await qq_client.start(appid=app_id, secret=secret)
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.error("QQ Bot 运行异常", exc_info=True)
        finally:
            logger.info("QQ Bot 正在停止...")
            try:
                if not qq_client.is_closed():
                    await qq_client.close()
            except Exception:
                logger.debug("QQ Bot 清理异常", exc_info=True)

    async def _run_feishu(
        self,
        builder: Any,
        api_url: str,
        credentials: dict[str, str] | None = None,
    ) -> None:
        """运行飞书 Bot（Webhook 模式）。

        飞书使用 Webhook 回调，不需要像 Telegram 那样主动 polling。
        此方法初始化 adapter 和 handler，注册到 launcher 中供 webhook 路由使用，
        然后保持运行直到被 cancel。
        """
        creds = credentials or {}
        app_id = creds.get("app_id") or os.environ.get("EXCELMANUS_FEISHU_APP_ID", "")
        app_secret = creds.get("app_secret") or os.environ.get("EXCELMANUS_FEISHU_APP_SECRET", "")
        if not app_id or not app_secret:
            missing = []
            if not app_id:
                missing.append("app_id")
            if not app_secret:
                missing.append("app_secret")
            raise ValueError(
                f"未提供飞书 Bot 凭证（{', '.join(missing)}）。请在设置中填写并保存，或设置对应环境变量。"
            )

        from excelmanus.channels.rate_limit import RateLimitConfig
        rate_limit_config = RateLimitConfig.from_store(self._config_store)

        adapter, handler = builder(
            app_id=app_id,
            app_secret=app_secret,
            api_url=api_url,
            rate_limit_config=rate_limit_config,
            bind_manager=self._bind_manager,
            service_token=self._service_token,
            event_bridge=self._event_bridge,
            config_store=self._config_store,
        )
        # 存储 adapter 到 _apps（供 send_notification 和 webhook 路由使用）
        self._apps["feishu"] = adapter
        self._handlers["feishu"] = handler

        logger.info("飞书 Bot 启动中（协同模式 - Webhook）...")
        logger.info("  API: %s", api_url)
        logger.info("  AppID: %s", app_id)

        # 启动 adapter（初始化 lark Client）
        await adapter.start()

        # 等待本地 API 就绪
        await self._wait_api_ready(api_url)

        logger.info("飞书 Bot 已就绪（Webhook 模式，等待飞书回调）")

        # 保持运行直到被 cancel
        try:
            while True:
                await asyncio.sleep(3600)
        except asyncio.CancelledError:
            pass
        finally:
            logger.info("飞书 Bot 正在停止...")
            try:
                await adapter.stop()
            except Exception:
                logger.debug("飞书 Bot 清理异常", exc_info=True)
