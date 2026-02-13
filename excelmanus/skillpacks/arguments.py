"""Skillpack 参数解析与占位符替换。"""

from __future__ import annotations

import re

_PLACEHOLDER_PATTERN = re.compile(
    r"\$ARGUMENTS\[(\d+)\]"  # $ARGUMENTS[N]
    r"|\$ARGUMENTS"  # $ARGUMENTS
    r"|\$(\d+)"  # $N
)


def parse_arguments(raw: str) -> list[str]:
    """将原始参数字符串解析为位置参数列表。"""
    if not raw or not raw.strip():
        return []

    args: list[str] = []
    current: list[str] = []
    token_started = False
    state = "normal"

    for ch in raw:
        if state == "normal":
            if ch.isspace():
                if token_started:
                    args.append("".join(current))
                    current = []
                    token_started = False
                continue
            if ch == '"':
                state = "double_quote"
                token_started = True
                continue
            if ch == "'":
                state = "single_quote"
                token_started = True
                continue
            current.append(ch)
            token_started = True
            continue

        if state == "double_quote":
            if ch == '"':
                state = "normal"
            else:
                current.append(ch)
            continue

        # state == "single_quote"
        if ch == "'":
            state = "normal"
        else:
            current.append(ch)

    if token_started:
        args.append("".join(current))

    return args


def substitute(template: str, args: list[str]) -> str:
    """将模板中的参数占位符替换为实际参数值。"""
    if not template:
        return ""

    if _PLACEHOLDER_PATTERN.search(template) is None:
        return template

    joined_args = " ".join(args)

    def _replace(match: re.Match[str]) -> str:
        full_match = match.group(0)
        indexed_in_arguments = match.group(1)
        indexed_short = match.group(2)

        if indexed_in_arguments is not None:
            index = int(indexed_in_arguments)
            return args[index] if index < len(args) else ""

        if full_match == "$ARGUMENTS":
            return joined_args

        if indexed_short is not None:
            index = int(indexed_short)
            return args[index] if index < len(args) else ""

        return full_match

    replaced = _PLACEHOLDER_PATTERN.sub(_replace, template)
    if not replaced.strip():
        return ""
    return replaced
