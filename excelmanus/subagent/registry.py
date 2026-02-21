"""子代理注册与发现。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from excelmanus.config import ExcelManusConfig
from excelmanus.logger import get_logger
from excelmanus.skillpacks.loader import SkillpackLoader, SkillpackValidationError
from excelmanus.subagent.builtin import BUILTIN_SUBAGENTS
from excelmanus.subagent.models import (
    SubagentCapabilityMode,
    SubagentConfig,
    SubagentMemoryScope,
    SubagentPermissionMode,
)

logger = get_logger("subagent.registry")

_VALID_PERMISSION_MODES: set[str] = {"default", "acceptEdits", "readOnly", "dontAsk"}
_VALID_CAPABILITY_MODES: set[str] = {"restricted", "full"}
_VALID_MEMORY_SCOPES: set[str] = {"user", "project"}
_MEMORY_SCOPE_KEYS: tuple[str, ...] = ("memory_scope", "memory-scope", "memory")
_SUBAGENT_NAME_ALIASES: dict[str, str] = {
    # 所有旧角色名映射到通用 subagent
    "explore": "subagent",
    "explorer": "subagent",
    "plan": "subagent",
    "planner": "subagent",
    "analyst": "subagent",
    "general-purpose": "subagent",
    "generalpurpose": "subagent",
    "writer": "subagent",
    "coder": "subagent",
    "full": "subagent",
}


class SubagentRegistry:
    """子代理注册表，支持 builtin/user/project 三层覆盖。"""

    def __init__(self, config: ExcelManusConfig) -> None:
        self._config = config
        self._agents: dict[str, SubagentConfig] = {}
        self._user_dir = Path(config.subagent_user_dir).expanduser()
        self._project_dir = Path(config.subagent_project_dir).expanduser()

    def load_all(self) -> dict[str, SubagentConfig]:
        """加载全部子代理。覆盖优先级：project > user > builtin。"""
        merged: dict[str, SubagentConfig] = dict(BUILTIN_SUBAGENTS)
        self._load_from_dir(self._user_dir, source="user", merged=merged)
        self._load_from_dir(self._project_dir, source="project", merged=merged)
        self._agents = merged
        return dict(self._agents)

    def get(self, name: str) -> SubagentConfig | None:
        """按名称获取子代理。"""
        if not self._agents:
            self.load_all()
        normalized = (name or "").strip()
        if not normalized:
            return None
        alias = _SUBAGENT_NAME_ALIASES.get(normalized.lower(), normalized)
        if alias in self._agents:
            return self._agents[alias]
        lowered = alias.lower()
        for candidate, config in self._agents.items():
            if candidate.lower() == lowered:
                return config
        return None

    def list_all(self) -> list[SubagentConfig]:
        """返回全部子代理（按名称排序）。"""
        if not self._agents:
            self.load_all()
        return [self._agents[name] for name in sorted(self._agents)]

    def build_catalog(self) -> tuple[str, list[str]]:
        """生成子代理目录文本和名称列表。"""
        agents = self.list_all()
        if not agents:
            return ("", [])
        lines = ["可用子代理：\n"]
        names: list[str] = []
        for agent in agents:
            names.append(agent.name)
            lines.append(f"- {agent.name}：{agent.description}")
        return ("\n".join(lines), names)

    def _load_from_dir(
        self,
        root_dir: Path,
        *,
        source: str,
        merged: dict[str, SubagentConfig],
    ) -> None:
        if not root_dir.exists() or not root_dir.is_dir():
            return
        for md_file in sorted(root_dir.glob("*.md"), key=lambda p: p.name.lower()):
            try:
                config = self._parse_agent_file(md_file, source=source)
                merged[config.name] = config
            except Exception:
                logger.warning("解析子代理失败: %s", md_file, exc_info=True)

    def _parse_agent_file(self, path: Path, *, source: str) -> SubagentConfig:
        """解析子代理 Markdown 文件（frontmatter + body）。"""
        text = path.read_text(encoding="utf-8")
        frontmatter, body = SkillpackLoader._split_frontmatter(text=text, skill_file=path)

        name = SubagentRegistry._as_str(frontmatter.get("name"), default=path.stem)
        description = SubagentRegistry._as_str(frontmatter.get("description"), default="")
        model = SubagentRegistry._as_optional_str(frontmatter.get("model"))
        api_key = SubagentRegistry._as_optional_str(frontmatter.get("api_key"))
        base_url = SubagentRegistry._as_optional_str(frontmatter.get("base_url"))

        allowed_tools = SubagentRegistry._as_tool_list(frontmatter.get("tools"))
        disallowed_tools = SubagentRegistry._as_tool_list(frontmatter.get("disallowedTools"))

        permission_mode_raw = SubagentRegistry._as_str(
            frontmatter.get("permissionMode"),
            default="default",
        )
        if permission_mode_raw not in _VALID_PERMISSION_MODES:
            raise SkillpackValidationError(
                f"permissionMode 非法: {permission_mode_raw!r}，必须是 {_VALID_PERMISSION_MODES}"
            )
        permission_mode: SubagentPermissionMode = permission_mode_raw  # type: ignore[assignment]

        memory_scope_raw = SubagentRegistry._extract_memory_scope(frontmatter)
        memory_scope: SubagentMemoryScope | None = None
        if memory_scope_raw:
            if memory_scope_raw not in _VALID_MEMORY_SCOPES:
                raise SkillpackValidationError(
                    f"memory_scope 非法: {memory_scope_raw!r}，必须是 {_VALID_MEMORY_SCOPES}"
                )
            memory_scope = memory_scope_raw  # type: ignore[assignment]

        capability_mode_raw = SubagentRegistry._as_str(
            frontmatter.get("capability_mode"),
            default="restricted",
        )
        if capability_mode_raw not in _VALID_CAPABILITY_MODES:
            raise SkillpackValidationError(
                f"capability_mode 非法: {capability_mode_raw!r}，必须是 {_VALID_CAPABILITY_MODES}"
            )

        max_iterations = SubagentRegistry._as_int(
            frontmatter.get("max_iterations"),
            default=self._config.subagent_max_iterations,
        )
        max_failures = SubagentRegistry._as_int(
            frontmatter.get("max_consecutive_failures"),
            default=self._config.subagent_max_consecutive_failures,
        )

        skills = SubagentRegistry._as_str_list(frontmatter.get("skills"))

        return SubagentConfig(
            name=name,
            description=description,
            model=model,
            api_key=api_key,
            base_url=base_url,
            allowed_tools=allowed_tools,
            disallowed_tools=disallowed_tools,
            permission_mode=permission_mode,
            max_iterations=max_iterations,
            max_consecutive_failures=max_failures,
            skills=skills,
            memory_scope=memory_scope,
            capability_mode=capability_mode_raw,  # type: ignore[arg-type]
            source=source,  # type: ignore[arg-type]
            system_prompt=body.strip(),
        )

    @staticmethod
    def _as_str(value: Any, *, default: str = "") -> str:
        if value is None:
            return default
        text = str(value).strip()
        return text if text else default

    @staticmethod
    def _as_optional_str(value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @staticmethod
    def _extract_memory_scope(frontmatter: dict[str, Any]) -> str | None:
        values: list[tuple[str, str]] = []
        for key in _MEMORY_SCOPE_KEYS:
            value = SubagentRegistry._as_optional_str(frontmatter.get(key))
            if value:
                values.append((key, value))

        if not values:
            return None

        normalized = {value for _, value in values}
        if len(normalized) > 1:
            conflict = ", ".join(f"{key}={value}" for key, value in values)
            raise SkillpackValidationError(
                f"memory_scope 配置冲突: {conflict}"
            )
        return values[0][1]

    @staticmethod
    def _as_int(value: Any, *, default: int) -> int:
        if value is None:
            return default
        # YAML 可能将 120.0 解析为 float，若是整数值则直接转换
        if isinstance(value, float) and value.is_integer():
            value = int(value)
        if isinstance(value, int) and not isinstance(value, bool):
            if value <= 0:
                raise SkillpackValidationError("整数配置必须大于 0")
            return value
        text = str(value).strip()
        if not text or (not text.lstrip("-").isdigit()):
            raise SkillpackValidationError(f"无效整数配置: {value!r}")
        result = int(text)
        if result <= 0:
            raise SkillpackValidationError("整数配置必须大于 0")
        return result

    @staticmethod
    def _as_str_list(value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            text = value.strip()
            return [text] if text else []
        if not isinstance(value, list):
            raise SkillpackValidationError("列表字段必须是字符串列表")
        items: list[str] = []
        for item in value:
            text = str(item).strip()
            if text:
                items.append(text)
        return items

    @staticmethod
    def _as_tool_list(value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        if not isinstance(value, list):
            raise SkillpackValidationError("tools/disallowedTools 必须是字符串列表")
        tools: list[str] = []
        for item in value:
            text = str(item).strip()
            if text:
                tools.append(text)
        return tools
