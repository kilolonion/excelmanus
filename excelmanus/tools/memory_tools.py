"""记忆工具：提供主题文件按需读取能力，供 LLM 在需要时主动加载特定领域的持久记忆。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from excelmanus.logger import get_logger
from excelmanus.tools.registry import ToolDef

if TYPE_CHECKING:
    from excelmanus.persistent_memory import PersistentMemory

logger = get_logger("tools.memory")

# ── 模块级 PersistentMemory 引用（延迟初始化） ─────────────

_persistent_memory: PersistentMemory | None = None

# 主题名称到文件名的映射
_TOPIC_FILE_MAP: dict[str, str] = {
    "file_patterns": "file_patterns.md",
    "user_prefs": "user_prefs.md",
}


def init_memory(persistent_memory: PersistentMemory | None) -> None:
    """初始化模块级 PersistentMemory 引用。

    在 AgentEngine 初始化时调用，传入 PersistentMemory 实例。
    传入 None 表示持久记忆功能未启用。

    Args:
        persistent_memory: PersistentMemory 实例或 None。
    """
    global _persistent_memory
    _persistent_memory = persistent_memory


# ── 工具函数 ──────────────────────────────────────────────


def memory_read_topic(topic: str) -> str:
    """读取指定主题的持久记忆文件内容。

    Args:
        topic: 主题名称，支持 file_patterns 或 user_prefs。

    Returns:
        主题文件内容字符串，或提示信息。
    """
    if _persistent_memory is None:
        return "持久记忆功能未启用"

    filename = _TOPIC_FILE_MAP.get(topic)
    if filename is None:
        return f"不支持的主题: {topic}，支持的主题: {', '.join(_TOPIC_FILE_MAP)}"

    content = _persistent_memory.load_topic(filename)
    if not content:
        return f"主题 '{topic}' 暂无记忆内容"

    return content


# ── get_tools() 导出 ──────────────────────────────────────


def get_tools() -> list[ToolDef]:
    """返回记忆工具的所有工具定义。"""
    return [
        ToolDef(
            name="memory_read_topic",
            description="读取指定主题的持久记忆文件内容",
            input_schema={
                "type": "object",
                "properties": {
                    "topic": {
                        "type": "string",
                        "description": "主题名称，支持 file_patterns 或 user_prefs",
                        "enum": ["file_patterns", "user_prefs"],
                    },
                },
                "required": ["topic"],
                "additionalProperties": False,
            },
            func=memory_read_topic,
        ),
    ]
