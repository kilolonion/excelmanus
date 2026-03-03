"""渠道抽象基类：定义所有平台适配器的统一接口。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class FileAttachment:
    """归一化的文件附件。"""

    filename: str
    data: bytes
    mime_type: str = "application/octet-stream"


@dataclass
class ChannelUser:
    """归一化的渠道用户。"""

    user_id: str          # 平台用户 ID（统一 str）
    username: str = ""    # 用户名 / 昵称
    display_name: str = ""


@dataclass
class ChannelMessage:
    """归一化的入站消息。"""

    channel: str                          # "telegram" | "qq" | "feishu"
    user: ChannelUser
    chat_id: str                          # 会话/群 ID
    text: str = ""                        # 消息正文
    files: list[FileAttachment] = field(default_factory=list)
    callback_data: str | None = None      # 按钮回调数据（inline keyboard 等）
    is_command: bool = False              # 是否为 /command 格式
    command: str = ""                     # 解析后的命令名（不含 /）
    command_args: list[str] = field(default_factory=list)  # 命令参数
    raw: Any = None                       # 原始平台消息对象（供适配器内部使用）


class ChannelAdapter(ABC):
    """渠道适配器抽象基类。

    每个平台（Telegram / QQ / 飞书）实现此接口，
    由 MessageHandler 统一调度。
    """

    name: str = "unknown"

    # ── 生命周期 ──

    @abstractmethod
    async def start(self) -> None:
        """启动渠道适配器（连接、轮询、webhook 等）。"""

    @abstractmethod
    async def stop(self) -> None:
        """停止渠道适配器，释放资源。"""

    # ── 发送能力 ──

    @abstractmethod
    async def send_text(self, chat_id: str, text: str) -> None:
        """发送纯文本消息。实现方应自行处理消息长度拆分。"""

    @abstractmethod
    async def send_markdown(self, chat_id: str, text: str) -> None:
        """发送 Markdown 格式消息。不支持时降级为纯文本。"""

    @abstractmethod
    async def send_file(self, chat_id: str, file_path: str, filename: str) -> None:
        """发送文件给用户。"""

    @abstractmethod
    async def send_approval_card(
        self,
        chat_id: str,
        approval_id: str,
        tool_name: str,
        risk_level: str,
        args_summary: dict[str, str],
    ) -> None:
        """发送审批卡片（带批准/拒绝按钮）。"""

    @abstractmethod
    async def send_question_card(
        self,
        chat_id: str,
        question_id: str,
        header: str,
        text: str,
        options: list[dict[str, str]],
    ) -> None:
        """发送问答卡片（带选项按钮 + 自由回复提示）。"""

    @abstractmethod
    async def show_typing(self, chat_id: str) -> None:
        """显示"正在输入"指示器。"""

    async def send_progress(self, chat_id: str, stage: str, message: str) -> None:
        """发送进度提示。默认实现为发送文本。"""
        await self.send_text(chat_id, f"⏳ [{stage}] {message}")

    async def update_approval_result(
        self, chat_id: str, message_id: str, result_text: str,
    ) -> None:
        """更新审批卡片为已处理状态。可选实现。"""

    async def update_question_result(
        self, chat_id: str, message_id: str, answer_text: str,
    ) -> None:
        """更新问答卡片为已回答状态。可选实现。"""

    # ── 工具方法 ──

    @staticmethod
    def split_message(text: str, max_len: int = 4000) -> list[str]:
        """将长文本按 max_len 拆分，优先在换行处断开。"""
        if len(text) <= max_len:
            return [text]
        parts: list[str] = []
        while text:
            if len(text) <= max_len:
                parts.append(text)
                break
            idx = text.rfind("\n", 0, max_len)
            if idx < max_len // 2:
                idx = max_len
            parts.append(text[:idx])
            text = text[idx:].lstrip("\n")
        return parts
