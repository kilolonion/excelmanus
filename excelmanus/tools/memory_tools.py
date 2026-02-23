"""记忆工具：提供持久记忆的读写能力，供 LLM 在对话中主动保存和加载有价值的信息。"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime
from typing import TYPE_CHECKING, cast

from excelmanus.logger import get_logger
from excelmanus.memory_models import MemoryCategory, MemoryEntry
from excelmanus.tools.registry import ToolDef

if TYPE_CHECKING:
    from excelmanus.persistent_memory import PersistentMemory

logger = get_logger("tools.memory")

# ── 模块级 PersistentMemory 引用（延迟初始化） ─────────────

_persistent_memory: PersistentMemory | None = None
_UNSET = object()
_memory_context: ContextVar[PersistentMemory | None | object] = ContextVar(
    "excelmanus_memory_context",
    default=_UNSET,
)

# 主题名称到文件名的映射
_TOPIC_FILE_MAP: dict[str, str] = {
    "file_patterns": "file_patterns.md",
    "user_prefs": "user_prefs.md",
    "error_solutions": "error_solutions.md",
    "general": "general.md",
}

# 输入别名兼容（旧字段仍可读）
_TOPIC_ALIASES: dict[str, str] = {
    "file_pattern": "file_patterns",
    "user_pref": "user_prefs",
    "error_solution": "error_solutions",
}


def init_memory(persistent_memory: PersistentMemory | None) -> None:
    """兼容入口：设置模块级回退 PersistentMemory 引用。

    运行时优先使用 bind_memory_context 绑定的上下文实例。
    仅在未绑定上下文时回退到该全局引用（主要用于测试过渡与兼容旧调用）。

    Args:
        persistent_memory: PersistentMemory 实例或 None（None 表示禁用回退）。
    """
    global _persistent_memory
    _persistent_memory = persistent_memory


@contextmanager
def bind_memory_context(
    persistent_memory: PersistentMemory | None,
):
    """在当前执行上下文绑定 PersistentMemory（线程/协程隔离）。"""
    token = _memory_context.set(persistent_memory)
    try:
        yield
    finally:
        _memory_context.reset(token)


def _resolve_memory() -> PersistentMemory | None:
    """优先使用上下文绑定内存；缺失时回退到兼容全局引用。"""
    bound = _memory_context.get()
    if bound is not _UNSET:
        return cast("PersistentMemory | None", bound)
    return _persistent_memory


# ── 工具函数 ──────────────────────────────────────────────


def memory_read_topic(topic: str) -> str:
    """读取指定主题的持久记忆文件内容。

    Args:
        topic: 主题名称，支持 file_patterns 或 user_prefs。

    Returns:
        主题文件内容字符串，或提示信息。
    """
    memory = _resolve_memory()
    if memory is None:
        return "持久记忆功能未启用"

    normalized_topic = _TOPIC_ALIASES.get(topic, topic)
    filename = _TOPIC_FILE_MAP.get(normalized_topic)
    if filename is None:
        supported = ", ".join(_TOPIC_FILE_MAP)
        return f"不支持的主题: {topic}，支持的主题: {supported}"

    content = memory.load_topic(filename)
    if not content:
        return f"主题 '{normalized_topic}' 暂无记忆内容"

    return content


# ── 记忆写入 ──────────────────────────────────────────────

# 类别名到枚举的映射（含别名兼容）
_CATEGORY_MAP: dict[str, MemoryCategory] = {
    "file_pattern": MemoryCategory.FILE_PATTERN,
    "file_patterns": MemoryCategory.FILE_PATTERN,
    "user_pref": MemoryCategory.USER_PREF,
    "user_prefs": MemoryCategory.USER_PREF,
    "error_solution": MemoryCategory.ERROR_SOLUTION,
    "error_solutions": MemoryCategory.ERROR_SOLUTION,
    "general": MemoryCategory.GENERAL,
}


def memory_save(content: str, category: str) -> str:
    """将一条有价值的信息保存到持久记忆中。

    仅保存对未来会话有复用价值的信息，例如：
    - 文件结构特征（列名、数据类型、sheet 布局）
    - 用户偏好（图表样式、输出格式）
    - 错误解决方案
    - 常用工作流模式

    Args:
        content: 要保存的记忆内容，应简洁、结构化。
        category: 类别，支持 file_pattern / user_pref / error_solution / general。

    Returns:
        操作结果描述。
    """
    memory = _resolve_memory()
    if memory is None:
        return "持久记忆功能未启用"

    if memory.read_only_mode:
        return "持久记忆处于只读模式，无法写入"

    normalized_content = (content or "").strip()
    if not normalized_content:
        return "记忆内容不能为空"

    cat_enum = _CATEGORY_MAP.get(category)
    if cat_enum is None:
        supported = "file_pattern, user_pref, error_solution, general"
        return f"不支持的类别: {category}，支持的类别: {supported}"

    entry = MemoryEntry(
        content=normalized_content,
        category=cat_enum,
        timestamp=datetime.now(),
    )

    try:
        memory.save_entries([entry])
    except Exception:
        logger.exception("保存记忆条目失败")
        return "保存记忆失败，请稍后重试"

    return f"已保存到 {cat_enum.value} 类别"


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
                        "description": (
                            "主题名称，支持 file_patterns、user_prefs、"
                            "error_solutions、general；"
                            "兼容别名 file_pattern、user_pref、error_solution"
                        ),
                        "enum": [
                            "file_patterns",
                            "user_prefs",
                            "error_solutions",
                            "general",
                            "file_pattern",
                            "user_pref",
                            "error_solution",
                        ],
                    },
                },
                "required": ["topic"],
                "additionalProperties": False,
            },
            func=memory_read_topic,
            write_effect="none",
        ),
        ToolDef(
            name="memory_save",
            description=(
                "将有价值的信息保存到持久记忆，供未来会话复用。"
                "适合保存：文件结构特征、用户偏好、错误解决方案、常用工作流模式。"
                "不要保存：一次性数据值、临时文件路径、当前会话已知的上下文。"
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "content": {
                        "type": "string",
                        "description": (
                            "要保存的记忆内容，应简洁、结构化。"
                            "例如：'销售数据.xlsx 包含 sheet「2024年」，"
                            "列：日期(date)、产品(str)、数量(int)、单价(float)、金额(float)，"
                            "header 在第1行，约5000行数据'"
                        ),
                    },
                    "category": {
                        "type": "string",
                        "description": (
                            "记忆类别：file_pattern（文件结构）、"
                            "user_pref（用户偏好）、"
                            "error_solution（错误解决方案）、"
                            "general（其他有价值信息）"
                        ),
                        "enum": [
                            "file_pattern",
                            "user_pref",
                            "error_solution",
                            "general",
                        ],
                    },
                },
                "required": ["content", "category"],
                "additionalProperties": False,
            },
            func=memory_save,
            write_effect="external_write",
        ),
    ]
