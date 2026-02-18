"""Skillpack 加载器：多目录扫描、协议兼容解析、覆盖与软校验。"""

from __future__ import annotations

from pathlib import Path
import re
from typing import Any

from excelmanus.config import ExcelManusConfig
from excelmanus.logger import get_logger
from excelmanus.skillpacks.frontmatter import (
    FrontmatterError,
    parse_frontmatter as parse_frontmatter_text,
    parse_scalar as parse_frontmatter_scalar,
    serialize_frontmatter as serialize_frontmatter_text,
)
from excelmanus.skillpacks.models import SkillCommandDispatchMode, Skillpack
from excelmanus.tools import ToolRegistry

logger = get_logger("skillpacks.loader")

_FRONTMATTER_PATTERN = re.compile(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", re.DOTALL)


class SkillpackLoaderError(Exception):
    """Skillpack 加载失败。"""


class SkillpackValidationError(SkillpackLoaderError):
    """Skillpack 内容不合法。"""


_CANONICAL_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "name": ("name",),
    "description": ("description",),
    "allowed_tools": ("allowed_tools", "allowed-tools"),
    "triggers": ("triggers",),
    "file_patterns": ("file_patterns", "file-patterns"),
    "resources": ("resources",),
    "priority": ("priority",),
    "version": ("version",),
    "disable_model_invocation": (
        "disable_model_invocation",
        "disable-model-invocation",
    ),
    "user_invocable": ("user_invocable", "user-invocable"),
    "argument_hint": ("argument_hint", "argument-hint"),
    "context": ("context",),
    "agent": ("agent",),
    "hooks": ("hooks",),
    "model": ("model",),
    "metadata": ("metadata",),
    "command_dispatch": ("command_dispatch", "command-dispatch"),
    "command_tool": ("command_tool", "command-tool"),
    "required_mcp_servers": ("required_mcp_servers", "required-mcp-servers"),
    "required_mcp_tools": ("required_mcp_tools", "required-mcp-tools"),
}


