"""Skillpack 加载器：三层目录扫描、解析、覆盖与软校验。

Frontmatter 解析器支持的 YAML 语法子集
========================================

支持的语法：
- 简单键值对：``key: value``
- 引号字符串：``key: "value"`` 或 ``key: 'value'``（首尾匹配引号会被去除）
- 布尔值：``true`` / ``false``（不区分大小写）
- 整数：``123``、``-42``
- 内联列表：``key: [a, b, c]``
- 多行列表::

    key:
      - item1
      - item2

- 包含冒号的值：``url: https://example.com``（仅按第一个冒号分割）
- 注释行：以 ``#`` 开头的行会被忽略

不支持的语法（会抛出 SkillpackValidationError）：
- 多行字符串块：``|``、``>`` 开头的值
- 嵌套对象 / flow mapping：``{`` 开头的值
"""

from __future__ import annotations

from pathlib import Path
import re
from typing import Any

from excelmanus.config import ExcelManusConfig
from excelmanus.logger import get_logger
from excelmanus.skillpacks.models import Skillpack, SkillpackSource
from excelmanus.tools import ToolRegistry

logger = get_logger("skillpacks.loader")

_FRONTMATTER_PATTERN = re.compile(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", re.DOTALL)


class SkillpackLoaderError(Exception):
    """Skillpack 加载失败。"""


class SkillpackValidationError(SkillpackLoaderError):
    """Skillpack 内容不合法。"""


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
        return dict(self._skillpacks)

    def load_all(self) -> dict[str, Skillpack]:
        """加载 system/user/project 三层 Skillpack。"""
        self._warnings.clear()
        merged: dict[str, Skillpack] = {}

        source_dirs: list[tuple[SkillpackSource, Path]] = [
            ("system", Path(self._config.skills_system_dir).expanduser()),
            ("user", Path(self._config.skills_user_dir).expanduser()),
            ("project", Path(self._config.skills_project_dir).expanduser()),
        ]

        for source, root_dir in source_dirs:
            source_skillpacks = self._scan_source(source=source, root_dir=root_dir)
            # 覆盖优先级：project > user > system
            merged.update(source_skillpacks)

        self._skillpacks = merged
        logger.info(
            "已加载 %d 个 Skillpack（system/user/project 合并后）",
            len(self._skillpacks),
        )
        return dict(self._skillpacks)

    def _scan_source(
        self, source: SkillpackSource, root_dir: Path
    ) -> dict[str, Skillpack]:
        if not root_dir.exists():
            logger.info("Skillpack 目录不存在，跳过: %s", root_dir)
            return {}
        if not root_dir.is_dir():
            self._append_warning(f"Skillpack 路径不是目录，已跳过: {root_dir}")
            return {}

        loaded: dict[str, Skillpack] = {}
        for child in sorted(root_dir.iterdir(), key=lambda p: p.name.lower()):
            if not child.is_dir():
                continue
            skill_md = child / "SKILL.md"
            if not skill_md.exists():
                continue
            try:
                skillpack = self._parse_skillpack_file(
                    source=source,
                    skill_dir=child,
                    skill_file=skill_md,
                )
            except SkillpackValidationError as exc:
                self._append_warning(f"{skill_md}: {exc}")
                continue
            loaded[skillpack.name] = skillpack
        return loaded

    def _parse_skillpack_file(
        self,
        source: SkillpackSource,
        skill_dir: Path,
        skill_file: Path,
    ) -> Skillpack:
        text = skill_file.read_text(encoding="utf-8")
        frontmatter, body = self._split_frontmatter(text=text, skill_file=skill_file)
        line_count = len(body.splitlines())
        if line_count > 500:
            self._append_warning(f"{skill_file}: 正文超过 500 行（当前 {line_count} 行）")

        name = self._get_required_str(frontmatter, "name")
        description = self._get_required_str(frontmatter, "description")
        allowed_tools = self._get_required_str_list(frontmatter, "allowed_tools")
        triggers = self._get_required_str_list(frontmatter, "triggers")
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

        resource_contents = self._load_resources(
            resources=resources, skill_dir=skill_dir, skill_name=name
        )
        self._validate_allowed_tools_soft(name=name, allowed_tools=allowed_tools)

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
            resource_contents=resource_contents,
        )

    def _validate_allowed_tools_soft(self, name: str, allowed_tools: list[str]) -> None:
        known_tools = set(self._tool_registry.get_tool_names())
        unknown_tools = sorted(tool for tool in allowed_tools if tool not in known_tools)
        if unknown_tools:
            self._append_warning(
                f"Skillpack '{name}' 引用了未注册工具（软校验告警）: {', '.join(unknown_tools)}"
            )

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
        frontmatter = SkillpackLoader._parse_frontmatter(frontmatter_raw)
        return frontmatter, body

    @staticmethod
    def _parse_frontmatter(raw: str) -> dict[str, Any]:
        data: dict[str, Any] = {}
        current_list_key: str | None = None

        for raw_line in raw.splitlines():
            line = raw_line.rstrip()
            if not line.strip():
                continue
            stripped = line.lstrip()
            if stripped.startswith("#"):
                continue

            if stripped.startswith("- "):
                if current_list_key is None:
                    raise SkillpackValidationError(f"无效 frontmatter 列表项: {line}")
                data.setdefault(current_list_key, []).append(
                    SkillpackLoader._parse_scalar(stripped[2:].strip())
                )
                continue

            if ":" not in line:
                raise SkillpackValidationError(f"frontmatter 行缺少 ':'：{line}")
            key, value = line.split(":", 1)
            key = key.strip()
            value = value.strip()
            if not key:
                raise SkillpackValidationError("frontmatter 存在空 key")

            if value == "":
                data[key] = []
                current_list_key = key
                continue

            # 拒绝不支持的 YAML 语法：多行字符串块和嵌套对象
            if value.startswith("|"):
                raise SkillpackValidationError(
                    f"不支持多行字符串块语法（'|'）: {line}"
                )
            if value.startswith(">"):
                raise SkillpackValidationError(
                    f"不支持折叠字符串块语法（'>'）: {line}"
                )
            if value.startswith("{"):
                raise SkillpackValidationError(
                    f"不支持嵌套对象 / flow mapping 语法（'{{'）: {line}"
                )

            data[key] = SkillpackLoader._parse_scalar(value)
            current_list_key = None
        return data

    @staticmethod
    def _parse_scalar(value: str) -> Any:
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            return value[1:-1]

        lowered = value.lower()
        if lowered == "true":
            return True
        if lowered == "false":
            return False
        if re.fullmatch(r"-?\d+", value):
            return int(value)

        if value.startswith("[") and value.endswith("]"):
            inner = value[1:-1].strip()
            if not inner:
                return []
            parts = [part.strip() for part in inner.split(",")]
            return [SkillpackLoader._parse_scalar(part) for part in parts]

        return value

    @staticmethod
    def _format_frontmatter(data: dict[str, Any]) -> str:
        """将字典格式化为 frontmatter 文本。

        支持的值类型：str、int、float、bool、list[str|int|float|bool]。
        """
        lines: list[str] = []
        for key, value in data.items():
            if isinstance(value, list):
                lines.append(f"{key}:")
                for item in value:
                    if isinstance(item, bool):
                        lines.append(f"  - {'true' if item else 'false'}")
                    else:
                        lines.append(f"  - {item}")
            elif isinstance(value, bool):
                # bool 必须在 int 之前判断（bool 是 int 的子类）
                lines.append(f"{key}: {'true' if value else 'false'}")
            else:
                lines.append(f"{key}: {value}")
        return "\n".join(lines)


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
    def _get_required_str_list(payload: dict[str, Any], key: str) -> list[str]:
        if key not in payload:
            raise SkillpackValidationError(f"frontmatter 缺少必填字段 '{key}'")
        return SkillpackLoader._to_str_list(value=payload[key], key=key)

    @staticmethod
    def _get_optional_str_list(payload: dict[str, Any], key: str) -> list[str]:
        if key not in payload:
            return []
        return SkillpackLoader._to_str_list(value=payload[key], key=key)

    @staticmethod
    def _to_str_list(value: Any, key: str) -> list[str]:
        if isinstance(value, str):
            items = [value.strip()] if value.strip() else []
            if items:
                return items
            raise SkillpackValidationError(f"frontmatter 字段 '{key}' 不能为空")

        if not isinstance(value, list):
            raise SkillpackValidationError(f"frontmatter 字段 '{key}' 必须是字符串列表")

        items: list[str] = []
        for item in value:
            if not isinstance(item, str) or not item.strip():
                raise SkillpackValidationError(
                    f"frontmatter 字段 '{key}' 存在非字符串或空字符串项"
                )
            items.append(item.strip())
        if not items:
            raise SkillpackValidationError(f"frontmatter 字段 '{key}' 不能为空")
        return items

    @staticmethod
    def _get_optional_int(payload: dict[str, Any], key: str, default: int) -> int:
        value = payload.get(key, default)
        if isinstance(value, int):
            return value
        if isinstance(value, str) and re.fullmatch(r"-?\d+", value.strip()):
            return int(value.strip())
        raise SkillpackValidationError(f"frontmatter 字段 '{key}' 必须是整数")

    @staticmethod
    def _get_optional_bool(payload: dict[str, Any], key: str, default: bool) -> bool:
        value = payload.get(key, default)
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered == "true":
                return True
            if lowered == "false":
                return False
        raise SkillpackValidationError(f"frontmatter 字段 '{key}' 必须是布尔值")
