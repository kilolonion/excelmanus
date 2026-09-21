"""对话记忆模块：管理多轮对话上下文与 token 截断。"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any
from uuid import uuid4

import tiktoken

from excelmanus.attachments.image_tokens import estimate_image_tokens
from excelmanus.attachments.migrate import migrate_messages
from excelmanus.attachments.project import assemble_model_request, resolve_image_request_policy
from excelmanus.attachments.request import request_image_dimensions
from excelmanus.config import ExcelManusConfig

logger = logging.getLogger(__name__)

IMAGE_TOKEN_ESTIMATE = 85  # 无尺寸信息时的保守下限（不再用作降级预算）

_INJECTED_USER_PREFIXES = (
    "<available_skills>",
    "<skill-invocation",
    "<mention_context>",
    "## Hook 上下文",
    "<sourced-context",
)

# role → 事件 kind（无 _event_kind 标记时的兜底映射）。
_ROLE_EVENT_KIND = {
    "user": "user/message",
    "assistant": "assistant/message",
    "tool": "tool/result",
    "system": "system/update",
}


def plain_user_text(content: Any) -> str:
    """取出 user 消息里的纯文本，供 UI / 回退轮次判断使用。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                text = part.get("text")
                if isinstance(text, str) and text:
                    parts.append(text)
        return "\n".join(parts)
    return str(content or "")


def _prefix_fingerprint(messages: list[dict]) -> tuple[int, str]:
    """surface 前缀指纹：条数 + 内容 md5（逐条拼接，O(字符数)，远快于 tokenize）。"""
    h = hashlib.md5()
    for msg in messages:
        content = msg.get("content") if isinstance(msg, dict) else ""
        h.update(str(content or "").encode("utf-8", "replace"))
        h.update(b"\x00")
    return (len(messages), h.hexdigest())


def is_visible_user_turn(msg: dict) -> bool:
    """是否计入 UI / rollback 的用户轮次。

    技能目录、skill-invocation 等后台注入的 user-role 消息仍发给模型，
    但不应当成用户气泡，也不占用编辑重发的 turn_index。
    """
    if msg.get("role") != "user":
        return False
    if msg.get("_ui_hidden"):
        return False
    text = plain_user_text(msg.get("content")).strip()
    return not text.startswith(_INJECTED_USER_PREFIXES)


# ---------------------------------------------------------------------------
# 消息清洗：发送到 LLM API 前剥离非标准字段
# ---------------------------------------------------------------------------

# 各角色允许的标准字段（OpenAI Chat Completions API 规范）
_REPLAY_KEYS = frozenset({
    "reasoning_content",
    "thinking",
    "reasoning",
    "replay_state",
    "replay_source",
    "signature",
    "thinking_text",
})
_ASSISTANT_ALLOWED_KEYS = frozenset({"role", "content", "tool_calls", "name", "refusal"}) | _REPLAY_KEYS
_TOOL_ALLOWED_KEYS = frozenset({"role", "content", "tool_call_id", "name"})
_GENERAL_ALLOWED_KEYS = frozenset({"role", "content", "name"})


def _sanitize_messages_for_api(messages: list[dict]) -> list[dict]:
    """去掉内部标记，但保留回放字段。展示清洗不得丢掉 replay_state。"""
    result: list[dict] = []
    for msg in messages:
        role = msg.get("role", "")
        if role == "assistant":
            clean = {k: v for k, v in msg.items() if k in _ASSISTANT_ALLOWED_KEYS}
            tc = clean.get("tool_calls")
            if tc is None or (isinstance(tc, list) and len(tc) == 0):
                clean.pop("tool_calls", None)
            if clean.get("reasoning_content") in (None, ""):
                fallback = clean.get("thinking_text") or clean.get("thinking") or clean.get("reasoning")
                if fallback:
                    clean["reasoning_content"] = fallback
            result.append(clean)
        elif role == "tool":
            result.append({k: v for k, v in msg.items() if k in _TOOL_ALLOWED_KEYS})
        elif role == "system":
            # 历史内 system_update（模式切换/提示词重载）在严格 OpenAI 网关上
            # 会触发 "System message must be at the beginning" 400；
            # 降级为 user 角色并加提示头，位置与内容语义不变。
            clean = {k: v for k, v in msg.items() if k in _GENERAL_ALLOWED_KEYS}
            content = clean.get("content")
            result.append({
                "role": "user",
                "content": (
                    "[系统提示已更新，以下为最新系统提示]\n" + str(content)
                    if isinstance(content, str) and content.strip()
                    else content
                ),
            })
        else:
            result.append({k: v for k, v in msg.items() if k in _GENERAL_ALLOWED_KEYS})
    return result


