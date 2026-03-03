"""ExcelManus Telegram Bot — 通过 Telegram 与 ExcelManus API 交互。

用法：
  EXCELMANUS_TG_TOKEN=xxx python3 excelmanus_tg_bot.py

环境变量：
  EXCELMANUS_TG_TOKEN   — Telegram Bot Token（必填）
  EXCELMANUS_API_URL    — ExcelManus API 地址（默认 http://localhost:8000）
  EXCELMANUS_TG_USERS   — 允许使用的 Telegram user ID，逗号分隔（留空=不限制）

本文件为薄入口层，核心逻辑已迁移至 excelmanus.channels 统一渠道框架。
"""

import logging
import os

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


def main() -> None:
    token = os.environ.get("EXCELMANUS_TG_TOKEN", "")
    if not token:
        print("❌ 请设置 EXCELMANUS_TG_TOKEN 环境变量")
        return

    api_url = os.environ.get("EXCELMANUS_API_URL", "http://localhost:8000")
    allowed_users: set[str] | None = None
    _raw = os.environ.get("EXCELMANUS_TG_USERS", "")
    if _raw.strip():
        allowed_users = {uid.strip() for uid in _raw.split(",") if uid.strip()}

    from excelmanus.channels.telegram.handlers import run_telegram_bot

    run_telegram_bot(
        token=token,
        api_url=api_url,
        allowed_users=allowed_users,
    )


if __name__ == "__main__":
    main()
