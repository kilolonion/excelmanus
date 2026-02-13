"""Skillpack frontmatter 序列化与反序列化。"""

from __future__ import annotations

import re
from typing import Any


class FrontmatterError(ValueError):
    """frontmatter 文本或数据结构不合法。"""


def parse_scalar(value: str) -> Any:
    """解析 frontmatter 标量值。"""
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
        return [parse_scalar(part) for part in parts]

    return value


def parse_frontmatter(raw: str) -> dict[str, Any]:
    """将 frontmatter 文本解析为字典。"""
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
                raise FrontmatterError(f"无效 frontmatter 列表项: {line}")
            data.setdefault(current_list_key, []).append(
                parse_scalar(stripped[2:].strip())
            )
            continue

        if ":" not in line:
            raise FrontmatterError(f"frontmatter 行缺少 ':'：{line}")
        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            raise FrontmatterError("frontmatter 存在空 key")

        if value == "":
            data[key] = []
            current_list_key = key
            continue

        if value.startswith("|") or value.startswith(">"):
            raise FrontmatterError(
                f"不支持多行字符串块语法（'|' 或 '>'）: {line}"
            )
        if value.startswith("{"):
            raise FrontmatterError(
                f"不支持嵌套对象 / flow mapping 语法（'{{'）: {line}"
            )

        data[key] = parse_scalar(value)
        current_list_key = None

    return data


def serialize_frontmatter(data: dict[str, Any]) -> str:
    """将字典格式化为 frontmatter 文本。"""
    if not isinstance(data, dict):
        raise FrontmatterError("frontmatter 数据必须为字典")

    lines: list[str] = []
    for key, value in data.items():
        if not isinstance(key, str) or not key.strip():
            raise FrontmatterError("frontmatter 字段名必须是非空字符串")
        normalized_key = key.strip()
        if isinstance(value, list):
            lines.append(f"{normalized_key}:")
            for item in value:
                if isinstance(item, bool):
                    lines.append(f"  - {'true' if item else 'false'}")
                elif isinstance(item, (str, int, float)):
                    lines.append(f"  - {item}")
                else:
                    raise FrontmatterError(
                        f"frontmatter 字段 '{normalized_key}' 的列表项类型不支持: "
                        f"{type(item).__name__}"
                    )
        elif isinstance(value, bool):
            # bool 必须在 int 之前判断（bool 是 int 的子类）
            lines.append(f"{normalized_key}: {'true' if value else 'false'}")
        elif isinstance(value, (str, int, float)):
            lines.append(f"{normalized_key}: {value}")
        else:
            raise FrontmatterError(
                f"frontmatter 字段 '{normalized_key}' 类型不支持: {type(value).__name__}"
            )
    return "\n".join(lines)

