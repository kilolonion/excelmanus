"""渠道协同启动器：在 API 进程内可选启动渠道 Bot。

支持两种配置方式（优先级从高到低）：
  1. create_app(channels=["telegram"]) 编程参数
  2. EXCELMANUS_CHANNELS=telegram,qq 环境变量

渠道 Bot 仍通过 HTTP 调用本地 API（ExcelManusAPIClient），
架构与独立进程模式完全一致，仅省去手动启动第二个终端。
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

logger = logging.getLogger("channels.launcher")

# 渠道名 → 启动函数的延迟映射（避免在无对应依赖时 import 报错）
_CHANNEL_BUILDERS: dict[str, str] = {
    "telegram": "excelmanus.channels.telegram.handlers:build_telegram_app",
    # "qq": "excelmanus.channels.qq.handlers:build_qq_app",
    # "feishu": "excelmanus.channels.feishu.handlers:build_feishu_app",
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

        launcher = ChannelLauncher(["telegram"], api_port=8000)
        await launcher.start()   # 在 lifespan startup 中调用
        ...
        await launcher.stop()    # 在 lifespan shutdown 中调用
    """

    def __init__(
        self,
        channels: list[str],
        *,
        api_port: int = 8000,
    ) -> None:
        self._channel_names = channels
        self._api_port = api_port
        self._tasks: dict[str, asyncio.Task] = {}
        self._apps: dict[str, Any] = {}

    @property
    def active_channels(self) -> list[str]:
        """当前活跃的渠道名列表。"""
        return [name for name, task in self._tasks.items() if not task.done()]

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
                self._tasks[name] = task
                logger.info("渠道 %s 已启动（协同模式）", name)
            except Exception:
                logger.error("渠道 %s 启动失败", name, exc_info=True)

        if self._tasks:
            logger.info(
                "渠道协同启动完成: %s",
                ", ".join(sorted(self._tasks.keys())),
            )

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

    async def _run_channel(self, name: str, api_url: str) -> None:
        """在 asyncio task 中运行单个渠道 Bot。"""
        builder_path = _CHANNEL_BUILDERS[name]
        module_path, func_name = builder_path.rsplit(":", 1)

        try:
            import importlib
            mod = importlib.import_module(module_path)
            builder = getattr(mod, func_name)
        except ImportError as exc:
            logger.error(
                "渠道 %s 依赖未安装: %s。请运行: pip install excelmanus[%s]",
                name, exc, name,
            )
            return
        except AttributeError:
            logger.error("渠道 %s 构建函数 %s 不存在", name, builder_path)
            return

        if name == "telegram":
            await self._run_telegram(builder, api_url)
        else:
            logger.warning("渠道 %s 的协同启动尚未实现", name)

    async def _wait_api_ready(self, api_url: str, timeout: float = 30) -> bool:
        """等待本地 API 就绪（health 端点可达）。"""
        import httpx

        deadline = asyncio.get_event_loop().time() + timeout
        health_url = f"{api_url}/api/v1/health"
        while asyncio.get_event_loop().time() < deadline:
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

    async def _run_telegram(self, builder: Any, api_url: str) -> None:
        """运行 Telegram Bot（非阻塞 polling 模式）。"""
        token = os.environ.get("EXCELMANUS_TG_TOKEN", "")
        if not token:
            logger.error(
                "Telegram Bot 未启动：EXCELMANUS_TG_TOKEN 环境变量未设置"
            )
            return

        allowed_users: set[str] | None = None
        raw = os.environ.get("EXCELMANUS_TG_USERS", "")
        if raw.strip():
            allowed_users = {uid.strip() for uid in raw.split(",") if uid.strip()}

        tg_app, adapter, handler = builder(
            token=token,
            api_url=api_url,
            allowed_users=allowed_users,
        )
        self._apps["telegram"] = tg_app

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
            await tg_app.updater.start_polling(drop_pending_updates=True)
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
