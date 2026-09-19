"""提示词段预算：复用现有组装路径，不另写一套 composer。

口径：tiktoken o200k_base；段 token 剥离 frontmatter 后独立计数。
完整 system 含能力地图与 SDK；wire tools 为当前目录 JSON；发现消耗为
代表性 introspect 查询。静态 token 不是服务商计费量。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from excelmanus.memory import TokenCounter
from excelmanus.prompt.load import PromptComposer, PromptContext, parse_prompt_file
from excelmanus.prompt.registry import AssembleContext, interpolate

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"
FIXED_WORKSPACE = "/tmp/excelmanus-ws"
FIXED_MODEL = "test-model"
FIXED_VARS = {"workspace_root": FIXED_WORKSPACE, "model": FIXED_MODEL}

# 原则 + 策略正文：identity / persona / 领域原则 / strategy 段。不含能力地图、SDK。
_PRINCIPLE_LAYERS = frozenset({"core", "strategy"})
_DISCOVERY_QUERIES = (
    ("tool_detail", "edit_spreadsheet.workbook_spec"),
    ("tool_detail", "edit_spreadsheet.workbook_spec.sheets"),
    ("tool_detail", "edit_spreadsheet.workbook_spec.sheets.value_blocks"),
    ("tool_detail", "edit_spreadsheet.workbook_spec.sheets.styles"),
    ("tool_detail", "edit_spreadsheet.workbook_spec.sheets.styles.border"),
    ("tool_detail", "edit_spreadsheet.workbook_spec.sheets.conditional_formats"),
    ("tool_detail", "edit_spreadsheet.workbook_spec.uncertainties"),
    ("tool_detail", "inspect_spreadsheet.range"),
    ("tool_detail", "analyze_spreadsheet.join"),
    ("tool_detail", "format_spreadsheet.operations.rule"),
)


@dataclass(frozen=True)
class SegmentBudget:
    name: str
    path: str
    layer: str
    order: int
    tokens: int
    max_tokens: int
    over_budget: bool


@dataclass
class ScenarioBudget:
    name: str
    chat_mode: str
    present_as: str
    new_workbook: bool
    families: tuple[str, ...]
    section_names: tuple[str, ...]
    visible_tools: tuple[str, ...]
    wire_tools: tuple[str, ...]
    sdk_tools: tuple[str, ...]
    principle_tokens: int
    system_tokens: int
    tools_json_tokens: int
    sdk_tokens: int
    discovery_tokens: int
    system_text: str
    tools_json: str
    sdk_text: str
    discovery_text: str
    over_budget_segments: tuple[str, ...] = ()


@dataclass
class BudgetReport:
    segments: list[SegmentBudget] = field(default_factory=list)
    scenarios: list[ScenarioBudget] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "tokenizer": "o200k_base",
            "workspace_root": FIXED_WORKSPACE,
            "model": FIXED_MODEL,
            "note": "静态 token，不是服务商计费量；不含段间分隔符以外的消息结构开销。",
            "segments": [item.__dict__ for item in self.segments],
            "scenarios": [
                {
                    "name": item.name,
                    "chat_mode": item.chat_mode,
                    "present_as": item.present_as,
                    "new_workbook": item.new_workbook,
                    "families": list(item.families),
                    "section_names": list(item.section_names),
                    "visible_tools": list(item.visible_tools),
                    "wire_tools": list(item.wire_tools),
                    "sdk_tools": list(item.sdk_tools),
                    "principle_tokens": item.principle_tokens,
                    "system_tokens": item.system_tokens,
                    "tools_json_tokens": item.tools_json_tokens,
                    "sdk_tokens": item.sdk_tokens,
                    "discovery_tokens": item.discovery_tokens,
                    "over_budget_segments": list(item.over_budget_segments),
                }
                for item in self.scenarios
            ],
        }


def count_tokens(text: str) -> int:
    return TokenCounter.count(text or "")


def iter_prompt_files() -> list[Path]:
    files: list[Path] = []
    for folder in ("core", "strategies"):
        directory = PROMPTS_DIR / folder
        if directory.is_dir():
            files.extend(sorted(directory.glob("*.md")))
    return files


def measure_segments() -> list[SegmentBudget]:
    rows: list[SegmentBudget] = []
    for path in iter_prompt_files():
        seg = parse_prompt_file(path)
        tokens = count_tokens(seg.content)
        max_tokens = int(seg.max_tokens or 0)
        rows.append(
            SegmentBudget(
                name=seg.name,
                path=str(path.relative_to(PROMPTS_DIR.parent.parent)),
                layer=seg.layer,
                order=seg.order,
                tokens=tokens,
                max_tokens=max_tokens,
                over_budget=bool(max_tokens and tokens > max_tokens),
            )
        )
    rows.sort(key=lambda item: (item.order, item.name))
    return rows


def _prepare_workspace(root: Path, *, profile: str, has_workbook: bool) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "uploads").mkdir(exist_ok=True)
    (root / "outputs").mkdir(exist_ok=True)
    book = root / "existing.xlsx"
    csv_path = root / "sales.csv"
    docx_path = root / "notes.docx"
    if has_workbook:
        if not book.exists():
            book.write_bytes(b"PK\x03\x04")
    elif book.exists():
        book.unlink()
    if profile == "csv":
        if not csv_path.exists():
            csv_path.write_text("a,b\n1,2\n", encoding="utf-8")
    elif csv_path.exists():
        csv_path.unlink()
    if profile == "docx":
        if not docx_path.exists():
            docx_path.write_bytes(b"PK\x03\x04")
    elif docx_path.exists():
        docx_path.unlink()


def _make_engine(
    *,
    chat_mode: str,
    present_as: str,
    workspace: Path,
    composer: PromptComposer,
) -> Any:
    from excelmanus.tools.registry import ToolRegistry

    registry = ToolRegistry()
    registry.register_builtin_tools(str(workspace))
    from excelmanus.tools.introspection_tools import register_introspection_tools

    register_introspection_tools(registry)

    # 与 AgentEngine.__init__ 一致的会话级工具：task / plan 也在真实 envelope 里。
    from excelmanus.task_list import TaskStore
    from excelmanus.tools.task_tools import get_tools as task_tools
    from excelmanus.tools import plan_tools

    task_store = TaskStore()
    registry.register_tools(task_tools(task_store))
    registry.register_tools(
        plan_tools.get_tools(
            task_store,
            str(workspace),
            is_plan_active=lambda: chat_mode == "plan",
            on_exit_submitted=lambda _plan: None,
        )
    )

    engine = SimpleNamespace(
        _prompt_composer=composer,
        _current_chat_mode=chat_mode,
        _present_as=present_as,
        _runtime_vars=dict(FIXED_VARS, workspace_root=str(workspace)),
        _child_system_prompt=None,
        _tool_runtime=None,
        _catalog_new_workbook=True,
        _fixed_capability=None,
        config=SimpleNamespace(workspace_root=str(workspace)),
        active_model=FIXED_MODEL,
        _registry=registry,
        registry=registry,
        memory=SimpleNamespace(system_prompt=""),
        _skill_router=None,
        _subagent_config=None,
        max_context_tokens=128000,
        state=SimpleNamespace(
            prompt_injection_snapshots=[],
            injected_context_fingerprint=None,
        ),
        _transient_hook_contexts=[],
        _session_turn=1,
        _bind_prompt_registry_runtime=None,
    )
    return engine


def _principle_tokens(composer: PromptComposer, ctx: AssembleContext) -> tuple[int, tuple[str, ...]]:
    assembly = composer.registry.assemble(ctx)
    names: list[str] = []
    total = 0
    for section in assembly.sections:
        if section.name.startswith("tools:"):
            continue
        names.append(section.name)
        total += count_tokens(section.text)
    return total, tuple(names)


def _discovery_blob(engine: Any) -> tuple[str, tuple[str, ...]]:
    """发现成本 + SDK 绑定工具名单。

    introspect / SDK 都绑 L2 执行目录（``catalog_from_engine`` 已不因
    present_as=code 坍缩）。code 呈现下返回的名单是执行目录减 ``run_code``。
    """
    from excelmanus.tools.catalog import RUN_CODE_NAME, catalog_from_engine
    from excelmanus.tools.introspection_tools import bind_introspection_catalog, introspect_capability
    from excelmanus.tools.runtime import present_as_of

    catalog = catalog_from_engine(engine)
    if catalog is None:
        return "", ()
    sdk_names: tuple[str, ...] = ()
    if present_as_of(engine) == "code":
        sdk_names = tuple(name for name in catalog.names() if name != RUN_CODE_NAME)
    bind_introspection_catalog(catalog)
    parts: list[str] = []
    for query_type, query in _DISCOVERY_QUERIES:
        try:
            parts.append(introspect_capability(query_type, query))
        except Exception as exc:  # noqa: BLE001
            parts.append(f"{query_type}:{query}: {exc}")
    return "\n\n".join(parts), sdk_names


def measure_scenario(
    *,
    name: str,
    chat_mode: str,
    present_as: str,
    has_workbook: bool,
    profile: str = "xlsx",
    workspace: Path | None = None,
    composer: PromptComposer | None = None,
) -> ScenarioBudget:
    from excelmanus.code_mode import render_sdk_section
    from excelmanus.prompt.assemble import build_stable_system_prompt
    from excelmanus.prompt.envelope import sort_tool_schemas
    from excelmanus.tools.catalog import catalog_from_engine
    from excelmanus.tools.context import execution_catalog_tools
    from excelmanus.tools.runtime import collapse_schemas

    base = workspace or Path(FIXED_WORKSPACE)
    root = base / name
    _prepare_workspace(root, profile=profile, has_workbook=has_workbook)
    loaded = composer or PromptComposer(PROMPTS_DIR)
    if composer is None:
        loaded.load_all(auto_repair=False)
    engine = _make_engine(
        chat_mode=chat_mode,
        present_as=present_as,
        workspace=root,
        composer=loaded,
    )
    catalog = catalog_from_engine(engine)
    sdk_text = ""
    if present_as == "code":
        sdk_text = render_sdk_section(execution_catalog_tools(engine))
        engine._tool_runtime = SimpleNamespace(render_sdk_section=lambda: sdk_text)
    system_text = build_stable_system_prompt(engine)
    visible_names: frozenset[str] | None = (
        frozenset(catalog.names()) if catalog is not None else None
    )
    # tools_json 走 L4 wire：catalog（L2 全量权限目录）→ collapse(code→run_code)。
    wire_schemas = sort_tool_schemas(
        collapse_schemas(
            catalog.tool_schemas() if catalog is not None else [],
            present_as,
        )
    )
    tools_json = json.dumps(
        wire_schemas,
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    )
    discovery_text, sdk_tools = _discovery_blob(engine)
    assemble_ctx = AssembleContext(
        plan_active=chat_mode == "plan",
        present_as=present_as,
        variables={"workspace_root": str(root), "model": FIXED_MODEL},
        chat_mode=chat_mode,
        sdk_section=sdk_text,
        visible_tools=visible_names,
        new_workbook=bool(getattr(engine, "_catalog_new_workbook", True)),
    )
    principle_tokens, section_names = _principle_tokens(loaded, assemble_ctx)
    over = tuple(
        seg.name
        for seg in loaded.core_segments + loaded.strategy_segments
        if seg.max_tokens and count_tokens(seg.content) > seg.max_tokens
        and seg.name in set(section_names)
    )
    return ScenarioBudget(
        name=name,
        chat_mode=chat_mode,
        present_as=present_as,
        new_workbook=bool(getattr(engine, "_catalog_new_workbook", True)),
        families=tuple(sorted(getattr(engine, "_catalog_families", ()) or ())),
        section_names=section_names,
        visible_tools=tuple(sorted(visible_names)) if visible_names else (),
        wire_tools=tuple(sorted({
            (s.get("function") or {}).get("name") or s.get("name") or ""
            for s in wire_schemas
        } - {""})),
        sdk_tools=sdk_tools,
        principle_tokens=principle_tokens,
        system_tokens=count_tokens(system_text),
        tools_json_tokens=count_tokens(tools_json),
        sdk_tokens=count_tokens(sdk_text),
        discovery_tokens=count_tokens(discovery_text),
        system_text=system_text,
        tools_json=tools_json,
        sdk_text=sdk_text,
        discovery_text=discovery_text,
        over_budget_segments=over,
    )


DEFAULT_SCENARIOS: tuple[dict[str, Any], ...] = (
    {"name": "native_read", "chat_mode": "read", "present_as": "native", "has_workbook": True},
    {"name": "native_plan", "chat_mode": "plan", "present_as": "native", "has_workbook": True},
    {"name": "native_write_existing", "chat_mode": "write", "present_as": "native", "has_workbook": True},
    {"name": "native_write_new", "chat_mode": "write", "present_as": "native", "has_workbook": False},
    {"name": "code_write_existing", "chat_mode": "write", "present_as": "code", "has_workbook": True},
    {"name": "code_write_new", "chat_mode": "write", "present_as": "code", "has_workbook": False},
    {"name": "native_csv", "chat_mode": "write", "present_as": "native", "has_workbook": False, "profile": "csv"},
    {"name": "native_docx", "chat_mode": "write", "present_as": "native", "has_workbook": False, "profile": "docx"},
)

SNAPSHOT_SCENARIOS: tuple[str, ...] = (
    "native_write_new",
    "native_write_existing",
    "code_write_existing",
)


def collect_report(workspace: Path | None = None) -> BudgetReport:
    composer = PromptComposer(PROMPTS_DIR)
    composer.load_all(auto_repair=False)
    root = workspace or Path(FIXED_WORKSPACE)
    scenarios = [
        measure_scenario(workspace=root, composer=composer, **spec)
        for spec in DEFAULT_SCENARIOS
    ]
    return BudgetReport(segments=measure_segments(), scenarios=scenarios)


def format_report(report: BudgetReport) -> str:
    lines = [
        "提示词预算（o200k_base，剥离 frontmatter；静态计数）",
        "",
        f"{'段':<32} {'token':>6} {'max':>6} {'状态':>6}",
    ]
    for seg in report.segments:
        status = "超限" if seg.over_budget else ("—" if not seg.max_tokens else "ok")
        max_col = str(seg.max_tokens) if seg.max_tokens else "—"
        lines.append(f"{seg.name:<32} {seg.tokens:>6} {max_col:>6} {status:>6}")
    lines.extend(
        [
            "",
            f"{'场景':<24} {'原则+策略':>10} {'完整system':>12} {'tools JSON':>12} {'SDK':>8} {'发现':>8}",
        ]
    )
    for item in report.scenarios:
        lines.append(
            f"{item.name:<24} {item.principle_tokens:>10} {item.system_tokens:>12} "
            f"{item.tools_json_tokens:>12} {item.sdk_tokens:>8} {item.discovery_tokens:>8}"
        )
    lines.append("")
    lines.append("原则+策略 = identity/persona/领域原则/strategy 段；完整 system 含能力地图与 SDK。")
    for item in report.scenarios:
        if item.present_as == "code":
            lines.append(
                f"{item.name}: wire 工具 {len(item.wire_tools)} 个（仅 run_code）；"
                f"SDK/执行目录 {len(item.sdk_tools)} 个"
            )
    return "\n".join(lines) + "\n"


def over_budget_names(report: BudgetReport) -> list[str]:
    return [seg.name for seg in report.segments if seg.over_budget]


def render_write_prefix_for_tests(
    *,
    chat_mode: str = "write",
    present_as: str = "native",
    new_workbook: bool = True,
) -> str:
    """测试用：只走 PromptComposer，不绑完整目录。"""
    composer = PromptComposer(PROMPTS_DIR)
    composer.load_all(auto_repair=False)
    return composer.compose_system_text(
        PromptContext(chat_mode=chat_mode),
        variables=FIXED_VARS,
        present_as=present_as,
    )


def filled_persona(text: str) -> str:
    return interpolate(text, FIXED_VARS, strict=True)