class SkillpackLoader:
    """Skillpack 扫描与加载。"""

    def __init__(self, config: ExcelManusConfig, tool_registry: ToolRegistry) -> None:
        self._config = config
        self._tool_registry = tool_registry
        self._skillpacks: dict[str, Skillpack] = {}
        self._warnings: list[str] = []

    @property
    def warnings(self) -> list[str]:
        """软校验告警列表。"""
        return list(self._warnings)

    def list_skillpacks(self) -> list[Skillpack]:
        """返回已加载 Skillpack 列表。"""
        return list(self._skillpacks.values())

    def get_skillpack(self, name: str) -> Skillpack | None:
        """按名称获取 Skillpack。"""
        return self._skillpacks.get(name)

    def get_skillpacks(self) -> dict[str, Skillpack]:
        """返回技能包映射（副本）。"""
        return {skill.name: skill for skill in self.list_skillpacks()}

    def load_all(self) -> dict[str, Skillpack]:
        """加载所有兼容目录下的 Skillpack，冲突按优先级覆盖。

        并发安全性说明：load_all 是同步方法，在 asyncio 单线程事件循环中
        不会被并发抢占，无需加锁。若未来改为 run_in_executor 执行，需补充锁保护。
        """
        self._warnings.clear()
        merged: dict[str, Skillpack] = {}

        for source, root_dir in self._iter_discovery_roots():
            source_skillpacks = self._scan_source(source=source, root_dir=root_dir)
            merged.update(source_skillpacks)

        self._skillpacks = merged
        logger.info("已加载 %d 个 Skillpack（全量发现后）", len(self._skillpacks))
        return dict(self._skillpacks)

    def _iter_discovery_roots(self) -> list[tuple[str, Path]]:
        """返回按覆盖优先级排序的扫描根目录（低优先级在前）。"""
        roots: list[tuple[str, Path]] = []
        seen: set[str] = set()

        def _append(source: str, path: Path) -> None:
            resolved = path.expanduser().resolve()
            key = f"{source}:{resolved}"
            if key in seen:
                return
            seen.add(key)
            roots.append((source, resolved))

        # 兼容旧 system 目录（最低优先级）
        _append("system", Path(self._config.skills_system_dir))

        if not self._config.skills_discovery_enabled:
            _append("user", Path(self._config.skills_user_dir))
            _append("project", Path(self._config.skills_project_dir))
            return roots

        workspace_root = Path(self._config.workspace_root).expanduser()
        if not workspace_root.is_absolute():
            workspace_root = (Path.cwd() / workspace_root).resolve()
        else:
            workspace_root = workspace_root.resolve()

        # user 级目录：低于任意 project 目录
        _append("user", Path(self._config.skills_user_dir))
        if self._config.skills_discovery_include_claude:
            _append("user", Path("~/.claude/skills"))
        if self._config.skills_discovery_include_openclaw:
            _append("user", Path("~/.openclaw/skills"))

        # ancestor .agents/skills：
        # - roots 采用“低优先级先追加，高优先级后追加”顺序；
        # - load_all 中后者会覆盖前者，因此“从远到近追加”意味着近目录优先级更高。
        # - 仅当 cwd 位于 workspace_root 内时扫描祖先链，避免越界扫描到无关目录。
        if (
            self._config.skills_discovery_include_agents
            and self._config.skills_discovery_scan_workspace_ancestors
        ):
            cursor = Path.cwd().resolve()
            in_workspace = False
            try:
                cursor.relative_to(workspace_root)
                in_workspace = True
            except ValueError:
                in_workspace = False

            if in_workspace:
                chain: list[Path] = []
                while True:
                    chain.append(cursor)
                    if cursor == workspace_root:
                        break
                    if cursor == cursor.parent:
                        break
                    cursor = cursor.parent
                chain.reverse()
                for parent in chain:
                    _append("project", parent / ".agents" / "skills")

        # project 显式目录（workspace 下），优先级最高
        _append("project", Path(self._config.skills_project_dir))
        if self._config.skills_discovery_include_agents:
            _append("project", workspace_root / ".agents" / "skills")
        if self._config.skills_discovery_include_claude:
            _append("project", workspace_root / ".claude" / "skills")
        if self._config.skills_discovery_include_openclaw:
            _append("project", workspace_root / ".openclaw" / "skills")

        for raw in self._config.skills_discovery_extra_dirs:
            _append("project", Path(raw))

        return roots

    def _scan_source(self, source: str, root_dir: Path) -> dict[str, Skillpack]:
        if not root_dir.exists():
            return {}
        if not root_dir.is_dir():
            self._append_warning(f"Skillpack 路径不是目录，已跳过: {root_dir}")
            return {}

        loaded: dict[str, Skillpack] = {}
        skill_files = sorted(
            root_dir.rglob("SKILL.md"),
            key=lambda p: str(p.relative_to(root_dir)).lower(),
        )
        for skill_md in skill_files:
            skill_dir = skill_md.parent
            try:
                skillpack = self._parse_skillpack_file(
                    source=source,
                    skill_dir=skill_dir,
                    skill_file=skill_md,
                )
            except SkillpackValidationError as exc:
                self._append_warning(f"{skill_md}: {exc}")
                continue
            loaded[skillpack.name] = skillpack
        return loaded

    def _parse_skillpack_file(
        self,
        source: str,
        skill_dir: Path,
        skill_file: Path,
    ) -> Skillpack:
        text = skill_file.read_text(encoding="utf-8")
        frontmatter, body = self._split_frontmatter(text=text, skill_file=skill_file)
        frontmatter = self._normalize_frontmatter(frontmatter)

        line_count = len(body.splitlines())
        if line_count > 500:
            self._append_warning(f"{skill_file}: 正文超过 500 行（当前 {line_count} 行）")

        name = self._get_required_str(frontmatter, "name")
        self._validate_skill_name(name)
        description = self._get_required_str(frontmatter, "description")

        allowed_tools = self._get_optional_str_list(frontmatter, "allowed_tools")
        triggers = self._get_optional_str_list(frontmatter, "triggers")
        file_patterns = self._get_optional_str_list(frontmatter, "file_patterns")
        resources = self._get_optional_str_list(frontmatter, "resources")

        priority = self._get_optional_int(frontmatter, "priority", default=0)
        version = self._get_optional_str(frontmatter, "version", default="1.0.0")
        disable_model_invocation = self._get_optional_bool(
            frontmatter,
            "disable_model_invocation",
            default=False,
        )
        user_invocable = self._get_optional_bool(
            frontmatter,
            "user_invocable",
            default=True,
        )
        argument_hint = self._get_optional_str(frontmatter, "argument_hint", default="")

        context = self._get_optional_context(frontmatter, default="normal")
        if "agent" in frontmatter:
            raise SkillpackValidationError(
                "frontmatter 字段 'agent' 已移除。"
                "请改为常规技能，并在执行阶段显式调用 "
                "`delegate_to_subagent(agent_name=...)`。"
            )
        if context != "normal":
            raise SkillpackValidationError(
                "frontmatter 字段 'context' 仅支持 normal。"
            )

        hooks = self._get_optional_dict(frontmatter, "hooks")
        model = self._get_optional_str_or_none(frontmatter, "model")
        metadata = self._get_optional_dict(frontmatter, "metadata")

        command_dispatch = self._get_optional_command_dispatch(
            frontmatter,
            default="none",
        )
        command_tool = self._get_optional_str_or_none(frontmatter, "command_tool")
        if command_dispatch == "tool" and not command_tool:
            raise SkillpackValidationError(
                "frontmatter 字段 'command_dispatch=tool' 时，'command_tool' 必填"
            )
        required_mcp_servers = self._normalize_required_mcp_servers(
            self._get_optional_str_list(frontmatter, "required_mcp_servers")
        )
        required_mcp_tools = self._normalize_required_mcp_tools(
            self._get_optional_str_list(frontmatter, "required_mcp_tools")
        )

        resource_contents = self._load_resources(
            resources=resources,
            skill_dir=skill_dir,
            skill_name=name,
        )
        self._validate_allowed_tools_soft(name=name, allowed_tools=allowed_tools)

        known_keys = set(_CANONICAL_FIELD_ALIASES.keys())
        extensions = {
            key: value
            for key, value in frontmatter.items()
            if key not in known_keys
        }

        return Skillpack(
            name=name,
            description=description,
            allowed_tools=allowed_tools,
            triggers=triggers,
            instructions=body.strip(),
            source=source,
            root_dir=str(skill_dir),
            file_patterns=file_patterns,
            resources=resources,
            priority=priority,
            version=version,
            disable_model_invocation=disable_model_invocation,
            user_invocable=user_invocable,
            argument_hint=argument_hint,
            hooks=hooks,
            model=model,
            metadata=metadata,
            command_dispatch=command_dispatch,
            command_tool=command_tool,
            required_mcp_servers=required_mcp_servers,
            required_mcp_tools=required_mcp_tools,
            extensions=extensions,
            resource_contents=resource_contents,
        )

    def _normalize_frontmatter(self, payload: dict[str, Any]) -> dict[str, Any]:
        """将 kebab/snake 字段归一到 snake_case，并保留未知字段。"""
        normalized = dict(payload)
        for canonical, aliases in _CANONICAL_FIELD_ALIASES.items():
            chosen: Any = None
            found = False
            for alias in aliases:
                if alias in payload:
                    if not found:
                        chosen = payload[alias]
                        found = True
                    elif payload[alias] != chosen:
                        raise SkillpackValidationError(
                            f"frontmatter 字段冲突: {aliases}"
                        )
                    if alias != canonical:
                        normalized.pop(alias, None)
            if found:
                normalized[canonical] = chosen
        return normalized

    @staticmethod
    def _validate_skill_name(name: str) -> None:
        if len(name) > 255:
            raise SkillpackValidationError("frontmatter 字段 'name' 长度不能超过 255")
        segments = name.split("/")
        seg_pattern = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
        for seg in segments:
            if not seg_pattern.fullmatch(seg):
                raise SkillpackValidationError(
                    "frontmatter 字段 'name' 非法，支持命名空间段模式 "
                    "[a-z0-9][a-z0-9._-]{0,63}"
                )

    def _validate_allowed_tools_soft(self, name: str, allowed_tools: list[str]) -> None:
        if not allowed_tools:
            return
        known_tools = set(self._tool_registry.get_tool_names())
        unknown_tools = sorted(
            tool
            for tool in allowed_tools
            if tool not in known_tools and not self._is_allowed_tool_selector(tool)
        )
        if unknown_tools:
            self._append_warning(
                f"Skillpack '{name}' 引用了未注册工具（软校验告警）: {', '.join(unknown_tools)}"
            )

    @staticmethod
    def _is_allowed_tool_selector(tool: str) -> bool:
        """判断是否为运行期可展开的工具选择器。"""
        normalized = tool.strip()
        if normalized == "mcp:*":
            return True
        if not normalized.startswith("mcp:"):
            return False
        parts = normalized.split(":", 2)
        if len(parts) != 3:
            return False
        server_name = parts[1].strip()
        tool_name = parts[2].strip()
        return bool(server_name and tool_name)

    @staticmethod
    def _normalize_required_mcp_servers(servers: list[str]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for raw in servers:
            name = raw.strip()
            if not name:
                continue
            if not re.fullmatch(r"[a-zA-Z0-9._-]+", name):
                raise SkillpackValidationError(
                    "frontmatter 字段 'required_mcp_servers' 存在非法 server 名称"
                )
            lower_name = name.lower()
            if lower_name in seen:
                continue
            seen.add(lower_name)
            normalized.append(name)
        return normalized

    @staticmethod
    def _normalize_required_mcp_tools(tools: list[str]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for raw in tools:
            token = raw.strip()
            if not token:
                continue
            parts = token.split(":", 1)
            if len(parts) != 2:
                raise SkillpackValidationError(
                    "frontmatter 字段 'required_mcp_tools' 必须是 server:tool 形式"
                )
            server_name = parts[0].strip()
            tool_name = parts[1].strip()
            if not server_name or not tool_name:
                raise SkillpackValidationError(
                    "frontmatter 字段 'required_mcp_tools' 不能为空"
                )
            if not re.fullmatch(r"[a-zA-Z0-9._-]+", server_name):
                raise SkillpackValidationError(
                    "frontmatter 字段 'required_mcp_tools' 的 server 名称非法"
                )
            if tool_name != "*" and not re.fullmatch(r"[a-zA-Z0-9._-]+", tool_name):
                raise SkillpackValidationError(
                    "frontmatter 字段 'required_mcp_tools' 的 tool 名称非法"
                )
            lower_token = f"{server_name.lower()}:{tool_name.lower()}"
            if lower_token in seen:
                continue
            seen.add(lower_token)
            normalized.append(f"{server_name}:{tool_name}")
        return normalized

    def _load_resources(
        self,
        resources: list[str],
        skill_dir: Path,
        skill_name: str,
    ) -> dict[str, str]:
        if not resources:
            return {}

        loaded: dict[str, str] = {}
        root = skill_dir.resolve()
        for item in resources:
            rel_path = Path(item)
            abs_path = (skill_dir / rel_path).resolve()
            try:
                abs_path.relative_to(root)
            except ValueError:
                self._append_warning(
                    f"Skillpack '{skill_name}' 资源越界，已跳过: {item}"
                )
                continue
            if not abs_path.exists() or not abs_path.is_file():
                self._append_warning(
                    f"Skillpack '{skill_name}' 资源不存在，已跳过: {item}"
                )
                continue
            loaded[item] = abs_path.read_text(encoding="utf-8")
        return loaded

    def _append_warning(self, message: str) -> None:
        self._warnings.append(message)
        logger.warning(message)

    @staticmethod
    def _split_frontmatter(text: str, skill_file: Path) -> tuple[dict[str, Any], str]:
        match = _FRONTMATTER_PATTERN.match(text)
        if not match:
            raise SkillpackValidationError(
                f"缺少 frontmatter（文件应以 --- 开始）: {skill_file}"
            )
        frontmatter_raw, body = match.groups()
        frontmatter = SkillpackLoader.parse_frontmatter(frontmatter_raw)
        return frontmatter, body

    @staticmethod
    def parse_frontmatter(raw: str) -> dict[str, Any]:
        """公开 frontmatter 解析入口。"""
        try:
            return parse_frontmatter_text(raw)
        except FrontmatterError as exc:
            raise SkillpackValidationError(str(exc))

    @staticmethod
    def parse_scalar(value: str) -> Any:
        """公开标量解析入口。"""
        return parse_frontmatter_scalar(value)

    @staticmethod
    def format_frontmatter(data: dict[str, Any]) -> str:
        """公开 frontmatter 序列化入口。"""
        try:
            return serialize_frontmatter_text(data)
        except FrontmatterError as exc:
            raise SkillpackValidationError(str(exc))

    @staticmethod
    def _get_required_str(payload: dict[str, Any], key: str) -> str:
        value = payload.get(key)
        if not isinstance(value, str) or not value.strip():
            raise SkillpackValidationError(f"frontmatter 字段 '{key}' 必须是非空字符串")
        return value.strip()

    @staticmethod
    def _get_optional_str(payload: dict[str, Any], key: str, default: str) -> str:
        value = payload.get(key, default)
        if value is None:
            return default
        if not isinstance(value, str):
            raise SkillpackValidationError(f"frontmatter 字段 '{key}' 必须是字符串")
        value = value.strip()
        return value or default

    @staticmethod
    def _get_optional_str_or_none(payload: dict[str, Any], key: str) -> str | None:
        if key not in payload:
            return None
        value = payload[key]
        if value is None:
            return None
        if not isinstance(value, str):
            raise SkillpackValidationError(f"frontmatter 字段 '{key}' 必须是字符串")
        value = value.strip()
        return value or None

    @staticmethod
    def _get_optional_str_list(payload: dict[str, Any], key: str) -> list[str]:
        if key not in payload:
            return []
        return SkillpackLoader._to_str_list(value=payload[key], key=key)

    @staticmethod
    def _to_str_list(value: Any, key: str) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            item = value.strip()
            return [item] if item else []
        if not isinstance(value, list):
            raise SkillpackValidationError(f"frontmatter 字段 '{key}' 必须是字符串列表")

        items: list[str] = []
        for item in value:
            if item is None:
                continue
            if not isinstance(item, str):
                item = str(item)
            normalized = item.strip()
            if normalized:
                items.append(normalized)
        return items

    @staticmethod
    def _get_optional_int(payload: dict[str, Any], key: str, default: int) -> int:
        value = payload.get(key, default)
        if value is None:
            return default
        if isinstance(value, bool):
            raise SkillpackValidationError(f"frontmatter 字段 '{key}' 必须是整数")
        if isinstance(value, int):
            return value
        if isinstance(value, str) and re.fullmatch(r"-?\d+", value.strip()):
            return int(value.strip())
        raise SkillpackValidationError(f"frontmatter 字段 '{key}' 必须是整数")

    @staticmethod
    def _get_optional_bool(payload: dict[str, Any], key: str, default: bool) -> bool:
        value = payload.get(key, default)
        if value is None:
            return default
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered == "true":
                return True
            if lowered == "false":
                return False
        raise SkillpackValidationError(f"frontmatter 字段 '{key}' 必须是布尔值")

    @staticmethod
    def _get_optional_dict(payload: dict[str, Any], key: str) -> dict[str, Any]:
        if key not in payload:
            return {}
        value = payload.get(key)
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise SkillpackValidationError(f"frontmatter 字段 '{key}' 必须是对象")
        return dict(value)

    @staticmethod
    def _get_optional_context(
        payload: dict[str, Any],
        key: str = "context",
        default: str = "normal",
    ) -> str:
        raw = payload.get(key, default)
        if not isinstance(raw, str):
            raise SkillpackValidationError(f"frontmatter 字段 '{key}' 必须是字符串")
        normalized = raw.strip().lower()
        if normalized == "normal":
            return normalized
        if normalized == "fork":
            raise SkillpackValidationError(
                "frontmatter 字段 'context: fork' 已移除。"
                "请改为常规技能，并在需要时显式调用 "
                "`delegate_to_subagent(agent_name=...)`。"
            )
        raise SkillpackValidationError(
            f"frontmatter 字段 '{key}' 仅支持 normal"
        )

    @staticmethod
    def _get_optional_command_dispatch(
        payload: dict[str, Any],
        key: str = "command_dispatch",
        default: SkillCommandDispatchMode = "none",
    ) -> SkillCommandDispatchMode:
        raw = payload.get(key, default)
        if not isinstance(raw, str):
            raise SkillpackValidationError(f"frontmatter 字段 '{key}' 必须是字符串")
        normalized = raw.strip().lower()
        if normalized in {"none", "tool"}:
            return normalized  # type: ignore[return-value]
        raise SkillpackValidationError(
            f"frontmatter 字段 '{key}' 必须是 none/tool"
        )
