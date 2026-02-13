"""ask_user 问题流：问题入队、渲染提示与答案解析。"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
import re
import secrets
from typing import Any


_INDEX_PATTERN = re.compile(r"^\s*(\d+)(?:[\)\].:：、\-]\s*.*)?$")


def _normalize_text(text: str) -> str:
    """文本归一化：去首尾空白、压缩空白并转小写。"""
    compact = re.sub(r"\s+", " ", text or "").strip()
    return compact.lower()


@dataclass(frozen=True)
class QuestionOption:
    """问题选项。"""

    label: str
    description: str
    value: str
    is_other: bool = False


@dataclass(frozen=True)
class PendingQuestion:
    """待回答问题。"""

    question_id: str
    tool_call_id: str
    header: str
    text: str
    options: list[QuestionOption]
    multi_select: bool
    created_at_utc: str


@dataclass(frozen=True)
class ParsedAnswer:
    """解析后的回答。"""

    question_id: str
    multi_select: bool
    selected_options: list[dict[str, Any]]
    other_text: str | None
    raw_input: str

    def to_tool_result(self) -> dict[str, Any]:
        """转换为 tool result 结构。"""
        return {
            "question_id": self.question_id,
            "multi_select": self.multi_select,
            "selected_options": self.selected_options,
            "other_text": self.other_text,
            "raw_input": self.raw_input,
        }


class QuestionFlowManager:
    """问题流管理器：维护 FIFO 队列与答案解析规则。"""

    OTHER_LABEL = "Other"
    OTHER_DESCRIPTION = "可输入其他答案"

    def __init__(self, max_queue_size: int = 8) -> None:
        if max_queue_size <= 0:
            raise ValueError("max_queue_size 必须为正整数。")
        self._max_queue_size = max_queue_size
        self._queue: deque[PendingQuestion] = deque()

    def clear(self) -> None:
        """清空队列。"""
        self._queue.clear()

    def has_pending(self) -> bool:
        """是否存在待回答问题。"""
        return bool(self._queue)

    def queue_size(self) -> int:
        """当前队列长度。"""
        return len(self._queue)

    def current(self) -> PendingQuestion | None:
        """返回队首问题。"""
        if not self._queue:
            return None
        return self._queue[0]

    def pop_current(self) -> PendingQuestion | None:
        """弹出队首问题。"""
        if not self._queue:
            return None
        return self._queue.popleft()

    def enqueue(self, question_payload: dict[str, Any], tool_call_id: str) -> PendingQuestion:
        """将问题入队并返回标准化后的 PendingQuestion。"""
        if len(self._queue) >= self._max_queue_size:
            raise ValueError(
                f"待回答问题已达到上限（{self._max_queue_size}），请先回答当前问题。"
            )
        if not isinstance(tool_call_id, str) or not tool_call_id.strip():
            raise ValueError("tool_call_id 不能为空。")

        pending = self._build_pending(question_payload, tool_call_id.strip())
        self._queue.append(pending)
        return pending

    def format_prompt(self, question: PendingQuestion | None = None) -> str:
        """将问题渲染为用户可读文本。"""
        q = question or self.current()
        if q is None:
            return "当前没有待回答问题。"

        lines: list[str] = [
            "请先回答这个问题后再继续：",
            f"[{q.header}] {q.text}",
            "",
            "可选项：",
        ]
        for i, opt in enumerate(q.options, start=1):
            lines.append(f"{i}. {opt.label}：{opt.description}")

        lines.append("")
        if q.multi_select:
            lines.append("多选：请每行输入一个选项（支持编号或文本），输入空行提交。")
        else:
            lines.append("单选：请输入一个选项（支持编号或文本）。")
        if self.queue_size() > 1:
            lines.append(f"队列中还有 {self.queue_size() - 1} 个问题等待回答。")
        return "\n".join(lines)

    def parse_answer(
        self,
        raw_text: str,
        question: PendingQuestion | None = None,
    ) -> ParsedAnswer:
        """按当前规则解析用户回答。"""
        q = question or self.current()
        if q is None:
            raise ValueError("当前没有待回答问题。")
        if not isinstance(raw_text, str):
            raise ValueError("回答必须是字符串。")

        original = raw_text.strip()
        if not original:
            raise ValueError("回答不能为空。")

        tokens = self._tokenize(original, multi_select=q.multi_select)
        matched_indices: list[int] = []
        other_parts: list[str] = []
        for token in tokens:
            matched = self._match_option(token, q.options)
            if matched is None:
                other_parts.append(token)
            else:
                matched_indices.append(matched)

        # 去重并保持顺序
        deduped_indices: list[int] = []
        seen: set[int] = set()
        for idx in matched_indices:
            if idx not in seen:
                seen.add(idx)
                deduped_indices.append(idx)

        other_index = self._other_index(q.options)
        if q.multi_select:
            final_indices, other_text = self._resolve_multi_select(
                q=q,
                selected_indices=deduped_indices,
                other_parts=other_parts,
                other_index=other_index,
            )
        else:
            final_indices, other_text = self._resolve_single_select(
                q=q,
                selected_indices=deduped_indices,
                other_parts=other_parts,
                other_index=other_index,
                raw_input=original,
            )

        selected_options = [
            {"index": idx + 1, "label": q.options[idx].label}
            for idx in final_indices
        ]
        return ParsedAnswer(
            question_id=q.question_id,
            multi_select=q.multi_select,
            selected_options=selected_options,
            other_text=other_text if other_text else None,
            raw_input=original,
        )

    def _build_pending(self, question_payload: dict[str, Any], tool_call_id: str) -> PendingQuestion:
        if not isinstance(question_payload, dict):
            raise ValueError("question 必须是对象。")

        text = str(question_payload.get("text", "")).strip()
        header = str(question_payload.get("header", "")).strip()
        raw_options = question_payload.get("options")
        multi_select_raw = question_payload.get(
            "multiSelect",
            question_payload.get("multi_select", False),
        )

        if not text:
            raise ValueError("question.text 不能为空。")
        if not header:
            raise ValueError("question.header 不能为空。")
        if len(header) > 12:
            raise ValueError("question.header 长度不能超过 12。")
        if not isinstance(raw_options, list):
            raise ValueError("question.options 必须是数组。")
        if len(raw_options) < 2 or len(raw_options) > 4:
            raise ValueError("question.options 数量必须在 2 到 4 之间。")
        if not isinstance(multi_select_raw, bool):
            raise ValueError("question.multiSelect 必须是布尔值。")

        base_options: list[QuestionOption] = []
        seen_labels: set[str] = set()
        for i, item in enumerate(raw_options, start=1):
            if not isinstance(item, dict):
                raise ValueError("question.options 的每一项都必须是对象。")
            label = str(item.get("label", "")).strip()
            description = str(item.get("description", "")).strip()
            if not label:
                raise ValueError("选项 label 不能为空。")
            if not description:
                raise ValueError("选项 description 不能为空。")
            normalized = _normalize_text(label)
            if normalized in {_normalize_text("other"), _normalize_text("其他")}:
                # 系统统一追加 Other，忽略模型显式传入。
                continue
            if normalized in seen_labels:
                raise ValueError("选项 label 不能重复。")
            seen_labels.add(normalized)
            base_options.append(
                QuestionOption(
                    label=label,
                    description=description,
                    value=f"opt_{i}",
                    is_other=False,
                )
            )

        if len(base_options) < 2 or len(base_options) > 4:
            raise ValueError("去重后的有效选项数量必须在 2 到 4 之间。")

        options = list(base_options)
        options.append(
            QuestionOption(
                label=self.OTHER_LABEL,
                description=self.OTHER_DESCRIPTION,
                value="other",
                is_other=True,
            )
        )

        return PendingQuestion(
            question_id=self._new_question_id(),
            tool_call_id=tool_call_id,
            header=header,
            text=text,
            options=options,
            multi_select=multi_select_raw,
            created_at_utc=datetime.now(timezone.utc).isoformat(),
        )

    @staticmethod
    def _tokenize(raw: str, *, multi_select: bool) -> list[str]:
        if multi_select:
            tokens = [line.strip() for line in raw.splitlines() if line.strip()]
            return tokens or [raw.strip()]

        tokens = [part.strip() for part in re.split(r"[,\n]+", raw) if part.strip()]
        return tokens or [raw.strip()]

    @staticmethod
    def _other_index(options: list[QuestionOption]) -> int:
        for i, opt in enumerate(options):
            if opt.is_other:
                return i
        return len(options) - 1

    @staticmethod
    def _match_option(token: str, options: list[QuestionOption]) -> int | None:
        # 1 / 1. / 1) / 1: xxx / 1-xxx 等形式
        index_match = _INDEX_PATTERN.match(token)
        if index_match:
            idx = int(index_match.group(1))
            if 1 <= idx <= len(options):
                return idx - 1

        normalized = _normalize_text(token)
        for i, opt in enumerate(options):
            if normalized == _normalize_text(opt.label):
                return i
            if normalized == _normalize_text(opt.value):
                return i

        if normalized in {_normalize_text("other"), _normalize_text("其他")}:
            for i, opt in enumerate(options):
                if opt.is_other:
                    return i
        return None

    def _resolve_single_select(
        self,
        *,
        q: PendingQuestion,
        selected_indices: list[int],
        other_parts: list[str],
        other_index: int,
        raw_input: str,
    ) -> tuple[list[int], str]:
        if len(selected_indices) > 1:
            raise ValueError("单选题只能选择一个选项。")

        other_text = "\n".join(other_parts).strip()
        if not selected_indices:
            return [other_index], raw_input

        selected = selected_indices[0]
        if selected == other_index:
            if not other_text:
                # 若仅输入 Other，本轮允许 other_text 为空。
                return [other_index], ""
            return [other_index], other_text

        return [selected], other_text

    def _resolve_multi_select(
        self,
        *,
        q: PendingQuestion,
        selected_indices: list[int],
        other_parts: list[str],
        other_index: int,
    ) -> tuple[list[int], str]:
        other_text = "\n".join(other_parts).strip()
        final_indices = list(selected_indices)

        if other_text and other_index not in final_indices:
            final_indices.append(other_index)
        if not final_indices and other_text:
            final_indices = [other_index]
        if not final_indices:
            raise ValueError("多选题至少需要选择一个选项或输入其他答案。")

        # 若只选了 Other 且无文本，允许为空，后续由模型继续追问。
        if final_indices == [other_index] and not other_text:
            return final_indices, ""
        return final_indices, other_text

    @staticmethod
    def _new_question_id() -> str:
        now = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        return f"qst_{now}_{secrets.token_hex(3)}"
