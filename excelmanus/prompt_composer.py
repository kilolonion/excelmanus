"""模块化提示词组装引擎。

将 memory.py 中硬编码的系统提示词迁移为独立 .md 文件，
通过 YAML frontmatter 声明元数据（优先级、版本、匹配条件），
由 PromptComposer 负责加载、条件匹配和预算裁剪。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)


# ── 数据模型 ──────────────────────────────────────────────


@dataclass(frozen=True)
class PromptSegment:
    """单个提示词段的加载结果。"""

    name: str
    version: str
    priority: int
    layer: str  # "core" | "strategy" | "subagent"
    content: str
    max_tokens: int = 0  # 0 表示不限
    min_tokens: int = 0
    conditions: dict[str, Any] = field(default_factory=dict)


@dataclass
class PromptContext:
    """当前请求的上下文信号，用于策略匹配。"""

    chat_mode: str = "write"  # 取值："write" | "read" | "plan"
    write_hint: str = "unknown"
    sheet_count: int = 0
    total_rows: int = 0
    file_count: int = 0
    task_tags: list[str] = field(default_factory=list)
    user_message: str = ""
    full_access: bool = False


# ── Frontmatter 解析 ─────────────────────────────────────


_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_REQUIRED_FIELDS = ("name", "priority", "layer")


def parse_prompt_file(path: Path) -> PromptSegment:
    """解析单个提示词 .md 文件（YAML frontmatter + Markdown 正文）。

    Raises:
        ValueError: frontmatter 缺失或缺少必填字段。
    """
    raw = path.read_text(encoding="utf-8")
    m = _FRONTMATTER_RE.match(raw)
    if not m:
        raise ValueError(f"文件缺少 YAML frontmatter: {path}")
    meta = yaml.safe_load(m.group(1)) or {}
    for f in _REQUIRED_FIELDS:
        if f not in meta:
            raise ValueError(f"缺少必填字段 {f!r}: {path}")
    content = raw[m.end():].strip()
    return PromptSegment(
        name=str(meta["name"]),
        version=str(meta.get("version", "0.0.0")),
        priority=int(meta["priority"]),
        layer=str(meta["layer"]),
        content=content,
        max_tokens=int(meta.get("max_tokens", 0)),
        min_tokens=int(meta.get("min_tokens", 0)),
        conditions=dict(meta.get("conditions", {}) or {}),
    )


# ── 兜底 core 文件内容 ────────────────────────────────────
# 当 prompts/core/ 目录缺失或文件不全时，自动补齐以下默认内容。

_FALLBACK_CORE_FILES: dict[str, str] = {}


# ── PromptComposer ───────────────────────────────────────


class PromptComposer:
    """模块化提示词加载与组装引擎。"""

    def __init__(self, prompts_dir: Path) -> None:
        self._prompts_dir = prompts_dir
        self.core_segments: list[PromptSegment] = []
        self.strategy_segments: list[PromptSegment] = []

    def load_all(self, *, auto_repair: bool = True) -> None:
        """启动时加载 prompts/ 下所有 .md 文件并解析 frontmatter。

        Args:
            auto_repair: 若为 True（默认），core/ 目录缺失或文件不全时
                自动从 _FALLBACK_CORE_FILES 补齐。测试时可设为 False。
        """
        self.core_segments.clear()
        self.strategy_segments.clear()

        core_dir = self._prompts_dir / "core"
        if auto_repair:
            self._ensure_core_files(core_dir)

        if core_dir.is_dir():
            for f in sorted(core_dir.glob("*.md")):
                try:
                    seg = parse_prompt_file(f)
                    self.core_segments.append(seg)
                except Exception as exc:
                    logger.warning("跳过无效提示词文件 %s: %s", f, exc)

        strat_dir = self._prompts_dir / "strategies"
        if strat_dir.is_dir():
            for f in sorted(strat_dir.glob("*.md")):
                try:
                    seg = parse_prompt_file(f)
                    self.strategy_segments.append(seg)
                except Exception as exc:
                    logger.warning("跳过无效策略文件 %s: %s", f, exc)

        logger.info(
            "PromptComposer: 加载 %d core + %d strategy 段",
            len(self.core_segments),
            len(self.strategy_segments),
        )

    @staticmethod
    def _ensure_core_files(core_dir: Path) -> None:
        """确保 core/ 目录存在。"""
        try:
            core_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            logger.warning("无法创建 core 目录 %s: %s", core_dir, exc)

    def compose(
        self,
        ctx: PromptContext,
        token_budget: int = 0,
    ) -> list[PromptSegment]:
        """根据上下文匹配并组装提示词段。

        流程:
        1. 始终包含所有 core/ 段
        2. 对 strategies/ 段做条件匹配
        3. 按 priority 排序
        4. 按 token_budget 裁剪（低优先级先丢弃）
        """
        selected: list[PromptSegment] = list(self.core_segments)
        for strat in self.strategy_segments:
            if self._match_conditions(strat.conditions, ctx):
                selected.append(strat)
        selected.sort(key=lambda s: s.priority)
        if token_budget > 0:
            selected = self._apply_budget(selected, token_budget)
        return selected

    def compose_text(
        self,
        ctx: PromptContext,
        token_budget: int = 0,
        variables: dict[str, str] | None = None,
    ) -> str:
        """compose() 的便捷版本，直接返回拼接后的文本。

        Args:
            variables: 运行时变量字典，键值对会替换文本中的 ``{key}`` 占位符。
        """
        segments = self.compose(ctx, token_budget)
        text = "\n\n".join(seg.content for seg in segments)
        if variables:
            for key, value in variables.items():
                text = text.replace(f"{{{key}}}", value)
        return text

    def compose_strategies_text(self, ctx: PromptContext) -> str:
        """仅返回匹配的策略段文本（不含 core），用于动态注入。"""
        matched = [
            strat
            for strat in self.strategy_segments
            if self._match_conditions(strat.conditions, ctx)
        ]
        if not matched:
            return ""
        matched.sort(key=lambda s: s.priority)
        return "\n\n".join(seg.content for seg in matched)

    def compose_for_subagent(self, subagent_name: str) -> str | None:
        """组装子代理提示词 = _base.md 正文 + {name}.md 正文。

        若 {name}.md frontmatter 中包含 ``base_sections`` 列表，
        则仅从 _base.md 中提取对应段落（由 ``<!-- section: xxx -->``
        标记分隔）；未指定时加载完整 _base.md。

        Args:
            subagent_name: 子代理名称（如 "explorer"、"planner"）。

        Returns:
            拼接后的提示词文本，若对应文件不存在则返回 None。
        """
        subagent_dir = self._prompts_dir / "subagent"
        if not subagent_dir.is_dir():
            return None

        specific_file = subagent_dir / f"{subagent_name}.md"
        if not specific_file.exists():
            return None

        # 加载专用文件（先解析以获取 base_sections）
        try:
            specific_seg = parse_prompt_file(specific_file)
        except Exception as exc:
            logger.warning("子代理 %s.md 解析失败: %s", subagent_name, exc)
            return None

        # 从 frontmatter conditions 中提取 base_sections（可选）
        base_sections: list[str] | None = specific_seg.conditions.get(
            "base_sections"
        )

        parts: list[str] = []

        # 加载共享基础 _base.md（可选）
        base_file = subagent_dir / "_base.md"
        if base_file.exists():
            try:
                base_seg = parse_prompt_file(base_file)
                base_content = base_seg.content.strip()
                if base_content:
                    if base_sections:
                        base_content = self._filter_base_sections(
                            base_content, base_sections
                        )
                    if base_content:
                        parts.append(base_content)
            except Exception as exc:
                logger.warning("子代理 _base.md 解析失败: %s", exc)

        if specific_seg.content.strip():
            parts.append(specific_seg.content)

        return "\n\n".join(parts) if parts else None

    @staticmethod
    def _filter_base_sections(
        content: str, allowed: list[str]
    ) -> str:
        """从 _base.md 内容中按 ``<!-- section: xxx -->`` 标记提取指定段落。"""
        section_re = re.compile(r"<!--\s*section:\s*(\S+)\s*-->")
        sections: dict[str, list[str]] = {}
        current_key: str | None = None
        for line in content.splitlines(keepends=True):
            m = section_re.match(line)
            if m:
                current_key = m.group(1)
                sections[current_key] = []
            elif current_key is not None:
                sections[current_key].append(line)
        parts = []
        for key in allowed:
            if key in sections:
                parts.append("".join(sections[key]).strip())
        return "\n\n".join(parts)

    @staticmethod
    def _match_conditions(conditions: dict[str, Any], ctx: PromptContext) -> bool:
        """检查策略的所有条件是否满足（AND 逻辑）。"""
        if not conditions:
            return True
        for key, value in conditions.items():
            if key == "write_hint":
                if ctx.write_hint != value:
                    return False
            elif key == "sheet_count_gte":
                if ctx.sheet_count < int(value):
                    return False
            elif key == "total_rows_gte":
                if ctx.total_rows < int(value):
                    return False
            elif key == "task_tags":
                expected = set(value) if isinstance(value, list) else {value}
                if not expected & set(ctx.task_tags):
                    return False
            elif key == "chat_mode":
                expected_modes = {value} if isinstance(value, str) else set(value)
                if ctx.chat_mode not in expected_modes:
                    return False
            elif key == "full_access":
                expected_val = bool(value)
                if ctx.full_access != expected_val:
                    return False
            # 未知条件键：忽略（宽松匹配，便于扩展）
        return True

    @staticmethod
    def _apply_budget(
        segments: list[PromptSegment],
        token_budget: int,
    ) -> list[PromptSegment]:
        """按优先级裁剪，低优先级（数字大）先丢弃。"""
        from excelmanus.memory import TokenCounter

        total = sum(TokenCounter.count(s.content) for s in segments)
        if total <= token_budget:
            return segments

        result = list(segments)
        # 按 priority 降序处理（数字大的先丢弃）
        for seg in sorted(segments, key=lambda s: s.priority, reverse=True):
            if total <= token_budget:
                break
            if seg.priority < 10:
                continue
            if seg in result:
                seg_tokens = TokenCounter.count(seg.content)
                result.remove(seg)
                total -= seg_tokens
        return result
