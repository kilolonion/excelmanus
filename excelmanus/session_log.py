"""Session event log：append-only 会话事件日志与 surface fold。

事实源是事件序列；模型可见面（surface）与 durable 审计视图都是其派生物。
语义对齐 dsh-excel ``packages/core/session/src/surface.ts``：

- 只有 surface-producing kind 参与 fold；其余事件（turn/step 边界、usage、
  compaction bookkeeping、request/header、``tool/call_start`` / ``tool/call_end``
  等）只进日志、永不进模型面。
- ``surface_op`` 取值：``append``（产出节点）、``replace``（遮蔽
  ``source_seqs`` 列出的 live 节点，并在其中最小 seq 的 surface 位置插入
  新节点）、``void``（遮蔽但不产出——rollback/drop_system_updates 等撤回）。
- ``replace``/``void`` 必须声明 ``source_seqs``（精确遮蔽集，fail-closed：
  每个 seq 都必须是当前 live 的 surface 节点）；``shadow`` 区间可省略，
  省略时自动取 ``(min, max)``。
- ``replace`` 的 payload 可以是 ``{"messages": [...]}`` 多节点形式——
  事件占用 ``seq .. seq+len(messages)-1`` 号段，每个 message 一个节点
  （压缩摘要的 [user 指令, assistant 摘要] 双消息形态由此表达）。
- ``tool/result`` 的 ``replace`` 只允许改 ``content``：payload 除
  ``content``/``message_id``/``_projection_content`` 外必须与源节点逐字段一致，
  且只能遮蔽单个 ``tool/result`` 节点。

缓存契约：surface fold 只决定「模型看到什么」；任何对已发送前缀的实质改动
都必须由调用方申报 REWRITE 事件（见 ``excelmanus.request.series``）——
本模块只保证改动可追溯、可回放，不豁免前缀不变量。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Iterable, Iterator, Mapping

# 产出 surface 节点的事件类型。
SURFACE_KINDS = frozenset(
    {
        "user/message",
        "assistant/message",
        "tool/result",
        "system/update",
        "context/inject",
        "legacy/import",
    }
)

OP_APPEND = "append"
OP_REPLACE = "replace"
OP_VOID = "void"
_SURFACE_OPS = frozenset({OP_APPEND, OP_REPLACE, OP_VOID})

# tool/result replace 允许变化的 payload 字段。
_TOOL_REPLACE_MUTABLE = frozenset(
    {"content", "message_id", "_projection_content", "replaces_message_id"}
)

# SSE 工具调用审计：非 surface，供 UI/回放按 parent 重建子调用时间线。
TOOL_CALL_START_KIND = "tool/call_start"
TOOL_CALL_END_KIND = "tool/call_end"
_TOOL_CALL_AUDIT_KINDS = frozenset({TOOL_CALL_START_KIND, TOOL_CALL_END_KIND})


class SurfaceContractError(ValueError):
    """surface 写入边界校验失败——fail-closed，不允许带病事件进日志。"""


@dataclass(frozen=True)
class SessionEvent:
    """一条会话事件。seq 在 session 内单调递增，由 SessionEventLog 分配。"""

    seq: int
    kind: str
    payload: Mapping[str, Any] = field(default_factory=dict)
    turn: int = 0
    step: int = 0
    surface_op: str | None = None
    shadow_start: int | None = None
    shadow_end: int | None = None
    source_seqs: tuple[int, ...] = ()
    created_at: float = 0.0

    @property
    def produces_surface(self) -> bool:
        return self.surface_op in (OP_APPEND, OP_REPLACE)

    def to_row(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "kind": self.kind,
            "turn": self.turn,
            "step": self.step,
            "payload": json.dumps(dict(self.payload), ensure_ascii=False),
            "surface_op": self.surface_op,
            "shadow_start": self.shadow_start,
            "shadow_end": self.shadow_end,
            "source_seqs": json.dumps(list(self.source_seqs)),
            "created_at": self.created_at,
        }

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> "SessionEvent":
        raw_payload = row.get("payload")
        if isinstance(raw_payload, Mapping):
            payload = dict(raw_payload)
        else:
            try:
                payload = json.loads(raw_payload) if raw_payload else {}
            except (json.JSONDecodeError, TypeError):
                payload = {}
            if not isinstance(payload, dict):
                payload = {"content": payload}
        raw_seqs = row.get("source_seqs")
        try:
            seqs = json.loads(raw_seqs) if raw_seqs else []
        except (json.JSONDecodeError, TypeError):
            seqs = []
        return cls(
            seq=int(row["seq"]),
            kind=str(row["kind"]),
            payload=payload,
            turn=int(row.get("turn") or 0),
            step=int(row.get("step") or 0),
            surface_op=row.get("surface_op"),
            shadow_start=row.get("shadow_start"),
            shadow_end=row.get("shadow_end"),
            source_seqs=tuple(int(s) for s in seqs),
            created_at=float(row.get("created_at") or 0.0),
        )


@dataclass
class _LiveNode:
    """surface 中的一个 live 节点。"""

    seq: int
    kind: str
    message: dict[str, Any]


class SurfaceFold:
    """增量 fold 状态机：按 seq 顺序 apply 事件，维护 live surface。

    同时保留全部 surface-producing 事件（含被遮蔽节点）供 durable 审计视图。
    """

    def __init__(self) -> None:
        self._live: list[_LiveNode] = []
        self._all: dict[int, SessionEvent] = {}
        self._tip_seq = 0

    @property
    def tip_seq(self) -> int:
        """已回放的最大 seq（增量回放游标）。"""
        return self._tip_seq

    @property
    def live_nodes(self) -> list[_LiveNode]:
        return list(self._live)

    def live_seqs(self) -> set[int]:
        return {n.seq for n in self._live}

    # ── 校验 ──────────────────────────────────────────

    def _live_by_seq(self) -> dict[int, _LiveNode]:
        return {n.seq: n for n in self._live}

    @staticmethod
    def _event_nodes(
        kind: str, payload: Mapping[str, Any],
    ) -> list[dict[str, Any]] | None:
        """提取事件将产出的消息节点列表；None 表示非法。"""
        if kind == "legacy/import":
            return [dict(payload)]
        nested = payload.get("messages")
        if isinstance(nested, list):
            if not nested or not all(isinstance(m, dict) for m in nested):
                return None
            if not all(isinstance(m.get("role"), str) for m in nested):
                return None
            return [dict(m) for m in nested]
        if isinstance(payload.get("role"), str):
            return [dict(payload)]
        return None

    def _validate_surface(
        self,
        *,
        kind: str,
        payload: Mapping[str, Any],
        surface_op: str | None,
        shadow_start: int | None,
        shadow_end: int | None,
        source_seqs: tuple[int, ...],
    ) -> list[_LiveNode]:
        """返回将被遮蔽的 live 节点；校验失败抛 SurfaceContractError。"""
        if surface_op is None:
            if kind in SURFACE_KINDS:
                raise SurfaceContractError(
                    f"surface kind {kind!r} 必须声明 surface_op"
                )
            return []
        if surface_op not in _SURFACE_OPS:
            raise SurfaceContractError(f"非法 surface_op: {surface_op!r}")
        if surface_op == OP_APPEND:
            if kind not in SURFACE_KINDS:
                raise SurfaceContractError(
                    f"非 surface kind {kind!r} 不得携带 append"
                )
            if self._event_nodes(kind, payload) is None:
                raise SurfaceContractError(
                    f"{kind!r} append 的 payload 缺少 role"
                )
            return []
        if kind not in SURFACE_KINDS and surface_op == OP_REPLACE:
            raise SurfaceContractError(
                f"replace 只能由 surface kind 发起，收到 {kind!r}"
            )
        if not source_seqs:
            raise SurfaceContractError(f"{surface_op} 缺少 source_seqs")
        live = self._live_by_seq()
        dead = [s for s in source_seqs if s not in live]
        if dead:
            raise SurfaceContractError(
                f"{surface_op} source_seqs 含非 live 节点: {dead}"
            )
        if shadow_start is not None or shadow_end is not None:
            derived = (min(source_seqs), max(source_seqs))
            if (shadow_start, shadow_end) != derived:
                raise SurfaceContractError(
                    f"shadow 区间 [{shadow_start}, {shadow_end}] 与 "
                    f"source_seqs 推导 {derived} 不一致"
                )
        shadowed = sorted(
            (live[s] for s in source_seqs), key=lambda n: n.seq
        )
        if surface_op == OP_REPLACE and kind == "tool/result":
            if len(shadowed) != 1 or shadowed[0].kind != "tool/result":
                raise SurfaceContractError(
                    "tool/result replace 只能遮蔽单个 tool/result 节点"
                )
            src = shadowed[0].message
            for key in set(src) | set(payload):
                if key in _TOOL_REPLACE_MUTABLE:
                    continue
                if src.get(key) != payload.get(key):
                    raise SurfaceContractError(
                        f"tool/result replace 不允许改字段 {key!r}"
                    )
        return shadowed

    # ── 回放 ──────────────────────────────────────────

    def apply(self, event: SessionEvent) -> None:
        """校验并应用一条事件；失败抛 SurfaceContractError（不落地）。"""
        if event.seq <= self._tip_seq:
            raise SurfaceContractError(
                f"事件 seq={event.seq} 乱序/重复（tip={self._tip_seq}）"
            )
        shadowed = self._validate_surface(
            kind=event.kind,
            payload=event.payload,
            surface_op=event.surface_op,
            shadow_start=event.shadow_start,
            shadow_end=event.shadow_end,
            source_seqs=event.source_seqs,
        )
        nodes = (
            self._event_nodes(event.kind, event.payload)
            if event.surface_op in (OP_APPEND, OP_REPLACE)
            else None
        ) or []
        self._tip_seq = event.seq + len(nodes) - 1 if nodes else event.seq
        if event.surface_op in (OP_REPLACE, OP_VOID):
            # 移除被遮蔽节点；replace 在最小 source_seq 的 surface 位置插入。
            first = shadowed[0].seq
            insert_at = next(
                i for i, n in enumerate(self._live) if n.seq == first
            )
            dead = {n.seq for n in shadowed}
            self._live = [n for n in self._live if n.seq not in dead]
            for offset, message in enumerate(nodes):
                self._live.insert(
                    insert_at + offset,
                    _LiveNode(seq=event.seq + offset, kind=event.kind,
                              message=message),
                )
        elif event.surface_op == OP_APPEND:
            for offset, message in enumerate(nodes):
                self._live.append(
                    _LiveNode(seq=event.seq + offset, kind=event.kind,
                              message=message)
                )
        if event.kind in SURFACE_KINDS or event.surface_op == OP_VOID:
            self._all[event.seq] = event

    def apply_all(self, events: Iterable[SessionEvent]) -> None:
        for ev in events:
            self.apply(ev)

    # ── 派生视图 ─────────────────────────────────────

    def surface_messages(self) -> list[dict[str, Any]]:
        """模型可见面：live 节点的 message，按 surface 位置排序。"""
        return [dict(n.message) for n in self._live]

    def durable_messages(self) -> list[dict[str, Any]]:
        """审计视图：全部 surface 事件按 seq 排序，遮蔽关系以标记呈现。

        - 被遮蔽节点带 ``_shadowed_by``（遮蔽它的事件 seq）；
        - replace/void 产出的事件带 ``_shadows``（被遮蔽 seq 列表）；
        - void 事件本身不产出消息，仅在被遮蔽节点上留痕。
        """
        shadowed_by: dict[int, int] = {}
        for ev in self._all.values():
            for src in ev.source_seqs:
                shadowed_by[src] = ev.seq
        out: list[dict[str, Any]] = []
        for seq in sorted(self._all):
            ev = self._all[seq]
            if ev.surface_op == OP_VOID or not ev.produces_surface:
                continue
            nodes = self._event_nodes(ev.kind, ev.payload) or []
            for offset, msg in enumerate(nodes):
                node_seq = seq + offset
                entry = dict(msg)
                entry["_seq"] = node_seq
                if node_seq in shadowed_by:
                    entry["_shadowed_by"] = shadowed_by[node_seq]
                if ev.source_seqs:
                    entry["_shadows"] = list(ev.source_seqs)
                out.append(entry)
        return out


class SessionEventLog:
    """会话级 append-only 事件日志。

    持有 fold 状态机与 seq 分配器；可选地把事件落到 ChatHistoryStore。
    所有写入先过 fold 校验——校验失败的事件既不进内存也不落盘。
    """

    def __init__(
        self,
        session_id: str,
        *,
        store: Any | None = None,
        events: Iterable[SessionEvent | Mapping[str, Any]] | None = None,
    ) -> None:
        self._session_id = session_id
        self._store = store
        self._fold = SurfaceFold()
        self._events: list[SessionEvent] = []
        self._flushed_upto = 0
        if events:
            for raw in events:
                ev = (
                    raw
                    if isinstance(raw, SessionEvent)
                    else SessionEvent.from_row(raw)
                )
                self._fold.apply(ev)
                self._events.append(ev)
            # 回放进来的事件均已落盘
            self._flushed_upto = self._fold.tip_seq

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def events(self) -> list[SessionEvent]:
        return list(self._events)

    @property
    def tip_seq(self) -> int:
        return self._fold.tip_seq

    def append(
        self,
        kind: str,
        payload: Mapping[str, Any] | None = None,
        *,
        turn: int = 0,
        step: int = 0,
        surface_op: str | None = None,
        shadow: tuple[int, int] | None = None,
        source_seqs: Iterable[int] = (),
    ) -> SessionEvent:
        """追加一条事件。校验失败抛 SurfaceContractError。"""
        if surface_op is None and kind in SURFACE_KINDS:
            surface_op = OP_APPEND
        seqs = tuple(int(s) for s in source_seqs)
        if shadow is None and seqs:
            shadow = (min(seqs), max(seqs))
        event = SessionEvent(
            seq=self._fold.tip_seq + 1,
            kind=kind,
            payload=dict(payload or {}),
            turn=turn,
            step=step,
            surface_op=surface_op,
            shadow_start=shadow[0] if shadow else None,
            shadow_end=shadow[1] if shadow else None,
            source_seqs=seqs,
            created_at=time.time(),
        )
        self._fold.apply(event)
        self._events.append(event)
        return event

    def events_after(self, seq: int) -> list[SessionEvent]:
        """seq 之后的事件（含 seq=0 取全部）。持久化对账用。"""
        return [ev for ev in self._events if ev.seq > seq]

    def pending_events(self) -> list[SessionEvent]:
        """尚未标记落盘的事件（内存水位；DB 侧另有 max_event_seq 对账）。"""
        return self.events_after(self._flushed_upto)

    def note_flushed(self, upto_seq: int) -> None:
        self._flushed_upto = max(self._flushed_upto, upto_seq)

    # ── 视图 ─────────────────────────────────────────

    def surface_messages(self) -> list[dict[str, Any]]:
        return self._fold.surface_messages()

    def durable_messages(self) -> list[dict[str, Any]]:
        return self._fold.durable_messages()

    def live_seqs(self) -> set[int]:
        return self._fold.live_seqs()

    def live_nodes_view(self) -> list[tuple[int, str, dict[str, Any]]]:
        """live 节点的 (seq, kind, message) 快照——resume 重建用。"""
        return [
            (n.seq, n.kind, dict(n.message)) for n in self._fold.live_nodes
        ]

    def find_surface_node(self, *, tool_call_id: str) -> _LiveNode | None:
        """按 tool_call_id 找最近的 live tool/result 节点。"""
        for node in reversed(self._fold.live_nodes):
            if node.kind == "tool/result" and node.message.get(
                "tool_call_id"
            ) == tool_call_id:
                return node
        return None

    def orphan_compaction_starts(self) -> list[SessionEvent]:
        """有 ``compaction/start`` 但无对应 ``compaction/end`` 的事件。

        崩溃遗留的压缩锁——surface fold 本身仍正确（replace 只按
        已落盘事件回放），但调用方应告警以便诊断。
        """
        open_starts: list[SessionEvent] = []
        for ev in self._events:
            if ev.kind == "compaction/start":
                open_starts.append(ev)
            elif ev.kind == "compaction/end" and open_starts:
                open_starts.pop()
        return open_starts

    def iter_persisted(self) -> Iterator[SessionEvent]:
        """从 store 重放全部事件（resume / 审计路径）。"""
        if self._store is None:
            return iter(self._events)
        return iter(
            SessionEvent.from_row(r)
            for r in self._store.iter_events(self._session_id)
        )


def fold_events(
    events: Iterable[SessionEvent | Mapping[str, Any]],
) -> SurfaceFold:
    """便捷入口：回放事件序列返回 fold。"""
    fold = SurfaceFold()
    for raw in events:
        ev = raw if isinstance(raw, SessionEvent) else SessionEvent.from_row(raw)
        fold.apply(ev)
    return fold


def tool_call_audit_payload(event: Any) -> dict[str, Any]:
    """从 ToolCallEvent 抽出有界审计 payload，不落完整中间矩阵。"""
    from excelmanus.output_guard import sanitize_external_data, sanitize_external_text

    args = getattr(event, "arguments", None)
    if not isinstance(args, dict):
        args = {}
    payload: dict[str, Any] = {
        "tool_call_id": str(getattr(event, "tool_call_id", "") or ""),
        "tool_name": str(getattr(event, "tool_name", "") or ""),
        "iteration": int(getattr(event, "iteration", 0) or 0),
        "arguments": sanitize_external_data(args, max_len=1000),
    }
    parent = str(getattr(event, "parent_call_id", "") or "")
    if parent:
        payload["parent_call_id"] = parent
    for field in ("trace_id", "span_id", "parent_span_id", "request_id", "turn_id", "step_id", "execution_id", "execution_state"):
        value = getattr(event, field, "")
        if isinstance(value, str) and value:
            payload[field] = value
    event_type = getattr(event, "event_type", None)
    value = event_type.value if hasattr(event_type, "value") else str(event_type or "")
    if value == "tool_call_end":
        payload["success"] = bool(getattr(event, "success", True))
        result = getattr(event, "result", "") or ""
        payload["result"] = sanitize_external_text(str(result)[:500], max_len=500)
        error = getattr(event, "error", None)
        if error:
            payload["error"] = sanitize_external_text(str(error), max_len=300)
    return payload


def append_tool_call_event(log: SessionEventLog, event: Any) -> SessionEvent | None:
    """把 TOOL_CALL_START/END 追加为非 surface 审计事件。其它类型忽略。"""
    event_type = getattr(event, "event_type", None)
    value = event_type.value if hasattr(event_type, "value") else str(event_type or "")
    if value == "tool_call_start":
        kind = TOOL_CALL_START_KIND
    elif value == "tool_call_end":
        kind = TOOL_CALL_END_KIND
    else:
        return None
    return log.append(kind, tool_call_audit_payload(event))


def _coerce_session_event(
    raw: SessionEvent | Mapping[str, Any],
) -> tuple[str, dict[str, Any], int]:
    if isinstance(raw, SessionEvent):
        return raw.kind, dict(raw.payload), raw.seq
    kind = str(raw.get("kind") or "")
    payload = raw.get("payload")
    if isinstance(payload, Mapping):
        body = dict(payload)
    else:
        try:
            parsed = json.loads(payload) if payload else {}
        except (json.JSONDecodeError, TypeError):
            parsed = {}
        body = parsed if isinstance(parsed, dict) else {}
    return kind, body, int(raw.get("seq") or 0)


def reconstruct_tool_call_timeline(
    events: Iterable[SessionEvent | Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """按 parent_call_id 重建工具调用时间线。

    顶层只含根调用（无 parent，或 parent 不在本批）；子调用挂在
    ``children`` 下，不作为独立模型轮次。每个已启动调用对应至多一条结束。
    """
    records: dict[str, dict[str, Any]] = {}
    order: list[str] = []

    def _ensure(call_id: str) -> dict[str, Any]:
        rec = records.get(call_id)
        if rec is None:
            rec = {
                "tool_call_id": call_id,
                "tool_name": "",
                "parent_call_id": "",
                "started": False,
                "ended": False,
                "success": None,
                "seq_start": None,
                "seq_end": None,
                "children": [],
            }
            records[call_id] = rec
            order.append(call_id)
        return rec

    for raw in events:
        kind, payload, seq = _coerce_session_event(raw)
        if kind not in _TOOL_CALL_AUDIT_KINDS:
            continue
        call_id = str(payload.get("tool_call_id") or "")
        if not call_id:
            continue
        rec = _ensure(call_id)
        parent = str(payload.get("parent_call_id") or "")
        if parent:
            rec["parent_call_id"] = parent
        name = str(payload.get("tool_name") or "")
        if name:
            rec["tool_name"] = name
        if kind == TOOL_CALL_START_KIND:
            rec["started"] = True
            rec["seq_start"] = seq
        elif kind == TOOL_CALL_END_KIND:
            rec["ended"] = True
            rec["seq_end"] = seq
            if "success" in payload:
                rec["success"] = bool(payload.get("success"))

    id_set = set(records)
    roots: list[dict[str, Any]] = []
    for call_id in order:
        rec = records[call_id]
        parent = rec.get("parent_call_id") or ""
        if parent and parent in id_set and parent != call_id:
            records[parent]["children"].append(rec)
        else:
            roots.append(rec)
    return roots