# ---------------------------------------------------------------------------
# 默认系统提示词：从 prompts/ 文件加载，缺失时自动补齐
# ---------------------------------------------------------------------------


def _load_system_prompt() -> str:
    """从 PromptComposer 加载完整 system 前缀。变量稍后再插。"""
    from excelmanus.prompt.load import PromptComposer, PromptContext
    prompts_dir = Path(__file__).resolve().parent / "prompts"
    composer = PromptComposer(prompts_dir)
    composer.load_all()
    return composer.compose_system_text(PromptContext(chat_mode="write"))


_DEFAULT_SYSTEM_PROMPT = _load_system_prompt()


_encoding_cache: Any = None


def _get_encoding() -> Any:
    """首次计数时才加载 BPE 词表，避免阻塞模块导入。"""
    global _encoding_cache
    if _encoding_cache is None:
        try:
            _encoding_cache = tiktoken.get_encoding("o200k_base")
        except Exception:
            _encoding_cache = tiktoken.get_encoding("cl100k_base")
    return _encoding_cache


class TokenCounter:
    """基于 tiktoken 的 token 计数器。

    优先使用 o200k_base 编码（GPT-5 系列），对 Qwen 等模型也能提供
    比字符估算更准确的近似值，用于 memory 截断判断。
    """

    @staticmethod
    def count(text: str) -> int:
        """计算文本的 token 数量。"""
        if not text:
            return 0
        return len(_get_encoding().encode(text))

    @staticmethod
    def count_message(
        message: dict,
        *,
        config: Any | None = None,
        deepseek: bool = False,
    ) -> int:
        """计算单条消息的 token 数量（含结构开销）。

        图片按请求版尺寸估价，不再用固定 1500 当预算闸。
        """
        tokens = 4  # 每条消息的固定开销（role、分隔符等）
        policy = resolve_image_request_policy(config) if config is not None else None
        for key, value in message.items():
            if value is None:
                continue
            if isinstance(value, str):
                tokens += TokenCounter.count(value)
            elif isinstance(value, list):
                # 多模态 content parts 或 tool_calls 列表
                for item in value:
                    if isinstance(item, dict):
                        if item.get("type") == "image_url":
                            tokens += IMAGE_TOKEN_ESTIMATE
                        elif item.get("type") == "image":
                            att = item.get("attachment")
                            if isinstance(att, dict):
                                width = int(att.get("width") or 0)
                                height = int(att.get("height") or 0)
                                if policy is not None and width > 0 and height > 0:
                                    width, height = request_image_dimensions(
                                        width, height, policy.max_pixels,
                                    )
                                tokens += estimate_image_tokens(
                                    width, height, deepseek=deepseek,
                                )
                            else:
                                tokens += IMAGE_TOKEN_ESTIMATE
                        elif item.get("type") == "text":
                            tokens += TokenCounter.count(item.get("text", ""))
                        else:
                            tokens += TokenCounter.count(str(item))
                    else:
                        tokens += TokenCounter.count(str(item))
        return tokens


