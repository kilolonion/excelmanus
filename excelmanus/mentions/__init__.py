"""@ 提及系统（Mention System）。

提供 @type:value 语法的解析、内容解析和补全功能。
"""

from excelmanus.mentions.parser import (
    Mention,
    MentionParser,
    ParseResult,
    ResolvedMention,
)
from excelmanus.mentions.resolver import MentionResolver


def __getattr__(name: str):
    if name == "MentionCompleter":
        from excelmanus.mentions.completer import MentionCompleter
        return MentionCompleter
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "Mention",
    "MentionCompleter",
    "MentionParser",
    "MentionResolver",
    "ParseResult",
    "ResolvedMention",
]
