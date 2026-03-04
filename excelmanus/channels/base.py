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
class ImageAttachment:
    """归一化的图片附件（用于 vision API）。"""

    data: bytes
    media_type: str = "image/jpeg"
    detail: str = "auto"  # "auto" | "low" | "high"


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
    images: list[ImageAttachment] = field(default_factory=list)
    callback_data: str | None = None      # 按钮回调数据（inline keyboard 等）
    is_command: bool = False              # 是否为 /command 格式
    command: str = ""                     # 解析后的命令名（不含 /）
    command_args: list[str] = field(default_factory=list)  # 命令参数
    raw: Any = None                       # 原始平台消息对象（供适配器内部使用）
    chat_type: str = "private"            # "private" | "group" | "channel"


@dataclass
class ChannelCapabilities:
    """平台能力声明，供 ChunkedOutputManager 查询以选择输出策略。"""

    supports_edit: bool = False          # 是否支持编辑已发送的消息
    supports_card: bool = False          # 是否支持结构化卡片消息
    supports_card_update: bool = False   # 是否支持卡片内容更新（飞书）
    supports_reply_chain: bool = False   # 是否支持 reply_to 消息串
    supports_typing: bool = False        # 是否支持输入中指示
    supports_markdown_tables: bool = True  # 是否渲染 Markdown 表格（Telegram/QQ=False）
    max_message_length: int = 4000       # 消息字符上限
    max_edits_per_minute: int = 0        # 编辑速率限制（0=不支持）
    preferred_format: str = "markdown"   # "markdown" | "html" | "plain"
    passive_reply_window: int = 0        # 被动回复窗口（秒），0=无限制，QQ=300


class ChannelAdapter(ABC):
    """渠道适配器抽象基类。

    每个平台（Telegram / QQ / 飞书）实现此接口，
    由 MessageHandler 统一调度。
    """

    name: str = "unknown"
    capabilities: ChannelCapabilities = ChannelCapabilities()

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
    async def send_file(self, chat_id: str, data: bytes, filename: str) -> None:
        """发送文件给用户。data 为文件内容字节。"""

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

    async def send_staged_card(
        self,
        chat_id: str,
        files: list[dict[str, Any]],
        pending_count: int,
        session_id: str,
    ) -> None:
        """发送 staged 文件摘要卡片（带 apply/discard 按钮）。

        默认实现降级为纯文本列表。子类可覆盖实现 InlineKeyboard 等交互。
        """
        if not files:
            await self.send_text(chat_id, "📂 暂无待确认文件")
            return
        lines = [f"📦 {pending_count} 个文件待确认:\n"]
        from pathlib import PurePosixPath
        for i, f in enumerate(files[:10], 1):
            name = PurePosixPath(f.get("original_path", "?")).name
            summary = f.get("summary")
            detail = ""
            if summary:
                parts = []
                if summary.get("cells_changed"):
                    parts.append(f"~{summary['cells_changed']}")
                if summary.get("cells_added"):
                    parts.append(f"+{summary['cells_added']}")
                if summary.get("cells_removed"):
                    parts.append(f"-{summary['cells_removed']}")
                if summary.get("sheets_added"):
                    parts.append(f"+{len(summary['sheets_added'])} sheet")
                delta = summary.get("size_delta_bytes", 0)
                if delta and not parts:
                    parts.append(f"Δ{delta:+d}B")
                if parts:
                    detail = f"  ({', '.join(parts)})"
            lines.append(f"  {i}. {name}{detail}")
        lines.append("\n操作: /apply [编号|all]  /discard [编号|all]")
        await self.send_text(chat_id, "\n".join(lines))

    # ── 消息编辑能力（可选，各平台按需覆盖）──

    async def send_text_return_id(
        self, chat_id: str, text: str, reply_to: str | None = None,
    ) -> str:
        """发送文本并返回消息 ID（用于后续编辑）。

        不支持时降级为 send_text 并返回空字符串。
        """
        await self.send_text(chat_id, text)
        return ""

    async def send_markdown_return_id(
        self, chat_id: str, text: str, reply_to: str | None = None,
    ) -> str:
        """发送 Markdown 并返回消息 ID。"""
        await self.send_markdown(chat_id, text)
        return ""

    async def edit_text(
        self, chat_id: str, message_id: str, text: str,
    ) -> bool:
        """编辑已发送的纯文本消息。返回是否成功。"""
        return False

    async def edit_markdown(
        self, chat_id: str, message_id: str, text: str,
    ) -> bool:
        """编辑已发送的 Markdown 消息。返回是否成功。"""
        return False

    async def send_card(
        self, chat_id: str, card: dict[str, Any],
    ) -> str:
        """发送结构化卡片消息，返回消息 ID。不支持时降级为文本。"""
        text = card.get("text", card.get("content", str(card)))
        return await self.send_text_return_id(chat_id, text)

    async def update_card(
        self, chat_id: str, message_id: str, card: dict[str, Any],
    ) -> bool:
        """更新已发送的卡片消息内容。返回是否成功。"""
        return False

    # ── 工具方法 ──

    @staticmethod
    def split_message(text: str, max_len: int = 4000) -> list[str]:
        """将长文本按 max_len 拆分，优先在换行处断开。

        注意：这是兼容旧代码的简单实现。
        新代码应使用 channels.chunking.smart_chunk 进行语义分块。
        """
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
