"""Skillpack 管理服务：CRUD、原子写入与软删除归档。"""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import re
import secrets
import shutil
import threading
from typing import Any

from excelmanus.config import ExcelManusConfig
from excelmanus.skillpacks.loader import SkillpackLoader
from excelmanus.skillpacks.models import Skillpack
from excelmanus.skillpacks.pre_router import invalidate_pre_route_cache

_SEGMENT_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_REQUIRED_CREATE_FIELDS = {"description"}
_SUPPORTED_FIELDS = {
    "description",
    "allowed_tools",
    "triggers",
    "instructions",
    "file_patterns",
    "resources",
    "priority",
    "version",
    "disable_model_invocation",
    "user_invocable",
    "argument_hint",
    "hooks",
    "model",
    "metadata",
    "command_dispatch",
    "command_tool",
    "required_mcp_servers",
    "required_mcp_tools",
}

_FIELD_ALIASES = {
    "allowed-tools": "allowed_tools",
    "file-patterns": "file_patterns",
    "disable-model-invocation": "disable_model_invocation",
    "user-invocable": "user_invocable",
    "argument-hint": "argument_hint",
    "command-dispatch": "command_dispatch",
    "command-tool": "command_tool",
    "required-mcp-servers": "required_mcp_servers",
    "required-mcp-tools": "required_mcp_tools",
}

_DEFAULTS: dict[str, Any] = {
    "allowed_tools": [],
    "triggers": [],
    "file_patterns": [],
    "resources": [],
    "priority": 0,
    "version": "1.0.0",
    "disable_model_invocation": False,
    "user_invocable": True,
    "argument_hint": "",
    "hooks": {},
    "model": None,
    "metadata": {},
    "command_dispatch": "none",
    "command_tool": None,
    "required_mcp_servers": [],
    "required_mcp_tools": [],
}


class SkillpackManagerError(Exception):
    """Skillpack 管理失败。"""


class SkillpackInputError(SkillpackManagerError):
    """输入参数不合法。"""


class SkillpackNotFoundError(SkillpackManagerError):
    """Skillpack 不存在。"""


class SkillpackConflictError(SkillpackManagerError):
    """操作与当前状态冲突。"""


