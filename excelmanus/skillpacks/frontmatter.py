"""Skillpack frontmatter 序列化与反序列化。"""

from __future__ import annotations

from typing import Any

import yaml


class FrontmatterError(ValueError):
    """frontmatter 文本或数据结构不合法。"""


def parse_scalar(value: str) -> Any:
    """解析 frontmatter 标量值。"""
    if isinstance(value, str) and len(value) >= 2:
        if value[0] == value[-1] == "'":
            return value[1:-1].replace("''", "'")
        if value[0] == value[-1] == '"':
            return value[1:-1]
    try:
        parsed = yaml.safe_load(value)
    except yaml.YAMLError as exc:
        raise FrontmatterError(f"标量解析失败: {exc}") from exc
    return parsed


def parse_frontmatter(raw: str) -> dict[str, Any]:
    """将 frontmatter 文本解析为字典。"""
    if not raw or not raw.strip():
        return {}
    try:
        parsed = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise FrontmatterError(f"frontmatter 解析失败: {exc}") from exc
    if parsed is None:
        return {}
    if not isinstance(parsed, dict):
        raise FrontmatterError("frontmatter 根节点必须为对象")
    return dict(parsed)


def serialize_frontmatter(data: dict[str, Any]) -> str:
    """将字典格式化为 frontmatter 文本。"""
    if not isinstance(data, dict):
        raise FrontmatterError("frontmatter 数据必须为字典")
    for key in data.keys():
        if not isinstance(key, str) or not key.strip():
            raise FrontmatterError("frontmatter 字段名必须是非空字符串")
    try:
        dumped = yaml.safe_dump(
            data,
            allow_unicode=True,
            sort_keys=False,
            default_flow_style=False,
        )
    except yaml.YAMLError as exc:
        raise FrontmatterError(f"frontmatter 序列化失败: {exc}") from exc
    return dumped.rstrip("\n")
