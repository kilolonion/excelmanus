"""统一渠道接口：抽象多平台 Bot 接入层。

支持 Telegram / QQ Bot / 飞书等多渠道统一接入 ExcelManus API。
渠道可独立进程运行，也可通过 ChannelLauncher 协同 API 进程启动。
"""

from excelmanus.channels.base import (
    ChannelAdapter,
    ChannelMessage,
    ChannelUser,
    FileAttachment,
)
from excelmanus.channels.launcher import ChannelLauncher, parse_channels_config
from excelmanus.channels.registry import ChannelRegistry

__all__ = [
    "ChannelAdapter",
    "ChannelMessage",
    "ChannelUser",
    "FileAttachment",
    "ChannelLauncher",
    "ChannelRegistry",
    "parse_channels_config",
]
