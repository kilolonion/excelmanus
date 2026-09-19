"""提示词注册表：稳定 system 段 vs 变化才追加的 user-role 快照。

``{{var}}`` 未定义或值为空时组装失败，不得静默带着残缺提示词去请求。
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

_VAR_RE = re.compile(r"\{\{([A-Za-z_][A-Za-z0-9_]*)\}\}")

TextProvider = str | Callable[["AssembleContext"], str]


class PromptRegistryError(ValueError):
    """提示词注册 / 组装失败。"""


class UnknownPromptVariable(PromptRegistryError):
    def __init__(self, name: str) -> None:
        self.name = name
        super().__init__(f"提示词变量未定义或为空: {{{{{name}}}}}")


class DuplicatePromptName(PromptRegistryError):
    def __init__(self, layer: str, name: str) -> None:
        self.layer = layer
        self.name = name
        super().__init__(f"提示词{layer}重名: {name}")


class MultipleCompleteSection(PromptRegistryError):
    def __init__(self) -> None:
        super().__init__("至多一个 complete=True 的 system 段")


@dataclass
class AssembleContext:
    """一次 assemble 的运行时开关。"""

    plan_active: bool = False
    present_as: str = "native"  # native | code
    variables: dict[str, str] | None = None
    strict_variables: bool | None = None
    chat_mode: str = "write"
    full_access: bool = False
    sdk_section: str = ""
    visible_tools: frozenset[str] | None = None
    new_workbook: bool = True


@dataclass(frozen=True)
class AssembledSection:
    name: str
    order: int
    text: str
    complete: bool = False


@dataclass
class PromptAssembly:
    sections: list[AssembledSection] = field(default_factory=list)
    contexts: list[AssembledSection] = field(default_factory=list)
    variables: dict[str, str] = field(default_factory=dict)
    tools: list[Any] = field(default_factory=list)


@dataclass
class _Registration:
    name: str
    order: int
    provider: TextProvider
    complete: bool = False


class PromptRegistry:
    """按 name+order 注册 system 段、user-role context、变量与工具 schema。"""

    def __init__(self) -> None:
        self._sections: dict[str, _Registration] = {}
        self._contexts: dict[str, _Registration] = {}
        self._variable_providers: dict[str, Callable[[], str | None]] = {}
        self._tool_providers: list[Callable[[], list[Any]]] = []

    def section(
        self,
        name: str,
        order: int,
        text: TextProvider,
        *,
        complete: bool = False,
    ) -> None:
        if name in self._sections:
            raise DuplicatePromptName("段", name)
        self._sections[name] = _Registration(name, int(order), text, complete)

    def context(self, name: str, order: int, text: TextProvider) -> None:
        if name in self._contexts:
            raise DuplicatePromptName("context", name)
        self._contexts[name] = _Registration(name, int(order), text, False)

    def tools(self, provider: Callable[[], list[Any]]) -> None:
        self._tool_providers.append(provider)

    def variable(self, name: str, provider: Callable[[], str | None]) -> None:
        if name in self._variable_providers:
            raise DuplicatePromptName("变量", name)
        self._variable_providers[name] = provider

    def assemble(self, ctx: AssembleContext | None = None) -> PromptAssembly:
        ctx = ctx or AssembleContext()
        resolved = self._resolve_variables(ctx)
        strict = (
            ctx.strict_variables
            if ctx.strict_variables is not None
            else ctx.variables is not None
        )
        sections = self._render_layer(self._sections, ctx, resolved, strict)
        complete_count = sum(1 for sec in sections if sec.complete)
        if complete_count > 1:
            raise MultipleCompleteSection()
        contexts = self._render_layer(self._contexts, ctx, resolved, strict)
        collected_tools: list[Any] = []
        for provider in self._tool_providers:
            collected_tools.extend(provider() or [])
        return PromptAssembly(
            sections=sections,
            contexts=contexts,
            variables=resolved,
            tools=collected_tools,
        )

    def render_system(self, assembly: PromptAssembly) -> str:
        """按 order, name 拼接非空 system 段。complete 段若存在则单独作为全文。"""
        complete = [sec for sec in assembly.sections if sec.complete and sec.text.strip()]
        if complete:
            return complete[0].text.strip()
        parts = [sec.text.strip() for sec in assembly.sections if sec.text.strip()]
        return "\n\n".join(parts)

    def _resolve_variables(self, ctx: AssembleContext) -> dict[str, str]:
        resolved = dict(ctx.variables or {})
        for name, provider in self._variable_providers.items():
            if name in resolved and resolved[name]:
                continue
            value = provider()
            if value:
                resolved[name] = value
        return resolved

    def _render_layer(
        self,
        registrations: dict[str, _Registration],
        ctx: AssembleContext,
        variables: dict[str, str],
        strict: bool,
    ) -> list[AssembledSection]:
        rendered: list[AssembledSection] = []
        for reg in sorted(registrations.values(), key=lambda r: (r.order, r.name)):
            raw = reg.provider(ctx) if callable(reg.provider) else reg.provider
            text = interpolate(raw or "", variables, strict=strict)
            if not text.strip():
                continue
            rendered.append(
                AssembledSection(
                    name=reg.name,
                    order=reg.order,
                    text=text,
                    complete=reg.complete,
                )
            )
        return rendered


def interpolate(text: str, variables: dict[str, str], *, strict: bool) -> str:
    """替换 ``{{name}}``。strict 时未定义或空值抛 ``UnknownPromptVariable``。"""
    if not text:
        return text

    def _replace(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in variables:
            if strict:
                raise UnknownPromptVariable(name)
            return match.group(0)
        value = variables[name]
        if value is None or value == "":
            if strict:
                raise UnknownPromptVariable(name)
            return match.group(0)
        return str(value)

    return _VAR_RE.sub(_replace, text)