class ConversationMemory:
    """对话记忆管理器。

    职责：
    - 维护有序的消息列表（只追加；前缀替换只走 compaction）
    - 提供 system prompt 始终在首位的消息序列
    - 发送投影不改写已发出的历史前缀
    """

    def __init__(self, config: ExcelManusConfig) -> None:
        self._messages: list[dict] = []
        self._system_prompt: str = _DEFAULT_SYSTEM_PROMPT
        self._max_context_tokens: int = config.max_context_tokens
        self._token_counter = TokenCounter()
        self._config = config
        # 预留 10% 的 token 空间给模型输出
        self._compaction_generation: int = 0
        self._wire_sent_tool_ids: set[str] = set()
        self._projection_dirty: bool = False
        self._event_log: Any | None = None
        # 逐消息 token 定价缓存：content digest 变化才重新 tokenize。
        self._msg_token_cache: dict[tuple[bool, str], int] = {}
        # provider usage 锚点：最近一次成功请求的 prompt_tokens +
        # 发送时刻的 surface 前缀指纹。前缀未变时压力测量直接锚定，
        # 只对锚点之后追加的消息用启发式 delta。
        self._pending_anchor: tuple[int, tuple[int, str]] | None = None
        self._usage_anchor: tuple[int, int, tuple[int, str], int] | None = None

    @staticmethod
    def _message_token_key(message: dict) -> str:
        """消息内容的稳定 digest（含 role/tool_calls，排除易变内部键）。"""
        body = json.dumps(
            {k: v for k, v in message.items() if not str(k).startswith("_")},
            ensure_ascii=False, sort_keys=True, default=str,
        )
        return hashlib.md5(body.encode("utf-8")).hexdigest()

    # ── 事件日志接线（append-only 事实源）────────────────

    def attach_event_log(self, log: Any | None) -> None:
        """挂接 SessionEventLog；挂接后所有历史变更都会落事件。"""
        self._event_log = log

    @property
    def event_log(self) -> Any | None:
        return self._event_log

    # 内部链接键：只进内存，不进事件 payload / wire / 持久化。
    _LINK_KEYS = frozenset({"_seq", "_event_kind"})

    def _event_payload(self, msg: dict) -> dict:
        return {k: v for k, v in msg.items() if k not in self._LINK_KEYS}

    def _emit(self, kind: str, msg: dict, **kw: Any) -> None:
        """追加 surface 事件并在消息上打 seq/种类标记。未挂日志时空操作。"""
        log = self._event_log
        if log is None:
            return
        ev = log.append(kind, self._event_payload(msg), **kw)
        msg["_seq"] = ev.seq
        msg["_event_kind"] = ev.kind

    def _emit_void(self, msg: dict, *, kind: str) -> None:
        """对一条已上链的消息发撤回事件（遮蔽但不替换）。"""
        log = self._event_log
        seq = msg.get("_seq") if isinstance(msg, dict) else None
        if log is None or not isinstance(seq, int):
            return
        from excelmanus.session_log import OP_VOID

        log.append(kind, {}, surface_op=OP_VOID, source_seqs=(seq,))

    def _emit_replace(self, msg: dict, *, kind: str | None = None) -> None:
        """对一条已上链的消息发 replace 事件（遮蔽原节点、原位插入新内容）。"""
        log = self._event_log
        seq = msg.get("_seq") if isinstance(msg, dict) else None
        if log is None or not isinstance(seq, int):
            return
        from excelmanus.session_log import OP_REPLACE

        ev_kind = kind or msg.get("_event_kind") or _ROLE_EVENT_KIND.get(
            str(msg.get("role") or ""), "user/message"
        )
        ev = log.append(
            ev_kind,
            self._event_payload(msg),
            surface_op=OP_REPLACE,
            source_seqs=(seq,),
        )
        msg["_seq"] = ev.seq
        msg["_event_kind"] = ev.kind

    def load_from_log(self, log: Any) -> None:
        """resume 路径：从事件日志 fold 出 live surface 重建消息列表。"""
        self.attach_event_log(log)
        self._messages = []
        for seq, kind, message in log.live_nodes_view():
            msg = dict(message)
            msg["_seq"] = seq
            msg["_event_kind"] = kind
            self._messages.append(msg)

    def drain_events(self) -> list:
        """取走未持久化的事件（ConversationPersistence 在每个保存点调用）。"""
        log = self._event_log
        return log.drain_pending() if log is not None else []

    def surface_contains_seq(self, seq: int) -> bool:
        """某 durable 节点是否仍在 live surface 上（未被遮蔽/撤回）。"""
        log = self._event_log
        if log is not None:
            live = getattr(log, "live_seqs", None)
            if callable(live):
                return seq in live()
        return any(
            m.get("_seq") == seq for m in self._messages if isinstance(m, dict)
        )

    def update_context_window(self, max_context_tokens: int) -> None:
        """切换模型后更新上下文窗口大小和截断阈值。"""
        self._max_context_tokens = max(1, max_context_tokens)

    @property
    def messages(self) -> list[dict]:
        """内部消息列表引用（只读语义，调用方不应直接修改）。"""
        return self._messages

    def remove_last_assistant_if(self, predicate: Callable[[str], bool]) -> bool:
        """移除最后一条 assistant 消息（如果其文本内容满足 predicate）。

        Returns:
            True 如果成功移除，False 如果不满足条件或列表为空。
        """
        if self._messages and self._messages[-1].get("role") == "assistant":
            content = self._messages[-1].get("content", "")
            if isinstance(content, str) and predicate(content):
                msg = self._messages.pop()
                self._emit_void(msg, kind="history/truncate")
                return True
        return False

    def replace_message_content(self, index: int, content: str) -> bool:
        """替换指定位置的消息内容。越界时返回 False。"""
        if 0 <= index < len(self._messages):
            msg = self._messages[index]
            msg["content"] = content
            self._emit_replace(msg)
            return True
        return False

    @property
    def system_prompt(self) -> str:
        """获取当前系统提示词。"""
        return self._system_prompt

    @system_prompt.setter
    def system_prompt(self, value: str) -> None:
        """设置系统提示词。"""
        self._system_prompt = value

    def add_user_message(
        self,
        content: str | list[dict],
        *,
        hidden: bool = False,
        prompt_kind: str | None = None,
    ) -> None:
        """添加用户消息。

        Args:
            content: 纯文本字符串或多模态 content parts 列表。
                     列表中的 image_url / image 会在写入时准入为 durable ref。
            hidden: 为 True 时仍进入模型上下文，但不计入 UI / rollback 用户轮次。
            prompt_kind: 注入来源标记（如 skill_catalog），仅用于持久化与排查。
        """
        extra: dict[str, Any] = {}
        if hidden:
            extra["_ui_hidden"] = True
        if prompt_kind:
            extra["_prompt_kind"] = prompt_kind

        durable = self._durablize_content(content)
        msg = {
            "role": "user",
            "content": durable,
            "message_id": uuid4().hex,
            **extra,
        }
        self._messages.append(msg)
        # prompt_kind 标记的注入物（技能目录/mention/hook 等）走 context/inject
        self._emit("context/inject" if prompt_kind else "user/message", msg)

    def add_system_message(
        self,
        content: str,
        *,
        hidden: bool = True,
        prompt_kind: str = "system_update",
    ) -> None:
        """追加 in-history system（模式切换）。不改 leading system。"""
        if not isinstance(content, str) or not content.strip():
            return
        extra: dict[str, Any] = {"_prompt_kind": prompt_kind}
        if hidden:
            extra["_ui_hidden"] = True
        msg = {
            "role": "system",
            "content": content,
            "message_id": uuid4().hex,
            **extra,
        }
        self._messages.append(msg)
        self._emit("system/update", msg)

    def drop_system_updates(self) -> int:
        """移除 durable 中全部 in-history system_update 消息，返回移除数。

        仅允许在信封 series 边界调用（starts_series = 缓存前缀已 miss，
        本请求重新定义后续前缀）——这是与 compaction 同级的唯一合法历史
        重写点。不清理会导致新 series 后模型读到新旧多份 system 指令叠加。
        """
        kept: list[dict[str, Any]] = []
        removed = 0
        for msg in self._messages:
            if msg.get("_prompt_kind") == "system_update":
                self._emit_void(msg, kind="system/drop-updates")
                removed += 1
                continue
            kept.append(msg)
        if removed:
            self._messages[:] = kept
        return removed

    def _durablize_content(self, content: str | list[dict]) -> str | list[dict]:
        if isinstance(content, str):
            return content
        if not isinstance(content, list):
            return content
        from excelmanus.attachments.admit import admit_image_bytes, decode_image_payload
        from excelmanus.attachments.types import AttachmentError

        out: list[dict] = []
        for part in content:
            if not isinstance(part, dict):
                out.append(part)
                continue
            if part.get("type") == "image" and isinstance(part.get("attachment"), dict):
                out.append(part)
                continue
            if part.get("type") != "image_url":
                out.append(part)
                continue
            url = str((part.get("image_url") or {}).get("url") or "")
            try:
                raw = decode_image_payload(url)
                header = url.split(";base64,", 1)[0] if ";base64," in url else "data:image/png"
                media = header.replace("data:", "", 1) or "image/png"
                ref = admit_image_bytes(raw, media_type=media)
                out.append({"type": "image", "attachment": ref.to_dict()})
            except (AttachmentError, ValueError, Exception):
                logger.warning("图片准入失败，写入占位文本", exc_info=True)
                out.append({"type": "text", "text": "[image omitted: unreadable attachment]"})
        return out

    def _live_config(self) -> ExcelManusConfig:
        try:
            from excelmanus.api_app_state import get_config
            cfg = get_config()
            if cfg is not None:
                return cfg
        except Exception:
            pass
        return self._config

    def _count_message(self, message: dict) -> int:
        cfg = self._live_config()
        deepseek = "deepseek" in str(getattr(cfg, "base_url", "") or "").lower()
        key = (bool(deepseek), self._message_token_key(message))
        cached = self._msg_token_cache.get(key)
        if cached is not None:
            return cached
        value = self._token_counter.count_message(message, config=cfg, deepseek=deepseek)
        if len(self._msg_token_cache) > 8192:
            self._msg_token_cache.clear()
        self._msg_token_cache[key] = value
        return value

    def add_assistant_message(self, content: str) -> None:
        """添加助手纯文本回复。"""
        msg = {"role": "assistant", "content": content, "message_id": uuid4().hex}
        self._messages.append(msg)
        self._emit("assistant/message", msg)

    def add_tool_call(self, tool_call_id: str, name: str, arguments: str) -> None:
        """添加助手的工具调用消息。"""
        msg = {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": tool_call_id,
                    "type": "function",
                    "function": {"name": name, "arguments": arguments},
                }
            ],
            "message_id": uuid4().hex,
        }
        self._messages.append(msg)
        self._emit("assistant/message", msg)

    def add_assistant_tool_message(self, message: dict) -> None:
        """添加完整的 assistant tool 调用消息。

        用于保留供应商返回的扩展字段（如 reasoning / 思维链相关元数据）。
        """
        normalized = dict(message)
        normalized["role"] = "assistant"
        # 防御性校验：确保每个 tool_call 都包含 type 字段
        tcs = normalized.get("tool_calls")
        if isinstance(tcs, list):
            for tc in tcs:
                if isinstance(tc, dict) and "type" not in tc:
                    tc["type"] = "function"
        if not normalized.get("message_id"):
            normalized["message_id"] = uuid4().hex
        self._messages.append(normalized)
        self._emit("assistant/message", normalized)

    def add_tool_result(
        self,
        tool_call_id: str,
        content: str,
        *,
        projection_content: str | None = None,
    ) -> None:
        """添加工具执行结果消息，可附带仅出网投影使用的精简文本。"""
        message = {
            "role": "tool",
            "tool_call_id": tool_call_id,
            "content": content,
            "message_id": uuid4().hex,
        }
        if projection_content is not None and projection_content != content:
            message["_projection_content"] = projection_content
        self._messages.append(message)
        self._emit("tool/result", message)

    def replace_tool_result(self, tool_call_id: str, content: str) -> bool:
        """替换已有工具结果消息的内容（按 tool_call_id 匹配最后一条）。

        用于审批通过后将审批提示替换为实际工具执行结果。
        若该结果已经进入上一封信封，标记 projection dirty，由信封 bump generation。
        返回是否成功替换。
        """
        for msg in reversed(self._messages):
            if msg.get("role") == "tool" and msg.get("tool_call_id") == tool_call_id:
                if msg.get("content") == content:
                    return True
                msg["content"] = content
                msg.pop("_projection_content", None)
                # 审批替换：已上链则落 replace 事件（原文留在日志里），
                # 未上链则原地改 durable——两种路径模型可见行为一致。
                self._emit_replace(msg, kind="tool/result")
                if tool_call_id in self._wire_sent_tool_ids:
                    self._projection_dirty = True
                return True
        return False

    def build_system_messages(self, system_prompts: list[str] | None = None) -> list[dict]:
        """构建 system 消息列表。"""
        prompts = system_prompts or [self._system_prompt]
        system_msgs = [
            {"role": "system", "content": prompt}
            for prompt in prompts
            if isinstance(prompt, str) and prompt.strip()
        ]
        if not system_msgs:
            system_msgs = [{"role": "system", "content": self._system_prompt}]
        return system_msgs

    def get_messages(
        self,
        system_prompts: list[str] | None = None,
    ) -> list[dict]:
        """获取完整消息列表（leading system + 对话历史）。

        会过滤内部标记字段（``_image_id`` / ``_ui_hidden`` 等），
        确保不泄露到发送给 LLM 的消息中。动态事实必须先写入 durable。
        """
        system_msgs = self.build_system_messages(system_prompts)
        output: list[dict] = []
        for msg in self._messages:
            output.append({k: v for k, v in msg.items() if not str(k).startswith("_")})
        return system_msgs + output

    def project_for_request(
        self,
        system_prompts: list[str],
        vision_capable: bool = True,
        image_pins: list[str] | tuple[str, ...] | None = None,
        image_report: dict | None = None,
        exclude_system_updates: bool = False,
    ) -> list[dict]:
        """投影 leading system + durable 历史。不插段、不改写已发出前缀。

        预算压力由 compaction 处理；此方法不做 token 截断。
        system 前缀以调用方（信封）给定的为准，不回退默认值——
        否则 wire 上的 messages[0] 会与 envelope.system_head 分叉。
        """
        projected: list[dict] = []
        for msg in self._messages:
            if exclude_system_updates and msg.get("_prompt_kind") == "system_update":
                continue
            clean = {k: v for k, v in msg.items() if not str(k).startswith("_")}
            projection_content = msg.get("_projection_content")
            if msg.get("role") == "tool" and isinstance(projection_content, str):
                clean["content"] = projection_content
            projected.append(clean)
        body = _sanitize_messages_for_api(projected)
        # 记录发送时刻的 surface 快照；请求成功后由
        # note_provider_prompt_tokens 绑定为 usage 锚点。
        self._pending_anchor = (
            len(self._messages),
            _prefix_fingerprint(self._messages),
        )
        self._wire_sent_tool_ids = {
            str(m.get("tool_call_id"))
            for m in body
            if m.get("role") == "tool" and m.get("tool_call_id")
        }
        system_msgs = [
            {"role": "system", "content": prompt}
            for prompt in (system_prompts or [])
            if isinstance(prompt, str) and prompt.strip()
        ]
        return assemble_model_request(
            system_msgs + body,
            vision_capable=vision_capable,
            config=self._live_config(),
            pin_seq=image_pins,
            report=image_report,
        )

    def repair_dangling_tool_calls(self) -> int:
        """修复尾部悬空的 tool_call：为缺失 result 的 tool_call 补占位 tool result。

        当任务被中断（abort / CancelledError）时，memory 尾部可能存在
        assistant 消息包含 N 个 tool_calls 但只有 0..N-1 个 tool results。
        LLM API 要求每个 tool_call 都有对应 tool result，否则下次调用会报错。

        Returns:
            补充的占位 tool result 数量。
        """
        if not self._messages:
            return 0

        # 收集尾部 assistant tool_call 消息中所有 call id
        expected_ids: list[str] = []
        for msg in reversed(self._messages):
            role = msg.get("role")
            if role == "tool":
                continue  # 跳过已有的 tool result
            if role == "assistant" and msg.get("tool_calls"):
                for tc in msg["tool_calls"]:
                    tc_id = tc.get("id") if isinstance(tc, dict) else getattr(tc, "id", None)
                    if tc_id:
                        expected_ids.append(tc_id)
                break  # 只修复最近一组
            else:
                break  # 遇到非 tool/非 tool_call assistant 消息即停止

        if not expected_ids:
            return 0

        # 收集已有的 tool result id
        existing_ids: set[str] = set()
        for msg in self._messages:
            if msg.get("role") == "tool" and msg.get("tool_call_id"):
                existing_ids.add(msg["tool_call_id"])

        # 为缺失的 tool_call 补占位 result
        repaired = 0
        for tc_id in expected_ids:
            if tc_id not in existing_ids:
                msg = {
                    "role": "tool",
                    "tool_call_id": tc_id,
                    "content": "[任务已中断，该工具的结果未完整记录；继续前需核对实际执行结果]",
                }
                self._messages.append(msg)
                self._emit("tool/result", msg)
                repaired += 1

        return repaired

    def inject_messages(self, messages: list[dict]) -> None:
        """注入历史消息（用于会话恢复）。不触发截断。旧 base64 行当场迁成 ref。

        挂了事件日志时逐条落 ``legacy/import`` append——旧会话由此惰性
        迁入事件溯源，无需一次性全库迁移。
        """
        migrated = migrate_messages(messages)
        for msg in migrated:
            self._emit("legacy/import", msg)
        self._messages.extend(migrated)

    def rollback_to_user_turn(self, turn_index: int, *, keep_target: bool = True) -> int:
        """回退对话到第 turn_index 个用户消息（0-indexed）。

        截断该用户消息之后的所有消息。

        Args:
            turn_index: 目标用户轮次索引（0 = 第一条用户消息）。
            keep_target: 若为 True（默认），保留目标 user 消息本身；
                若为 False，连同目标 user 消息一起移除（用于重发场景）。

        Returns:
            被截断的消息数量。

        Raises:
            IndexError: turn_index 超出范围。
        """
        user_indices = [
            i for i, m in enumerate(self._messages) if is_visible_user_turn(m)
        ]
        if not user_indices or turn_index < 0 or turn_index >= len(user_indices):
            raise IndexError(
                f"用户轮次索引 {turn_index} 超出范围（共 {len(user_indices)} 轮）"
            )
        cut_after = user_indices[turn_index]
        if keep_target:
            removed = self._messages[cut_after + 1:]
            self._messages = self._messages[: cut_after + 1]
        else:
            removed = self._messages[cut_after:]
            self._messages = self._messages[:cut_after]
        for msg in removed:
            self._emit_void(msg, kind="rollback/edit")
        return len(removed)

    def list_user_turns(self) -> list[dict]:
        """列出所有用户轮次摘要，返回 [{index, content_preview, msg_index}]。"""
        turns: list[dict] = []
        turn_idx = 0
        for i, m in enumerate(self._messages):
            if is_visible_user_turn(m):
                content = m.get("content", "")
                if isinstance(content, list):
                    preview = "[多模态消息]"
                elif isinstance(content, str):
                    preview = content[:80] + ("..." if len(content) > 80 else "")
                else:
                    preview = str(content)[:80]
                turns.append({
                    "index": turn_idx,
                    "content_preview": preview,
                    "msg_index": i,
                })
                turn_idx += 1
        return turns

    @property
    def message_count(self) -> int:
        """当前消息数量。"""
        return len(self._messages)

    def clear(self) -> None:
        """清除所有对话历史（保留 system prompt 配置）。"""
        for msg in self._messages:
            self._emit_void(msg, kind="session/clear")
        self._messages.clear()

    def apply_compaction_summary(
        self, synthetic: list[dict], split_idx: int,
    ) -> None:
        """压缩落点：用 ``synthetic`` 替换 ``_messages[:split_idx]``。

        挂日志时落一条多节点 ``user/message`` replace 事件：被压区间的
        原始消息在事件日志中永久保留，surface 上原位换成摘要——
        原文不再随压缩丢失。
        """
        removed = self._messages[:split_idx]
        seqs = sorted(
            m["_seq"] for m in removed if isinstance(m.get("_seq"), int)
        )
        log = self._event_log
        if log is not None and len(seqs) == len(removed) and seqs:
            from excelmanus.session_log import OP_REPLACE

            ev = log.append(
                "user/message",
                {"messages": [self._event_payload(m) for m in synthetic]},
                surface_op=OP_REPLACE,
                source_seqs=seqs,
            )
            for offset, msg in enumerate(synthetic):
                msg["_seq"] = ev.seq + offset
                msg["_event_kind"] = ev.kind
        else:
            # 无日志或区间含未上链消息：退化为逐条 void + 直接拼接。
            for msg in removed:
                self._emit_void(msg, kind="compaction/summary")
            for msg in synthetic:
                self._emit("context/inject", msg)
        self._messages = synthetic + self._messages[split_idx:]

    def note_provider_prompt_tokens(self, prompt_tokens: int) -> None:
        """请求成功后绑定 usage 锚点（provider 上报的真实输入 token 数）。"""
        pending = self._pending_anchor
        if pending is None or int(prompt_tokens or 0) <= 0:
            return
        count, fp = pending
        self._usage_anchor = (
            int(prompt_tokens), count, fp, self._compaction_generation,
        )

    def _total_tokens_with_system_messages(self, system_msgs: list[dict] | None) -> int:
        # usage 锚点命中：前缀指纹与压缩代数都没变 → provider 真实计量
        # 直接复用，只对锚点后追加的消息做启发式 delta。
        anchor = self._usage_anchor
        if anchor is not None:
            prompt_tokens, count, fp, gen = anchor
            if (
                gen == self._compaction_generation
                and len(self._messages) >= count
                and _prefix_fingerprint(self._messages[:count]) == fp
            ):
                delta = sum(
                    self._count_message(m) for m in self._messages[count:]
                )
                return prompt_tokens + delta
            self._usage_anchor = None

        total = 0
        if system_msgs is None:
            system_msg = {"role": "system", "content": self._system_prompt}
            total += self._count_message(system_msg)
        else:
            for msg in system_msgs:
                total += self._count_message(msg)
        for msg in self._messages:
            total += self._count_message(msg)
        return total

    def _truncate_history_to_threshold(
        self,
        threshold: int,
        system_msgs: list[dict] | None,
        *,
        protect_first: int = 0,
    ) -> None:
        """从头部截断历史直到 token 数降回阈值以内。

        protect_first：头部 N 条消息受保护（如压缩刚写入的合成摘要），
        只会被内容收缩、不会被删除。
        """
        while (
            self._messages
            and len(self._messages) > protect_first
            and self._total_tokens_with_system_messages(system_msgs) > threshold
        ):
            # 只剩最后一条（未保护）时做内容收缩，避免单条超长消息长期越阈值。
            if len(self._messages) == protect_first + 1:
                if not self._shrink_last_message_for_threshold(threshold, system_msgs):
                    # 无法收缩（例如 content 为 None 的 tool_call 壳消息）时，
                    # 直接丢弃该条，保证请求不会持续超预算。
                    removed_last = self._messages.pop(protect_first)
                    self._emit_void(removed_last, kind="compaction/truncate")
                    break
                # 收缩后仍可能因 system 过大而超阈值，此时保留最后一条不删
                if self._total_tokens_with_system_messages(system_msgs) > threshold:
                    break
                continue

            # 移除最早的未保护消息，但至少保护住头部 protect_first 条
            removed = self._messages.pop(protect_first)
            self._emit_void(removed, kind="compaction/truncate")

            # 如果移除的是带 tool_calls 的 assistant 消息，
            # 需要同时移除对应的 tool result 消息
            if removed.get("tool_calls"):
                call_ids = {
                    tc["id"] for tc in removed["tool_calls"] if "id" in tc
                }
                # 移除所有匹配的 tool result（它们紧跟在 tool_call 之后）
                kept_msgs = []
                for m in self._messages:
                    if m.get("role") == "tool" and m.get("tool_call_id") in call_ids:
                        self._emit_void(m, kind="compaction/truncate")
                        continue
                    kept_msgs.append(m)
                self._messages = kept_msgs

            # 如果未保护区域头部是孤立的 tool result（对应的 tool_call 已不存在），
            # 继续移除以保持消息一致性。
            # 注意：必须检查 tool_call_id 是否真的孤立，避免误删有效的 tool result。
            while (
                len(self._messages) > protect_first
                and self._messages[protect_first].get("role") == "tool"
            ):
                # 收集剩余消息中所有有效的 tool_call id
                valid_call_ids: set[str] = {
                    tc["id"]
                    for m in self._messages
                    if m.get("tool_calls")
                    for tc in m["tool_calls"]
                    if "id" in tc
                }
                head_call_id = self._messages[protect_first].get("tool_call_id")
                if head_call_id in valid_call_ids:
                    # 对应的 tool_call 仍存在，不是孤立消息，停止清理
                    break
                orphan = self._messages.pop(protect_first)
                self._emit_void(orphan, kind="compaction/truncate")

    def _shrink_last_message_for_threshold(
        self,
        threshold: int,
        system_msgs: list[dict] | None,
    ) -> bool:
        """尽量收缩最后一条消息内容，返回是否完成收缩。"""
        msg = self._messages[-1]
        content = msg.get("content")
        if not isinstance(content, str):
            return False
        if not content:
            # 已为空，无需再收缩，保留该条消息
            return True

        message_tokens = self._count_message(msg)
        content_tokens = self._token_counter.count(content)
        base_tokens = message_tokens - content_tokens
        budget_for_content = threshold - (
            self._total_tokens_with_system_messages(system_msgs) - message_tokens
        ) - base_tokens
        if budget_for_content <= 0:
            msg["content"] = ""
            self._emit_replace(msg)
            return True

        if content_tokens <= budget_for_content:
            return True

        marker = "[截断]"

        def _fits(candidate: str) -> bool:
            return self._token_counter.count(candidate) <= budget_for_content

        # 二分查找可保留的最大尾部长度，避免字符近似导致过度裁切。
        left = 1
        right = len(content)
        best = ""
        while left <= right:
            mid = (left + right) // 2
            candidate = f"{marker}{content[-mid:]}"
            if _fits(candidate):
                best = candidate
                left = mid + 1
            else:
                right = mid - 1

        if not best:
            # 如果加标记放不下，退化为纯尾部文本，尽量保留一点近期上下文。
            left = 1
            right = len(content)
            while left <= right:
                mid = (left + right) // 2
                candidate = content[-mid:]
                if _fits(candidate):
                    best = candidate
                    left = mid + 1
                else:
                    right = mid - 1

        if not best:
            msg["content"] = ""
            self._emit_replace(msg)
            return True

        if len(best) >= len(content):
            best = content[-max(1, len(content) // 2):]

        # 防止收缩后内容未变导致外层 while 无限循环
        if best == content:
            msg["content"] = ""
            self._emit_replace(msg)
            return True

        msg["content"] = best
        self._emit_replace(msg)
        return True
