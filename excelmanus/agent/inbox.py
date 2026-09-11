"""两列 Inbox：next-turn（用户后续）与 next-step（steer / inject）。

认领是破坏性的：从队列剪走并标记 claimed。pre-step 拒绝后不自动塞回。
进程内可序列化；重启丢失插话可以，但不能把未认领队列与模型历史搅在一起。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

InboxKind = Literal["followup", "steer", "inject"]
InboxTarget = Literal["next-turn", "next-step"]

_KIND_TARGET: dict[str, InboxTarget] = {
    "followup": "next-turn",
    "steer": "next-step",
    "inject": "next-step",
}


@dataclass
class InboxItem:
    """一条待处理输入。``extra`` 仅进程内使用，reconstruct 不序列化回调。"""

    id: str
    kind: InboxKind
    content: str
    target: InboxTarget
    claimed: bool = False
    claimed_turn: int | None = None
    claimed_step: int | None = None
    extra: dict[str, Any] = field(default_factory=dict)
    result: Any = None

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "content": self.content,
            "target": self.target,
            "claimed": self.claimed,
            "claimed_turn": self.claimed_turn,
            "claimed_step": self.claimed_step,
        }


class Inbox:
    """两个待处理列。claim 失败或 reject 不自动重入。"""

    def __init__(self) -> None:
        self._next_turn: list[InboxItem] = []
        self._next_step: list[InboxItem] = []
        self._claimed: list[InboxItem] = []
        self._seq = 0

    @property
    def next_turn(self) -> tuple[InboxItem, ...]:
        return tuple(self._next_turn)

    @property
    def next_step(self) -> tuple[InboxItem, ...]:
        return tuple(self._next_step)

    def peek_turn(self) -> InboxItem | None:
        return self._next_turn[0] if self._next_turn else None

    def push(
        self,
        kind: InboxKind,
        content: str,
        *,
        extra: dict[str, Any] | None = None,
    ) -> InboxItem:
        text = str(content or "")
        self._seq += 1
        target = _KIND_TARGET[kind]
        item = InboxItem(
            id=f"inb_{self._seq}",
            kind=kind,
            content=text,
            target=target,
            extra=dict(extra or {}),
        )
        if target == "next-turn":
            self._next_turn.append(item)
        else:
            self._next_step.append(item)
        return item

    def push_followup(self, content: str, *, extra: dict[str, Any] | None = None) -> InboxItem:
        return self.push("followup", content, extra=extra)

    def push_steer(self, content: str, *, extra: dict[str, Any] | None = None) -> InboxItem:
        return self.push("steer", content, extra=extra)

    def push_inject(self, content: str, *, extra: dict[str, Any] | None = None) -> InboxItem:
        return self.push("inject", content, extra=extra)

    def claim(
        self,
        target: InboxTarget,
        *,
        turn: int,
        step: int = 0,
    ) -> list[InboxItem]:
        """从队列剪走一批并标记 claimed。

        ``next-turn``：仅当存在一条 followup 时，才同时带走 pending next-step
        与这一条 next-turn。没有 followup 时 **不** 动 next-step，避免 idle
        时的 steer 被空 turn 吞掉。
        """
        claimed: list[InboxItem] = []
        if target == "next-turn":
            if not self._next_turn:
                return []
            claimed.extend(self._take_all_step())
            one = self._take_one_turn()
            if one is not None:
                claimed.append(one)
        elif target == "next-step":
            claimed.extend(self._take_all_step())
        else:
            raise ValueError(f"unknown inbox target: {target}")

        for item in claimed:
            item.claimed = True
            item.claimed_turn = turn
            item.claimed_step = step
        self._claimed.extend(claimed)
        return claimed

    def drain_unclaimed(self, target: InboxTarget) -> list[str]:
        """兼容旧 drain_* API：取出未认领内容，不经过 claim 标记。"""
        if target == "next-turn":
            items, self._next_turn = self._next_turn, []
        else:
            items, self._next_step = self._next_step, []
        return [item.content for item in items if str(item.content or "").strip()]

    def reconstruct(self) -> dict[str, list[dict[str, Any]]]:
        """序列化未认领队列（不含 extra 回调）。"""
        return {
            "next-turn": [item.to_public_dict() for item in self._next_turn],
            "next-step": [item.to_public_dict() for item in self._next_step],
        }

    def load_reconstructed(self, data: dict[str, Any] | None) -> None:
        """从 reconstruct() 快恢复未认领队列。已认领历史不恢复。"""
        self._next_turn = []
        self._next_step = []
        if not data:
            return
        for raw in data.get("next-turn") or []:
            self._next_turn.append(self._item_from_public(raw, default_target="next-turn"))
        for raw in data.get("next-step") or []:
            self._next_step.append(self._item_from_public(raw, default_target="next-step"))

    def _take_one_turn(self) -> InboxItem | None:
        if not self._next_turn:
            return None
        return self._next_turn.pop(0)

    def _take_all_step(self) -> list[InboxItem]:
        items, self._next_step = self._next_step, []
        return items

    def _item_from_public(
        self,
        raw: dict[str, Any],
        *,
        default_target: InboxTarget,
    ) -> InboxItem:
        self._seq += 1
        kind = raw.get("kind") or ("followup" if default_target == "next-turn" else "inject")
        if kind not in _KIND_TARGET:
            kind = "followup" if default_target == "next-turn" else "inject"
        target = raw.get("target") or _KIND_TARGET[kind]
        return InboxItem(
            id=str(raw.get("id") or f"inb_{self._seq}"),
            kind=kind,
            content=str(raw.get("content") or ""),
            target=target,
        )
