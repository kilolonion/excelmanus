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
from excelmanus.skillpacks.clawhub import (
    ClawHubClient,
)
from excelmanus.skillpacks.clawhub_lockfile import ClawHubLockfile
from excelmanus.skillpacks.importer import (
    import_from_github_url,
    import_from_local_path,
)
from excelmanus.skillpacks.loader import SkillpackLoader
from excelmanus.skillpacks.models import Skillpack

_SEGMENT_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_REQUIRED_CREATE_FIELDS = {"description"}
_SUPPORTED_FIELDS = {
    "description",
    "instructions",
    "file_patterns",
    "resources",
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
    "file_patterns": [],
    "resources": [],
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
    """统一管理 project 层 skillpack 的写入与归档。

    当提供 ``user_skill_dir`` 时，用户通过 API 创建的技能将写入
    该目录（per-user 隔离），而非共享的 project 目录。
    """

    def __init__(
        self,
        config: ExcelManusConfig,
        loader: SkillpackLoader,
        *,
        user_skill_dir: Path | None = None,
    ) -> None:
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
        # per-user 技能写入目录（为 None 时回退到 project_dir）
        self._user_skill_dir: Path | None = (
            user_skill_dir.resolve() if user_skill_dir is not None else None
        )
        if self._user_skill_dir is not None:
            self._user_skill_dir.mkdir(parents=True, exist_ok=True)
        self._archive_root = self._workspace_root / ".excelmanus" / "skillpacks_archive"

        self._ensure_in_workspace(self._project_dir, "skills_project_dir")

        # ClawHub 集成
        self._clawhub_client: ClawHubClient | None = None
        self._clawhub_lockfile: ClawHubLockfile | None = None
        if config.clawhub_enabled:
            self._clawhub_client = ClawHubClient(
                registry_url=config.clawhub_registry_url,
                prefer_cli=config.clawhub_prefer_cli,
            )
            self._clawhub_lockfile = ClawHubLockfile(workspace)

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
        write_dir = self._write_target_dir()
        with self._lock:
            skillpacks = self._ensure_loaded()
            existing_name = self._resolve_skill_name(normalized_name, skillpacks)
            if existing_name is not None:
                existing_source = skillpacks[existing_name].source
                if existing_source in ("project", "user"):
                    raise SkillpackConflictError(
                        f"Skillpack `{normalized_name}` 在 {existing_source} 层已存在。"
                    )
            content = self._build_skill_file_content(
                name=normalized_name,
                payload=normalized_payload,
                base=None,
            )
            skill_dir = write_dir / normalized_name
            skill_file = skill_dir / "SKILL.md"
            skill_dir.mkdir(parents=True, exist_ok=True)
            self._atomic_write_text(skill_file, content)
            self._loader.load_all()
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
            if skill.source not in ("project", "user"):
                raise SkillpackConflictError(
                    f"Skillpack `{resolved_name}` 来源为 `{skill.source}`，"
                    "仅支持修改 project/user 层技能。"
                )
            content = self._build_skill_file_content(
                name=skill.name,
                payload=normalized_payload,
                base=skill,
            )
            # 定位技能所在的实际目录
            skill_dir = self._locate_skill_dir(skill)
            skill_file = skill_dir / "SKILL.md"
            if not skill_file.exists():
                raise SkillpackNotFoundError(
                    f"Skillpack 文件不存在：`{skill_file}`。"
                )
            self._atomic_write_text(skill_file, content)
            self._loader.load_all()
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
            if skill.source not in ("project", "user"):
                raise SkillpackConflictError(
                    f"Skillpack `{resolved_name}` 来源为 `{skill.source}`，"
                    "仅支持删除 project/user 层技能。"
                )

            src_dir = self._locate_skill_dir(skill)
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

        return {
            "name": skill.name,
            "archived_dir": self._to_workspace_rel(archive_dir),
            "deleted_at_utc": deleted_at.isoformat(),
            "actor": actor,
        }

    def import_skillpack(
        self,
        *,
        source: str,
        value: str,
        actor: str,
        overwrite: bool = False,
    ) -> dict[str, Any]:
        """从本地路径导入 SKILL.md 及附属资源（同步）。

        Args:
            source: 必须为 "local_path"。
            value: SKILL.md 文件的绝对路径。
            actor: 操作者标识。
            overwrite: 是否覆盖已存在的同名技能。

        Returns:
            导入结果字典。
        """
        if source != "local_path":
            raise SkillpackInputError(
                f"同步导入仅支持 local_path，收到：{source}"
            )
        with self._lock:
            result = import_from_local_path(
                skill_md_path=value,
                project_skills_dir=str(self._project_dir),
                overwrite=overwrite,
            )
            self._loader.load_all()
        return result.to_dict()

    async def import_skillpack_async(
        self,
        *,
        source: str,
        value: str,
        actor: str,
        overwrite: bool = False,
        version: str | None = None,
    ) -> dict[str, Any]:
        """从本地路径或 GitHub URL 导入 SKILL.md 及附属资源（异步）。

        Args:
            source: "local_path" 或 "github_url"。
            value: SKILL.md 路径或 GitHub URL。
            actor: 操作者标识。
            overwrite: 是否覆盖已存在的同名技能。
            version: 可选，预缓存的版本号（仅 clawhub 源有效，跳过版本解析）。

        Returns:
            导入结果字典。
        """
        if source == "local_path":
            return self.import_skillpack(
                source=source, value=value, actor=actor, overwrite=overwrite,
            )
        if source == "github_url":
            result = await import_from_github_url(
                url=value,
                project_skills_dir=str(self._project_dir),
                overwrite=overwrite,
            )
            self._loader.load_all()
            return result.to_dict()
        if source == "clawhub":
            return await self._import_from_clawhub(
                slug=value, actor=actor, overwrite=overwrite, version=version,
            )
        raise SkillpackInputError(
            f"不支持的导入来源：{source}（支持 local_path / github_url / clawhub）"
        )

    # ── ClawHub 操作 ─────────────────────────────────────

    def _require_clawhub(self) -> tuple[ClawHubClient, ClawHubLockfile]:
        if not self._clawhub_client or not self._clawhub_lockfile:
            raise SkillpackManagerError("ClawHub 未启用，请设置 EXCELMANUS_CLAWHUB_ENABLED=true")
        return self._clawhub_client, self._clawhub_lockfile

    async def clawhub_search(
        self, query: str, *, limit: int = 15
    ) -> list[dict[str, Any]]:
        """搜索 ClawHub 技能。"""
        client, _ = self._require_clawhub()
        results = await client.search(query, limit=limit)
        return [
            {
                "slug": r.slug,
                "display_name": r.display_name,
                "summary": r.summary,
                "version": r.version,
                "score": r.score,
                "updated_at": r.updated_at,
            }
            for r in results
        ]

    async def clawhub_skill_detail(self, slug: str) -> dict[str, Any]:
        """获取 ClawHub 技能详情。"""
        client, _ = self._require_clawhub()
        detail = await client.get_skill(slug)
        return {
            "slug": detail.slug,
            "display_name": detail.display_name,
            "summary": detail.summary,
            "tags": detail.tags,
            "latest_version": detail.latest_version,
            "latest_changelog": detail.latest_changelog,
            "owner_handle": detail.owner_handle,
            "owner_display_name": detail.owner_display_name,
            "stats": detail.stats,
            "created_at": detail.created_at,
            "updated_at": detail.updated_at,
        }

    async def _import_from_clawhub(
        self,
        slug: str,
        actor: str,
        overwrite: bool = False,
        version: str | None = None,
    ) -> dict[str, Any]:
        """从 ClawHub 安装技能。"""
        client, lockfile = self._require_clawhub()
        with self._lock:
            resolved_version, files = await client.download_and_extract(
                slug=slug,
                dest_dir=self._project_dir,
                version=version,
                overwrite=overwrite,
            )
            lockfile.add(slug, resolved_version)
            skill_dir = self._project_dir / slug
            loaded = self._loader.load_single(skill_dir, source="project")
            if loaded is None:
                self._loader.load_all()
        return {
            "name": slug,
            "description": loaded.description if loaded else "",
            "source_type": "clawhub",
            "files_copied": files,
            "dest_dir": str(self._project_dir / slug),
            "version": resolved_version,
        }

    async def clawhub_check_updates(self) -> list[dict[str, Any]]:
        """检查已安装 ClawHub 技能的可用更新。"""
        client, lockfile = self._require_clawhub()
        installed = lockfile.get_installed()
        if not installed:
            return []
        updates = await client.check_updates(installed)
        return [
            {
                "slug": u.slug,
                "installed_version": u.installed_version,
                "latest_version": u.latest_version,
                "update_available": u.update_available,
            }
            for u in updates
        ]

    async def clawhub_update(
        self,
        slug: str | None = None,
        *,
        version: str | None = None,
        update_all: bool = False,
    ) -> list[dict[str, Any]]:
        """更新 ClawHub 技能。"""
        client, lockfile = self._require_clawhub()
        installed = lockfile.get_installed()

        if update_all:
            slugs_to_update = list(installed.keys())
        elif slug:
            slugs_to_update = [slug]
        else:
            raise SkillpackInputError("请指定 slug 或使用 update_all=true")

        results: list[dict[str, Any]] = []
        for s in slugs_to_update:
            try:
                resolved, files = await client.download_and_extract(
                    slug=s,
                    dest_dir=self._project_dir,
                    version=version,
                    overwrite=True,
                )
                lockfile.update_version(s, resolved)
                skill_dir = self._project_dir / s
                if self._loader.load_single(skill_dir, source="project") is None:
                    self._loader.load_all()
                results.append({
                    "slug": s,
                    "version": resolved,
                    "success": True,
                    "files": files,
                })
            except Exception as exc:
                results.append({
                    "slug": s,
                    "success": False,
                    "error": str(exc),
                })
        return results

    async def clawhub_list_installed(self) -> list[dict[str, Any]]:
        """列出已安装的 ClawHub 技能。"""
        _, lockfile = self._require_clawhub()
        installed = lockfile.get_installed()
        return [
            {"slug": slug, "version": ver}
            for slug, ver in sorted(installed.items())
        ]

    def _write_target_dir(self) -> Path:
        """返回新建技能的写入目标目录。

        有 user_skill_dir 时写入用户私有目录，否则回退到 project 目录。
        """
        return self._user_skill_dir if self._user_skill_dir is not None else self._project_dir

    def _locate_skill_dir(self, skill: Skillpack) -> Path:
        """根据技能的 root_dir 定位其在文件系统上的实际目录。

        优先使用 skill.root_dir（加载时已记录的绝对路径），
        回退到按 source 推断目录。
        """
        if skill.root_dir and Path(skill.root_dir).exists():
            return Path(skill.root_dir)
        if skill.source == "user" and self._user_skill_dir is not None:
            return self._user_skill_dir / skill.name
        return self._project_dir / skill.name

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
            "instructions": base.instructions,
            "file_patterns": list(base.file_patterns),
            "resources": list(base.resources),
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
        if payload.get("file_patterns"):
            frontmatter["file-patterns"] = payload["file_patterns"]
        if payload.get("resources"):
            frontmatter["resources"] = payload["resources"]
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
            "instructions": skill.instructions,
            "source": skill.source,
            "writable": skill.source in ("project", "user"),
            "file_patterns": list(skill.file_patterns),
            "resources": list(skill.resources),
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