class SkillpackManager:
    """统一管理 project 层 skillpack 的写入与归档。"""

    def __init__(self, config: ExcelManusConfig, loader: SkillpackLoader) -> None:
        self._config = config
        self._loader = loader
        self._lock = threading.Lock()
        workspace = Path(config.workspace_root).expanduser()
        if not workspace.is_absolute():
            workspace = (Path.cwd() / workspace).resolve()
        else:
            workspace = workspace.resolve()
        self._workspace_root = workspace
        self._project_dir = self._resolve_path(config.skills_project_dir)
        self._archive_root = self._workspace_root / ".excelmanus" / "skillpacks_archive"

        self._ensure_in_workspace(self._project_dir, "skills_project_dir")

    def list_skillpacks(self) -> list[dict[str, Any]]:
        skillpacks = self._ensure_loaded()
        details = [self._skill_to_detail(skill) for skill in skillpacks.values()]
        return sorted(details, key=lambda item: str(item["name"]).lower())

    def get_skillpack(self, name: str) -> dict[str, Any]:
        normalized = self._validate_skill_name(name)
        skillpacks = self._ensure_loaded()
        resolved_name = self._resolve_skill_name(normalized, skillpacks)
        if resolved_name is None:
            raise SkillpackNotFoundError(f"未找到 Skillpack `{normalized}`。")
        return self._skill_to_detail(skillpacks[resolved_name])

    def create_skillpack(
        self,
        *,
        name: str,
        payload: dict[str, Any],
        actor: str,
    ) -> dict[str, Any]:
        normalized_name = self._validate_skill_name(name)
        normalized_payload = self._normalize_payload(payload, for_create=True)
        with self._lock:
            skillpacks = self._ensure_loaded()
            existing_name = self._resolve_skill_name(normalized_name, skillpacks)
            if (
                existing_name is not None
                and skillpacks[existing_name].source == "project"
            ):
                raise SkillpackConflictError(
                    f"Skillpack `{normalized_name}` 在 project 层已存在。"
                )
            content = self._build_skill_file_content(
                name=normalized_name,
                payload=normalized_payload,
                base=None,
            )
            skill_dir = self._project_dir / normalized_name
            skill_file = skill_dir / "SKILL.md"
            skill_dir.mkdir(parents=True, exist_ok=True)
            self._atomic_write_text(skill_file, content)
            self._loader.load_all()
            invalidate_pre_route_cache()
        return self.get_skillpack(normalized_name)

    def patch_skillpack(
        self,
        *,
        name: str,
        payload: dict[str, Any],
        actor: str,
    ) -> dict[str, Any]:
        normalized_name = self._validate_skill_name(name)
        normalized_payload = self._normalize_payload(payload, for_create=False)
        with self._lock:
            skillpacks = self._ensure_loaded()
            resolved_name = self._resolve_skill_name(normalized_name, skillpacks)
            if resolved_name is None:
                raise SkillpackNotFoundError(f"未找到 Skillpack `{normalized_name}`。")
            skill = skillpacks[resolved_name]
            if skill.source != "project":
                raise SkillpackConflictError(
                    f"Skillpack `{resolved_name}` 来源为 `{skill.source}`，"
                    "请先在 project 层创建同名覆盖版本。"
                )
            content = self._build_skill_file_content(
                name=skill.name,
                payload=normalized_payload,
                base=skill,
            )
            skill_dir = self._project_dir / skill.name
            skill_file = skill_dir / "SKILL.md"
            if not skill_file.exists():
                raise SkillpackNotFoundError(
                    f"project 层 Skillpack 文件不存在：`{skill_file}`。"
                )
            self._atomic_write_text(skill_file, content)
            self._loader.load_all()
            invalidate_pre_route_cache()
        return self.get_skillpack(skill.name)

    def delete_skillpack(
        self,
        *,
        name: str,
        actor: str,
        reason: str = "",
    ) -> dict[str, Any]:
        normalized_name = self._validate_skill_name(name)
        with self._lock:
            skillpacks = self._ensure_loaded()
            resolved_name = self._resolve_skill_name(normalized_name, skillpacks)
            if resolved_name is None:
                raise SkillpackNotFoundError(f"未找到 Skillpack `{normalized_name}`。")
            skill = skillpacks[resolved_name]
            if skill.source != "project":
                raise SkillpackConflictError(
                    f"Skillpack `{resolved_name}` 来源为 `{skill.source}`，"
                    "仅支持删除 project 层技能。"
                )

            src_dir = self._project_dir / skill.name
            if not src_dir.exists():
                raise SkillpackNotFoundError(f"待删除目录不存在：`{src_dir}`。")

            deleted_at = datetime.now(timezone.utc)
            month_bucket = deleted_at.strftime("%Y-%m")
            timestamp = deleted_at.strftime("%Y%m%dT%H%M%SZ")
            archive_dir = (
                self._archive_root
                / month_bucket
                / f"{skill.name}__{timestamp}"
            )
            archive_dir.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src_dir), str(archive_dir))

            meta = {
                "name": skill.name,
                "source_before_delete": skill.source,
                "original_dir": self._to_workspace_rel(src_dir),
                "archived_dir": self._to_workspace_rel(archive_dir),
                "deleted_at_utc": deleted_at.isoformat(),
                "reason": reason,
                "actor": actor,
            }
            (archive_dir / "meta.json").write_text(
                json.dumps(meta, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            self._loader.load_all()
            invalidate_pre_route_cache()

        return {
            "name": skill.name,
            "archived_dir": self._to_workspace_rel(archive_dir),
            "deleted_at_utc": deleted_at.isoformat(),
            "actor": actor,
        }

    def _ensure_loaded(self) -> dict[str, Skillpack]:
        skillpacks = self._loader.get_skillpacks()
        if not skillpacks:
            skillpacks = self._loader.load_all()
        return skillpacks

    @staticmethod
    def _resolve_skill_name(
        name: str,
        skillpacks: dict[str, Skillpack],
    ) -> str | None:
        if name in skillpacks:
            return name
        lowered = name.lower()
        for key in skillpacks.keys():
            if key.lower() == lowered:
                return key
        return None

    def _build_skill_file_content(
        self,
        *,
        name: str,
        payload: dict[str, Any],
        base: Skillpack | None,
    ) -> str:
        merged = self._base_payload(base=base)
        merged.update(payload)

        instructions = str(merged.pop("instructions", "")).strip()
        if not instructions:
            instructions = "测试说明"

        if merged.get("command_dispatch") == "tool" and not merged.get("command_tool"):
            raise SkillpackInputError("`command_dispatch=tool` 时必须提供 `command_tool`。")

        frontmatter = self._to_frontmatter_dict(name=name, payload=merged)
        frontmatter_text = SkillpackLoader.format_frontmatter(frontmatter)
        return f"---\n{frontmatter_text}\n---\n{instructions}\n"

    def _base_payload(self, *, base: Skillpack | None) -> dict[str, Any]:
        if base is None:
            return {
                "description": "",
                "instructions": "",
                **_DEFAULTS,
            }
        return {
            "description": base.description,
            "allowed_tools": list(base.allowed_tools),
            "triggers": list(base.triggers),
            "instructions": base.instructions,
            "file_patterns": list(base.file_patterns),
            "resources": list(base.resources),
            "priority": base.priority,
            "version": base.version,
            "disable_model_invocation": base.disable_model_invocation,
            "user_invocable": base.user_invocable,
            "argument_hint": base.argument_hint,
            "hooks": dict(base.hooks),
            "model": base.model,
            "metadata": dict(base.metadata),
            "command_dispatch": base.command_dispatch,
            "command_tool": base.command_tool,
            "required_mcp_servers": list(base.required_mcp_servers),
            "required_mcp_tools": list(base.required_mcp_tools),
        }

    def _to_frontmatter_dict(self, *, name: str, payload: dict[str, Any]) -> dict[str, Any]:
        frontmatter: dict[str, Any] = {
            "name": name,
            "description": payload["description"],
        }
        allowed_tools = list(payload.get("allowed_tools", []) or [])
        triggers = list(payload.get("triggers", []) or [])
        if allowed_tools:
            frontmatter["allowed-tools"] = allowed_tools
        if triggers:
            frontmatter["triggers"] = triggers

        if payload.get("file_patterns"):
            frontmatter["file-patterns"] = payload["file_patterns"]
        if payload.get("resources"):
            frontmatter["resources"] = payload["resources"]
        if int(payload.get("priority", 0)) != 0:
            frontmatter["priority"] = int(payload["priority"])

        version = str(payload.get("version", "1.0.0") or "").strip()
        if version and version != "1.0.0":
            frontmatter["version"] = version

        if bool(payload.get("disable_model_invocation", False)):
            frontmatter["disable-model-invocation"] = True
        if not bool(payload.get("user_invocable", True)):
            frontmatter["user-invocable"] = False

        argument_hint = str(payload.get("argument_hint", "") or "").strip()
        if argument_hint:
            frontmatter["argument-hint"] = argument_hint

        hooks = payload.get("hooks")
        if isinstance(hooks, dict) and hooks:
            frontmatter["hooks"] = hooks

        model = payload.get("model")
        if isinstance(model, str) and model.strip():
            frontmatter["model"] = model.strip()

        metadata = payload.get("metadata")
        if isinstance(metadata, dict) and metadata:
            frontmatter["metadata"] = metadata

        command_dispatch = str(payload.get("command_dispatch", "none") or "none").strip().lower()
        if command_dispatch != "none":
            frontmatter["command-dispatch"] = command_dispatch
            command_tool = payload.get("command_tool")
            if isinstance(command_tool, str) and command_tool.strip():
                frontmatter["command-tool"] = command_tool.strip()
        required_mcp_servers = list(payload.get("required_mcp_servers", []) or [])
        required_mcp_tools = list(payload.get("required_mcp_tools", []) or [])
        if required_mcp_servers:
            frontmatter["required-mcp-servers"] = required_mcp_servers
        if required_mcp_tools:
            frontmatter["required-mcp-tools"] = required_mcp_tools

        return frontmatter

    def _normalize_payload(self, payload: dict[str, Any], *, for_create: bool) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise SkillpackInputError("payload 必须为对象。")

        if "name" in payload:
            raise SkillpackInputError("不允许在 payload 中传入 `name` 字段。")

        # 别名归一
        canonical: dict[str, Any] = {}
        for key, value in payload.items():
            mapped = _FIELD_ALIASES.get(key, key)
            if mapped in canonical and canonical[mapped] != value:
                raise SkillpackInputError(f"字段冲突：`{key}` 与同义字段值不一致。")
            canonical[mapped] = value

        if "context" in canonical:
            context_raw = str(canonical.get("context", "")).strip().lower()
            if context_raw == "fork":
                raise SkillpackInputError(
                    "`context: fork` 已移除。请改为常规技能，并在运行时显式调用 "
                    "`delegate_to_subagent(agent_name=...)`。"
                )
            raise SkillpackInputError("`context` 字段已移除，不再支持传入。")
        if "agent" in canonical:
            raise SkillpackInputError(
                "`agent` 字段已移除。请在运行时显式调用 "
                "`delegate_to_subagent(agent_name=...)`。"
            )

        keys = set(canonical.keys())
        unknown = sorted(keys - _SUPPORTED_FIELDS)
        if unknown:
            raise SkillpackInputError(f"存在不支持字段: {', '.join(unknown)}")

        if for_create:
            missing = sorted(_REQUIRED_CREATE_FIELDS - keys)
            if missing:
                raise SkillpackInputError(f"创建缺少必填字段: {', '.join(missing)}")
        elif not keys:
            raise SkillpackInputError("patch 至少需要一个字段。")

        normalized: dict[str, Any] = {}
        for key, value in canonical.items():
            if key in {"description", "version", "argument_hint", "instructions"}:
                normalized[key] = self._normalize_str(
                    key,
                    value,
                    allow_empty=(key in {"argument_hint", "instructions"}),
                )
            elif key in {"model", "command_tool"}:
                normalized[key] = self._normalize_optional_str(key, value)
            elif key in {
                "allowed_tools",
                "triggers",
                "file_patterns",
                "resources",
                "required_mcp_servers",
                "required_mcp_tools",
            }:
                values = self._normalize_str_list(key, value)
                if key == "resources":
                    self._validate_resource_paths(values)
                normalized[key] = values
            elif key in {"disable_model_invocation", "user_invocable"}:
                normalized[key] = self._normalize_bool(key, value)
            elif key == "priority":
                normalized[key] = self._normalize_int(key, value)
            elif key == "command_dispatch":
                mode = self._normalize_str(key, value, allow_empty=False).lower()
                if mode not in {"none", "tool"}:
                    raise SkillpackInputError("`command_dispatch` 只能是 none 或 tool。")
                normalized[key] = mode
            elif key in {"hooks", "metadata"}:
                if value is None:
                    normalized[key] = {}
                elif isinstance(value, dict):
                    normalized[key] = value
                else:
                    raise SkillpackInputError(f"`{key}` 必须为对象。")
            else:
                raise SkillpackInputError(f"不支持字段 `{key}`。")

        return normalized

    @staticmethod
    def _normalize_bool(key: str, value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered in {"true", "1", "yes", "on"}:
                return True
            if lowered in {"false", "0", "no", "off"}:
                return False
        raise SkillpackInputError(f"`{key}` 必须为布尔值。")

    @staticmethod
    def _normalize_int(key: str, value: Any) -> int:
        if isinstance(value, bool):
            raise SkillpackInputError(f"`{key}` 必须为整数。")
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.strip().lstrip("-").isdigit():
            return int(value.strip())
        raise SkillpackInputError(f"`{key}` 必须为整数。")

    @staticmethod
    def _normalize_str(key: str, value: Any, *, allow_empty: bool) -> str:
        if not isinstance(value, str):
            raise SkillpackInputError(f"`{key}` 必须为字符串。")
        text = value.strip()
        if not text and not allow_empty:
            raise SkillpackInputError(f"`{key}` 不能为空。")
        return text

    @staticmethod
    def _normalize_optional_str(key: str, value: Any) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise SkillpackInputError(f"`{key}` 必须为字符串。")
        text = value.strip()
        return text or None

    @staticmethod
    def _normalize_str_list(key: str, value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            text = value.strip()
            return [text] if text else []
        if not isinstance(value, list):
            raise SkillpackInputError(f"`{key}` 必须为字符串数组。")
        normalized: list[str] = []
        for item in value:
            if item is None:
                continue
            if not isinstance(item, str):
                item = str(item)
            text = item.strip()
            if text:
                normalized.append(text)
        return normalized

    @staticmethod
    def _validate_resource_paths(resources: list[str]) -> None:
        for item in resources:
            path_obj = Path(item)
            if path_obj.is_absolute():
                raise SkillpackInputError("`resources` 不允许绝对路径。")
            if ".." in path_obj.parts:
                raise SkillpackInputError("`resources` 不允许包含 `..`。")

    def _atomic_write_text(self, target: Path, content: str) -> None:
        tmp_name = f".{target.name}.{secrets.token_hex(4)}.tmp"
        tmp_path = target.with_name(tmp_name)
        try:
            tmp_path.write_text(content, encoding="utf-8")
            tmp_path.replace(target)
        except Exception:
            try:
                tmp_path.unlink(missing_ok=True)
            except Exception:
                pass
            raise

    @staticmethod
    def _validate_skill_name(name: str) -> str:
        normalized = (name or "").strip()
        if not normalized:
            raise SkillpackInputError("技能名不能为空。")
        if len(normalized) > 255:
            raise SkillpackInputError("技能名长度不能超过 255。")
        for segment in normalized.split("/"):
            if not _SEGMENT_PATTERN.fullmatch(segment):
                raise SkillpackInputError(
                    "技能名不合法，段模式需匹配 "
                    "`[a-z0-9][a-z0-9._-]{0,63}`，支持命名空间 `/`。"
                )
        return normalized

    def _skill_to_detail(self, skill: Skillpack) -> dict[str, Any]:
        detail = {
            "name": skill.name,
            "description": skill.description,
            "allowed_tools": list(skill.allowed_tools),
            "triggers": list(skill.triggers),
            "instructions": skill.instructions,
            "source": skill.source,
            "writable": skill.source == "project",
            "file_patterns": list(skill.file_patterns),
            "resources": list(skill.resources),
            "priority": skill.priority,
            "version": skill.version,
            "disable_model_invocation": skill.disable_model_invocation,
            "user_invocable": skill.user_invocable,
            "argument_hint": skill.argument_hint,
            "hooks": dict(skill.hooks),
            "model": skill.model,
            "metadata": dict(skill.metadata),
            "command_dispatch": skill.command_dispatch,
            "command_tool": skill.command_tool,
            "required_mcp_servers": list(skill.required_mcp_servers),
            "required_mcp_tools": list(skill.required_mcp_tools),
            "extensions": dict(skill.extensions),
            "resource_contents": dict(skill.resource_contents),
        }
        # 对外补充标准别名键
        detail["allowed-tools"] = detail["allowed_tools"]
        detail["file-patterns"] = detail["file_patterns"]
        detail["disable-model-invocation"] = detail["disable_model_invocation"]
        detail["user-invocable"] = detail["user_invocable"]
        detail["argument-hint"] = detail["argument_hint"]
        detail["command-dispatch"] = detail["command_dispatch"]
        detail["command-tool"] = detail["command_tool"]
        detail["required-mcp-servers"] = detail["required_mcp_servers"]
        detail["required-mcp-tools"] = detail["required_mcp_tools"]
        return detail

    def _resolve_path(self, path_str: str) -> Path:
        raw = Path(path_str).expanduser()
        if raw.is_absolute():
            return raw.resolve()
        return (self._workspace_root / raw).resolve()

    def _ensure_in_workspace(self, path: Path, label: str) -> None:
        try:
            path.relative_to(self._workspace_root)
        except ValueError:
            raise SkillpackInputError(
                f"{label} 必须位于 workspace_root 内，当前值: {path}"
            )

    def _to_workspace_rel(self, path: Path) -> str:
        try:
            return str(path.resolve().relative_to(self._workspace_root))
        except ValueError:
            return str(path.resolve())
