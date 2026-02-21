"""prompt_toolkit 两阶段补全器。

阶段一：用户输入 @ 后显示分类菜单（file/folder/skill/mcp/img）
阶段二：选择分类后，逐级浏览文件系统（选择目录后进入下一级）

排除规则（Requirements 7.7, 7.8）：
- 排除隐藏文件（以 `.` 开头）
- 排除 `.venv` 目录
- 排除 `node_modules` 目录
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Iterable

from prompt_toolkit.completion import CompleteEvent, Completer, Completion
from prompt_toolkit.document import Document

if TYPE_CHECKING:
    from excelmanus.engine import AgentEngine

# 分类菜单项
_CATEGORIES = [
    ("file", "引用工作区文件"),
    ("folder", "引用工作区目录"),
    ("skill", "引用已加载技能"),
    ("mcp", "引用 MCP 服务"),
    ("img", "引用图片文件"),
]

# 图片扩展名
_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"}

# 排除的目录/文件名
_EXCLUDED_NAMES = {".venv", "node_modules", "__pycache__"}


class MentionCompleter(Completer):
    """prompt_toolkit 两阶段补全器，文件/目录支持逐级浏览。

    阶段一：用户输入 @ 后显示分类菜单（file/folder/skill/mcp/img）
    阶段二：选择分类后，只显示当前层级的条目；选择目录后进入下一级
    """

    def __init__(
        self,
        workspace_root: str,
        engine: AgentEngine | None = None,
        max_scan_depth: int = 2,
    ) -> None:
        self._workspace_root = workspace_root
        self._engine = engine
        self._max_scan_depth = max_scan_depth

    def get_completions(
        self, document: Document, complete_event: CompleteEvent
    ) -> Iterable[Completion]:
        """根据当前输入状态返回补全候选项。"""
        text_before = document.text_before_cursor

        # 查找最后一个 @ 的位置
        at_pos = text_before.rfind("@")
        if at_pos < 0:
            return

        after_at = text_before[at_pos + 1 :]

        # 阶段一：刚输入 @，显示分类菜单
        if after_at == "":
            yield from self._category_completions()
            return

        # 阶段二：已选择分类，内联补全具体值
        for cat_name, _desc in _CATEGORIES:
            if cat_name == "img":
                prefix = "img "
                if after_at.lower().startswith(prefix):
                    partial = after_at[len(prefix) :]
                    yield from self._img_completions(partial)
                    return
                elif "img".startswith(after_at.lower()) and not after_at.endswith(":"):
                    yield from self._filtered_category_completions(after_at)
                    return
            else:
                prefix = f"{cat_name}:"
                if after_at.lower().startswith(prefix):
                    partial = after_at[len(prefix) :]
                    yield from self._value_completions(cat_name, partial)
                    return

        # 正在输入分类名（如 @fi、@fol），过滤分类菜单
        yield from self._filtered_category_completions(after_at)

    # ── 阶段一：分类菜单 ─────────────────────────────────

    def _category_completions(self) -> Iterable[Completion]:
        """显示所有分类候选项。"""
        for name, desc in _CATEGORIES:
            suffix = " " if name == "img" else ":"
            yield Completion(
                text=f"{name}{suffix}",
                start_position=0,
                display=f"@{name}",
                display_meta=desc,
            )

    def _filtered_category_completions(self, partial: str) -> Iterable[Completion]:
        """根据已输入的部分文本过滤分类候选项。"""
        lower_partial = partial.lower()
        for name, desc in _CATEGORIES:
            if name.startswith(lower_partial):
                suffix = " " if name == "img" else ":"
                yield Completion(
                    text=f"{name}{suffix}",
                    start_position=-len(partial),
                    display=f"@{name}",
                    display_meta=desc,
                )

    # ── 阶段二：具体值补全 ───────────────────────────────

    def _value_completions(self, kind: str, partial: str) -> Iterable[Completion]:
        """根据分类类型返回具体值候选项。"""
        if kind == "file":
            yield from self._path_completions(partial, files=True, dirs=True)
        elif kind == "folder":
            yield from self._path_completions(partial, files=False, dirs=True)
        elif kind == "skill":
            yield from self._skill_completions(partial)
        elif kind == "mcp":
            yield from self._mcp_completions(partial)

    def _path_completions(
        self, partial: str, *, files: bool, dirs: bool
    ) -> Iterable[Completion]:
        """逐级浏览文件系统：只列出当前层级的条目。

        partial 示例：
        - ""              → 列出根目录下的文件和子目录
        - "excelmanus/"   → 列出 excelmanus/ 下的文件和子目录
        - "excelmanus/cl" → 过滤 excelmanus/ 下以 "cl" 开头的条目
        """
        root = Path(self._workspace_root)

        # 拆分 partial 为已确定的目录前缀和正在输入的片段
        if "/" in partial:
            last_slash = partial.rfind("/")
            dir_prefix = partial[: last_slash + 1]  # 如 "excelmanus/"
            name_fragment = partial[last_slash + 1 :]  # 如 "cl"
        else:
            dir_prefix = ""
            name_fragment = partial

        # 计算当前浏览的目录
        browse_dir = root / dir_prefix if dir_prefix else root

        if not browse_dir.is_dir():
            return

        # 检查深度限制
        depth = dir_prefix.count("/") if dir_prefix else 0
        if depth > self._max_scan_depth:
            return

        lower_fragment = name_fragment.lower()

        try:
            entries = sorted(browse_dir.iterdir(), key=lambda p: p.name.lower())
        except (PermissionError, OSError):
            return

        for entry in entries:
            # 排除隐藏文件和排除目录
            if entry.name.startswith(".") or entry.name in _EXCLUDED_NAMES:
                continue

            if entry.is_dir():
                # 目录总是显示（用于逐级深入），带 / 后缀
                rel = f"{dir_prefix}{entry.name}/"
                if entry.name.lower().startswith(lower_fragment):
                    yield Completion(
                        text=rel,
                        start_position=-len(partial),
                        display=f"📁 {entry.name}/",
                        display_meta="目录",
                    )
            elif entry.is_file() and files:
                rel = f"{dir_prefix}{entry.name}"
                if entry.name.lower().startswith(lower_fragment):
                    yield Completion(
                        text=rel,
                        start_position=-len(partial),
                        display=f"  {entry.name}",
                    )

    def _img_completions(self, partial: str) -> Iterable[Completion]:
        """逐级浏览图片文件。"""
        root = Path(self._workspace_root)

        if "/" in partial:
            last_slash = partial.rfind("/")
            dir_prefix = partial[: last_slash + 1]
            name_fragment = partial[last_slash + 1 :]
        else:
            dir_prefix = ""
            name_fragment = partial

        browse_dir = root / dir_prefix if dir_prefix else root
        if not browse_dir.is_dir():
            return

        lower_fragment = name_fragment.lower()

        try:
            entries = sorted(browse_dir.iterdir(), key=lambda p: p.name.lower())
        except (PermissionError, OSError):
            return

        for entry in entries:
            if entry.name.startswith(".") or entry.name in _EXCLUDED_NAMES:
                continue

            if entry.is_dir():
                rel = f"{dir_prefix}{entry.name}/"
                if entry.name.lower().startswith(lower_fragment):
                    yield Completion(
                        text=rel,
                        start_position=-len(partial),
                        display=f"📁 {entry.name}/",
                        display_meta="目录",
                    )
            elif entry.is_file():
                suffix = entry.suffix.lower()
                if suffix in _IMAGE_EXTENSIONS:
                    rel = f"{dir_prefix}{entry.name}"
                    if entry.name.lower().startswith(lower_fragment):
                        yield Completion(
                            text=rel,
                            start_position=-len(partial),
                            display=f"🖼  {entry.name}",
                        )

    def _skill_completions(self, partial: str) -> Iterable[Completion]:
        """列出 user_invocable 的已加载 Skillpack 名称。"""
        if self._engine is None:
            return
        try:
            names = self._engine._list_manual_invocable_skill_names()
        except Exception:
            return
        lower_partial = partial.lower()
        for name in sorted(names):
            if name.lower().startswith(lower_partial):
                yield Completion(
                    text=name,
                    start_position=-len(partial),
                    display=name,
                )

    def _mcp_completions(self, partial: str) -> Iterable[Completion]:
        """列出已连接的 MCP 服务名称。"""
        if self._engine is None:
            return
        try:
            servers = self._engine.mcp_server_info()
        except Exception:
            return
        lower_partial = partial.lower()
        for info in servers:
            name = info.get("name", "")
            if isinstance(name, str) and name.lower().startswith(lower_partial):
                status = info.get("status", "")
                yield Completion(
                    text=name,
                    start_position=-len(partial),
                    display=name,
                    display_meta=str(status) if status else None,
                )
