"""有序提示词段组装。md 是正文素材，组装走 ``PromptRegistry``。

稳定 system 前缀：identity / persona / spreadsheet:invariants / 工具自有段 /
workbook_spec / run_code；strategy 段按 front-matter ``conditions`` 门控
（``plan:policy`` 仅 plan 激活时非空；写相关段仅 write 目录非空）。
动态世界模型不再经 fingerprint 注入第二条 system。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from excelmanus.prompt.registry import AssembleContext, PromptRegistry, interpolate

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
    order: int = 0
    max_tokens: int = 0  # 0 表示不限
    min_tokens: int = 0
    conditions: dict[str, Any] = field(default_factory=dict)
    complete: bool = False


@dataclass
class PromptContext:
    """当前请求的组装开关。"""

    chat_mode: str = "write"  # 取值："write" | "read" | "plan"


# ── strategy 段门控 ──────────────────────────────────────
# front-matter ``conditions`` 语法（键之间 AND，同一键的列表为 OR）：
#   catalog_mode: write | read | plan          # 标量或列表
#   chat_mode:     write | read | plan          # 标量或列表；plan_active 额外匹配 plan
#   tool:          工具名（可见目录含该名才注入；未传 visible_tools 时不过滤）
#   new_workbook:  bool                        # 工作区尚无表格文件
#   profile:       csv | xlsx | docx           # 目录 profile（csv 表示只有 CSV；仅依赖已有工作簿的工具被门控）
#   full_access:   bool
# 空 / {} 表示无门控。base_sections 只给子代理，不参与门控。未知键忽略。


def _condition_values(value: Any) -> frozenset[str]:
    """front-matter 标量或列表 → 非空字符串集合。"""
    if value is None:
        return frozenset()
    if isinstance(value, (list, tuple, set, frozenset)):
        return frozenset(str(item).strip() for item in value if str(item).strip())
    text = str(value).strip()
    return frozenset({text}) if text else frozenset()


def _catalog_mode_of(ctx: AssembleContext) -> str:
    from excelmanus.tools.catalog import resolve_catalog_mode

    return resolve_catalog_mode(
        chat_mode=ctx.chat_mode,
    )


def strategy_conditions_match(
    conditions: dict[str, Any] | None,
    ctx: AssembleContext,
) -> bool:
    """strategy 段 ``conditions`` 是否匹配本次 ``AssembleContext``。"""
    if not conditions:
        return True
    cond = {
        key: value
        for key, value in conditions.items()
        if key != "base_sections"
    }
    if not cond:
        return True

    if "catalog_mode" in cond:
        allowed = _condition_values(cond["catalog_mode"])
        if _catalog_mode_of(ctx) not in allowed:
            return False

    if "chat_mode" in cond:
        allowed = _condition_values(cond["chat_mode"])
        current = {str(ctx.chat_mode or "write")}
        if ctx.plan_active:
            current.add("plan")
        if current.isdisjoint(allowed):
            return False

    if "full_access" in cond:
        if bool(cond["full_access"]) != bool(ctx.full_access):
            return False

    if "tool" in cond:
        needed = _condition_values(cond["tool"])
        visible = getattr(ctx, "visible_tools", None)
        if visible is not None and visible.isdisjoint(needed):
            return False

    if "new_workbook" in cond:
        want = bool(cond["new_workbook"])
        if bool(getattr(ctx, "new_workbook", True)) != want:
            return False

    if "profile" in cond:
        allowed = _condition_values(cond["profile"])
        current = str(getattr(ctx, "profile", None) or "").strip()
        # 未判定 profile（None/空）不匹配任何 profile 段，避免误注入。
        if current not in allowed:
            return False

    return True


# ── Frontmatter 解析 ─────────────────────────────────────


_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_REQUIRED_FIELDS = ("name", "priority", "layer")
_ALLOWED_LAYERS = frozenset({"core", "strategy", "subagent"})
_ALLOWED_CONDITION_KEYS = frozenset({
    "catalog_mode",
    "chat_mode",
    "tool",
    "new_workbook",
    "profile",
    "full_access",
    "base_sections",
})
# 与 tools/catalog.py 的 inspect_workspace_catalog 输出保持一致。
_CATALOG_PROFILES = frozenset({"xlsx", "csv", "docx"})
_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$")


def parse_prompt_file(path: Path) -> PromptSegment:
    """解析单个提示词 .md 文件（YAML frontmatter + Markdown 正文）。

    Raises:
        ValueError: frontmatter 缺失或缺少必填字段。
    """
    raw = path.read_text(encoding="utf-8")
    m = _FRONTMATTER_RE.match(raw)
    if not m:
        raise ValueError(f"文件缺少 YAML frontmatter: {path}")
    try:
        meta = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError as exc:
        raise ValueError(f"frontmatter YAML 不合法: {path}") from exc
    if not isinstance(meta, dict):
        raise ValueError(f"frontmatter 必须是对象: {path}")
    for f in _REQUIRED_FIELDS:
        if f not in meta:
            raise ValueError(f"缺少必填字段 {f!r}: {path}")
    name = str(meta["name"]).strip()
    if not name or not _NAME_RE.fullmatch(name):
        raise ValueError(f"提示词 name 不合法: {name!r}: {path}")
    layer = str(meta["layer"]).strip()
    if layer not in _ALLOWED_LAYERS:
        raise ValueError(f"不支持的提示词 layer: {layer!r}: {path}")
    priority = int(meta["priority"])
    order = int(meta["order"]) if "order" in meta else priority
    if not -10000 <= priority <= 10000 or not -10000 <= order <= 10000:
        raise ValueError(f"提示词 priority/order 超出范围: {path}")
    max_tokens = int(meta.get("max_tokens", 0) or 0)
    min_tokens = int(meta.get("min_tokens", 0) or 0)
    if max_tokens < 0 or min_tokens < 0 or (max_tokens and min_tokens > max_tokens):
        raise ValueError(f"提示词 token 预算不合法: {path}")
    raw_conditions = meta.get("conditions") or {}
    if not isinstance(raw_conditions, dict):
        raise ValueError(f"提示词 conditions 必须是对象: {path}")
    conditions = dict(raw_conditions)
    unknown_conditions = sorted(set(conditions) - _ALLOWED_CONDITION_KEYS)
    if unknown_conditions:
        raise ValueError(
            f"提示词 conditions 含未知键 {', '.join(unknown_conditions)}: {path}"
        )
    for mode_key in ("catalog_mode", "chat_mode"):
        if mode_key in conditions:
            modes = _condition_values(conditions[mode_key])
            if not modes or not modes <= {"read", "plan", "write"}:
                raise ValueError(f"提示词 {mode_key} 必须是 read/plan/write: {path}")
    if "profile" in conditions:
        profiles = _condition_values(conditions["profile"])
        if not profiles or not profiles <= _CATALOG_PROFILES:
            raise ValueError(
                f"提示词 profile 必须是 {'/'.join(sorted(_CATALOG_PROFILES))}: {path}"
            )
    complete = bool(meta.get("complete", False))
    content = raw[m.end():].strip()
    if not content:
        raise ValueError(f"提示词正文不能为空: {path}")
    return PromptSegment(
        name=name,
        version=str(meta.get("version", "0.0.0")),
        priority=priority,
        layer=layer,
        content=content,
        order=order,
        max_tokens=max_tokens,
        min_tokens=min_tokens,
        conditions=conditions,
        complete=complete,
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
        self.registry = PromptRegistry()
        self.load_errors: list[str] = []
        self._file_stamp: tuple[Any, ...] | None = None

    def _source_stamp(self) -> tuple[Any, ...]:
        return tuple(
            (str(path), path.stat().st_mtime_ns, path.stat().st_size)
            for folder in ("core", "strategies", "subagent")
            for path in sorted((self._prompts_dir / folder).glob("*.md"))
        )

    def reload_if_changed(self) -> bool:
        if self._source_stamp() == self._file_stamp:
            return False
        self.load_all(auto_repair=False)
        return True

    def fork(self) -> PromptComposer:
        """复用正文素材，重建注册表，避免复制父会话的工具/context 回调。"""
        child = PromptComposer(self._prompts_dir)
        child.core_segments = list(self.core_segments)
        child.strategy_segments = list(self.strategy_segments)
        child.load_errors = list(self.load_errors)
        child._file_stamp = self._file_stamp
        child._rebuild_registry()
        child.reload_if_changed()
        child.validate_runtime()
        return child

    def validate_runtime(self) -> None:
        """生产会话不能静默忽略损坏或缺失的核心提示。测试素材可单独加载。"""
        names = {s.name for s in self.core_segments if s.content.strip()}
        missing = {"harness:identity", "deployment:persona", "spreadsheet:invariants"} - names
        if self.load_errors or missing:
            raise ValueError(
                "提示词加载不完整：" + "; ".join(self.load_errors + sorted(missing))
            )

    def load_all(self, *, auto_repair: bool = True) -> None:
        """启动时加载 prompts/ 下所有 .md 文件并解析 frontmatter。

        Args:
            auto_repair: 若为 True（默认），core/ 目录缺失或文件不全时
                自动从 _FALLBACK_CORE_FILES 补齐。测试时可设为 False。
        """
        self.core_segments.clear()
        self.strategy_segments.clear()
        self.load_errors.clear()

        core_dir = self._prompts_dir / "core"
        if auto_repair:
            self._ensure_core_files(core_dir)

        if core_dir.is_dir():
            for f in sorted(core_dir.glob("*.md")):
                try:
                    seg = parse_prompt_file(f)
                    self.core_segments.append(seg)
                except Exception as exc:
                    self.load_errors.append(f"{f.name}: {exc}")
                    logger.warning("跳过无效提示词文件 %s: %s", f, exc)

        strat_dir = self._prompts_dir / "strategies"
        if strat_dir.is_dir():
            for f in sorted(strat_dir.glob("*.md")):
                try:
                    seg = parse_prompt_file(f)
                    self.strategy_segments.append(seg)
                except Exception as exc:
                    self.load_errors.append(f"{f.name}: {exc}")
                    logger.warning("跳过无效策略文件 %s: %s", f, exc)

        self._rebuild_registry()
        self._file_stamp = self._source_stamp()
        logger.info(
            "PromptComposer: 加载 %d core + %d strategy 段",
            len(self.core_segments),
            len(self.strategy_segments),
        )

    def _rebuild_registry(self) -> None:
        """把已加载的 md 段登记进 PromptRegistry；条件不匹配时 text 为空。"""
        registry = PromptRegistry()
        for seg in self.core_segments:
            registry.section(seg.name, seg.order, seg.content, complete=seg.complete)
        for seg in self.strategy_segments:
            body = seg.content
            cond = dict(seg.conditions)
            registry.section(
                seg.name,
                seg.order,
                lambda ctx, text=body, conditions=cond, name=seg.name: (
                    text if (
                        (ctx.strategy_names is None or name in ctx.strategy_names or name == "plan:policy")
                        and strategy_conditions_match(conditions, ctx)
                    ) else ""
                ),
                complete=seg.complete,
            )
        self.registry = registry

    @staticmethod
    def _ensure_core_files(core_dir: Path) -> None:
        """确保 core/ 目录存在。"""
        try:
            core_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            logger.warning("无法创建 core 目录 %s: %s", core_dir, exc)

    def compose_system_text(
        self,
        ctx: PromptContext,
        variables: dict[str, str] | None = None,
        *,
        visible_tools: frozenset[str] | None = None,
        new_workbook: bool = True,
        full_access: bool = False,
        profile: str | None = None,
    ) -> str:
        """稳定 system 前缀：identity + persona + 按目录模式门控的策略段。"""
        assembly = self.registry.assemble(
            AssembleContext(
                plan_active=ctx.chat_mode == "plan",
                variables=variables,
                chat_mode=ctx.chat_mode,
                visible_tools=visible_tools,
                new_workbook=new_workbook,
                full_access=full_access,
                profile=profile,
            )
        )
        return self.registry.render_system(assembly)

    def compose_for_subagent(
        self,
        subagent_name: str,
        inherit_strategies: list[str] | None = None,
        variables: dict[str, str] | None = None,
        *,
        role_text: str | None = None,
        context: AssembleContext | None = None,
    ) -> str | None:
        """角色补充段。生产入口的 core/策略由共享 registry 按 child 条件组装。"""
        if (not subagent_name or subagent_name in {".", ".."}
                or "/" in subagent_name or "\\" in subagent_name):
            raise ValueError(f"子代理提示词名称不合法: {subagent_name!r}")
        subagent_dir = self._prompts_dir / "subagent"
        specific_file = subagent_dir / f"{subagent_name}.md"
        if role_text is None and not specific_file.exists():
            return None
        specific_seg = parse_prompt_file(specific_file) if role_text is None else None
        if specific_seg is not None and specific_seg.layer != "subagent":
            raise ValueError(f"子代理角色层必须为 subagent: {specific_file}")
        body = specific_seg.content if specific_seg is not None else str(role_text or "").strip()
        if not body:
            raise ValueError(f"子代理角色正文为空: {subagent_name}")
        # 缺失/损坏须由请求入口报告，不得绕过共享角色约束。
        base_file = subagent_dir / "_base.md"
        base_seg = parse_prompt_file(base_file)
        if base_seg.layer != "subagent":
            raise ValueError(f"子代理共享层必须为 subagent: {base_file}")
        parts = [base_seg.content, body]

        # ── 继承主代理策略段 ──
        if inherit_strategies:
            strategy_text = self._resolve_inherited_strategies(inherit_strategies, context=context)
            if strategy_text:
                parts.append(strategy_text)

        result = "\n\n".join(parts) if parts else None
        if not result:
            return None
        if variables is not None:
            return interpolate(result, variables, strict=True)
        return result

    def _resolve_inherited_strategies(
        self, inherit_strategies: list[str], *, context: AssembleContext | None = None,
    ) -> str:
        """按具名段从同一策略表取不变量，不认魔法标签。"""
        if not self.strategy_segments:
            return ""
        explicit_names = {
            name for name in inherit_strategies if name not in {"__universal__", "__all__"}
        }
        selected = [seg for seg in self.strategy_segments if seg.name in explicit_names
                    and (context is None or strategy_conditions_match(seg.conditions, context))]
        if not selected:
            return ""
        selected.sort(key=lambda s: (s.order, s.priority, s.name))
        return "\n\n".join(seg.content for seg in selected)

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
