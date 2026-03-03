"""对话记忆模块：管理多轮对话上下文与 token 截断。"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import tiktoken

from excelmanus.config import ExcelManusConfig

logger = logging.getLogger(__name__)

IMAGE_TOKEN_ESTIMATE = 1500  # 图片 token 估算值（用于 memory 截断）

# ---------------------------------------------------------------------------
# 消息清洗：发送到 LLM API 前剥离非标准字段
# ---------------------------------------------------------------------------

# 各角色允许的标准字段（OpenAI Chat Completions API 规范）
_ASSISTANT_ALLOWED_KEYS = frozenset({"role", "content", "tool_calls", "name", "refusal"})
_TOOL_ALLOWED_KEYS = frozenset({"role", "content", "tool_call_id", "name"})
_GENERAL_ALLOWED_KEYS = frozenset({"role", "content", "name"})


def _sanitize_messages_for_api(messages: list[dict]) -> list[dict]:
    """剥离消息中的非标准字段，防止不同 LLM 提供商因未知字段拒绝请求。

    provider 特有字段（thinking / reasoning / reasoning_content）在此处剥离；
    需要这些字段的 provider（如 DeepSeek thinking mode）由
    ``llm_caller._patch_reasoning_content`` 在调用失败后按需补回。
    """
    result: list[dict] = []
    for msg in messages:
        role = msg.get("role", "")
        if role == "assistant":
            clean = {k: v for k, v in msg.items() if k in _ASSISTANT_ALLOWED_KEYS}
            # tool_calls 为 None 或空列表时移除该键，避免某些 provider 拒绝 null
            tc = clean.get("tool_calls")
            if tc is None or (isinstance(tc, list) and len(tc) == 0):
                clean.pop("tool_calls", None)
            result.append(clean)
        elif role == "tool":
            result.append({k: v for k, v in msg.items() if k in _TOOL_ALLOWED_KEYS})
        else:
            # system / user — 保留 content 原始结构（可能为多模态 list）
            result.append({k: v for k, v in msg.items() if k in _GENERAL_ALLOWED_KEYS})
    return result


# ---------------------------------------------------------------------------
# 图片生命周期管理
# ---------------------------------------------------------------------------


@dataclass
class ImageCacheEntry:
    """图片本地缓存条目（支持降级后重注入）。"""

    image_id: int
    raw_base64: str  # 原始 base64 数据
    mime_type: str = "image/png"
    detail: str = "auto"
    inject_round: int = 0  # 注入时的对话轮次
    last_referenced_round: int = 0  # 最后被 LLM 看到的轮次
    degraded: bool = False  # 是否已降级为文本引用


class ImageLifecycleManager:
    """Provider-aware 的图片上下文生命周期管理。

    替代粗暴的 ``mark_images_sent()`` 立即降级策略。

    策略矩阵：
    ┌──────────────────┬──────────────────────────────────────┐
    │ 条件              │ 行为                                  │
    ├──────────────────┼──────────────────────────────────────┤
    │ 图片 < keep_rounds│ 保持完整 base64（Provider 自动缓存）   │
    │ 活跃数 > max      │ LRU 淘汰最老图片                      │
    │ token 超限        │ 强制降级最老图片                       │
    │ 用户引用已降级图片 │ 从本地缓存重注入                       │
    └──────────────────┴──────────────────────────────────────┘
    """

    def __init__(
        self,
        keep_rounds: int = 3,
        max_active_images: int = 2,
        image_token_budget: int = 6000,
    ):
        self.keep_rounds = keep_rounds
        self.max_active_images = max_active_images
        self.image_token_budget = image_token_budget
        self._cache: dict[int, ImageCacheEntry] = {}

    def register(
        self,
        image_id: int,
        base64_data: str,
        mime_type: str,
        detail: str,
        current_round: int,
    ) -> None:
        """注册新图片到生命周期管理器。"""
        self._cache[image_id] = ImageCacheEntry(
            image_id=image_id,
            raw_base64=base64_data,
            mime_type=mime_type,
            detail=detail,
            inject_round=current_round,
            last_referenced_round=current_round,
        )

    def get_ids_to_degrade(self, current_round: int) -> list[int]:
        """返回本轮应该降级的图片 ID 列表。

        策略：
        1. 超过 keep_rounds 的图片加入候选
        2. 活跃图片数超过 max_active_images 时 LRU 淘汰
        3. 活跃图片 token 超过 budget 时强制淘汰最老的
        """
        to_degrade: list[int] = []
        active = [
            e for e in self._cache.values() if not e.degraded
        ]
        if not active:
            return to_degrade

        # 按 last_referenced_round 排序（最老的在前）
        active.sort(key=lambda e: e.last_referenced_round)

        # 规则 1：超过 keep_rounds 的候选降级
        for entry in active:
            age = current_round - entry.inject_round
            if age >= self.keep_rounds:
                to_degrade.append(entry.image_id)

        # 规则 2：活跃数超限 → LRU 淘汰
        remaining_active = len(active) - len(to_degrade)
        if remaining_active > self.max_active_images:
            excess = remaining_active - self.max_active_images
            for entry in active:
                if entry.image_id not in to_degrade:
                    to_degrade.append(entry.image_id)
                    excess -= 1
                    if excess <= 0:
                        break

        # 规则 3：token 预算检查
        remaining_active_count = len(active) - len(to_degrade)
        active_tokens = remaining_active_count * IMAGE_TOKEN_ESTIMATE
        while active_tokens > self.image_token_budget and remaining_active_count > 0:
            # 找最老的还没被标记降级的
            for entry in active:
                if entry.image_id not in to_degrade:
                    to_degrade.append(entry.image_id)
                    remaining_active_count -= 1
                    active_tokens = remaining_active_count * IMAGE_TOKEN_ESTIMATE
                    break
            else:
                break

        return to_degrade

    _MAX_CACHE_SIZE = 10  # 降级后的缓存条目上限（防止长会话内存泄漏）

    def mark_degraded(self, image_id: int) -> None:
        """标记图片为已降级。超过缓存上限时淘汰最老的降级条目。"""
        entry = self._cache.get(image_id)
        if entry:
            entry.degraded = True
        # 淘汰最老的降级条目（保持 _cache 不无限增长）
        degraded = [e for e in self._cache.values() if e.degraded]
        if len(degraded) > self._MAX_CACHE_SIZE:
            degraded.sort(key=lambda e: e.last_referenced_round)
            for old in degraded[: len(degraded) - self._MAX_CACHE_SIZE]:
                del self._cache[old.image_id]

    def mark_round_sent(self, image_id: int, current_round: int) -> None:
        """更新图片的最后引用轮次。"""
        entry = self._cache.get(image_id)
        if entry and not entry.degraded:
            entry.last_referenced_round = current_round

    def get_reinject_data(self, image_id: int) -> ImageCacheEntry | None:
        """获取已降级图片的缓存数据（用于重注入）。"""
        entry = self._cache.get(image_id)
        if entry and entry.degraded and entry.raw_base64:
            return entry
        return None

    def clear(self) -> None:
        """清空所有缓存。"""
        self._cache.clear()

# ---------------------------------------------------------------------------
# 默认系统提示词：从 prompts/ 文件加载，缺失时自动补齐
# ---------------------------------------------------------------------------


def _load_system_prompt() -> str:
    """从 PromptComposer 加载系统提示词。

    若 prompts/core/ 目录缺失或文件不全，PromptComposer 会自动补齐后加载。
    """
    from excelmanus.prompt_composer import PromptComposer, PromptContext
    prompts_dir = Path(__file__).resolve().parent / "prompts"
    composer = PromptComposer(prompts_dir)
    composer.load_all()
    ctx = PromptContext(write_hint="unknown")
    return composer.compose_text(ctx)


_DEFAULT_SYSTEM_PROMPT = _load_system_prompt()


class TokenCounter:
    """基于 tiktoken 的 token 计数器。

    使用 cl100k_base 编码（GPT-4 系列），对 Qwen 等模型也能提供
    比字符估算更准确的近似值，用于 memory 截断判断。
    """

    _encoding = tiktoken.get_encoding("cl100k_base")

    @staticmethod
    def count(text: str) -> int:
        """计算文本的 token 数量。"""
        if not text:
            return 0
        return len(TokenCounter._encoding.encode(text))

    @staticmethod
    def count_message(message: dict) -> int:
        """计算单条消息的 token 数量（含结构开销）。"""
        tokens = 4  # 每条消息的固定开销（role、分隔符等）
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
    - 维护有序的消息列表
    - 提供 system prompt 始终在首位的消息序列
    - 当 token 总量接近上下文窗口限制时，截断最早的对话记录
    """

    def __init__(self, config: ExcelManusConfig) -> None:
        self._messages: list[dict] = []
        self._system_prompt: str = _DEFAULT_SYSTEM_PROMPT
        self._max_context_tokens: int = config.max_context_tokens
        self._token_counter = TokenCounter()
        # 预留 10% 的 token 空间给模型输出
        self._truncation_threshold = int(self._max_context_tokens * 0.9)
        # 图片降级追踪
        self._image_seq: int = 0  # 图片序号
        self._fresh_image_ids: set[int] = set()  # 尚未发送过的图片 ID
        # 图片生命周期管理器（视觉原生模式使用）
        self._lifecycle = ImageLifecycleManager(
            keep_rounds=getattr(config, "image_keep_rounds", 3),
            max_active_images=getattr(config, "image_max_active", 2),
            image_token_budget=getattr(config, "image_token_budget", 6000),
        )
        self._current_round: int = 0  # 当前对话轮次

    def update_context_window(self, max_context_tokens: int) -> None:
        """切换模型后更新上下文窗口大小和截断阈值。"""
        self._max_context_tokens = max(1, max_context_tokens)
        self._truncation_threshold = int(self._max_context_tokens * 0.9)

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
                self._messages.pop()
                return True
        return False

    def replace_message_content(self, index: int, content: str) -> bool:
        """替换指定位置的消息内容。越界时返回 False。"""
        if 0 <= index < len(self._messages):
            self._messages[index]["content"] = content
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

    def add_user_message(self, content: str | list[dict]) -> None:
        """添加用户消息。

        Args:
            content: 纯文本字符串或多模态 content parts 列表。
                     当 content 为列表且包含 image_url 类型的 part 时，
                     自动注册到图片追踪系统，使 mark_images_sent() 可以
                     在首轮 LLM 调用后将 base64 降级为文本引用。
        """
        # 检测多模态内容中的图片并注册追踪
        has_images = False
        if isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("type") == "image_url":
                    has_images = True
                    break

        if has_images:
            self._image_seq += 1
            image_id = self._image_seq
            self._messages.append({
                "role": "user", "content": content, "_image_id": image_id,
            })
            self._fresh_image_ids.add(image_id)
            # 注册到生命周期管理器（提取第一张图片的 base64/mime/detail）
            if isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "image_url":
                        img_url = part.get("image_url", {})
                        url_str = img_url.get("url", "")
                        detail = img_url.get("detail", "auto")
                        # 从 data URI 提取 base64 和 mime
                        if url_str.startswith("data:") and ";base64," in url_str:
                            header, b64_data = url_str.split(";base64,", 1)
                            mime_type = header.replace("data:", "")
                            self._lifecycle.register(
                                image_id, b64_data, mime_type, detail, self._current_round,
                            )
                        break  # 只注册第一张
        else:
            self._messages.append({"role": "user", "content": content})
        self._truncate_if_needed()

    def add_image_message(
        self, base64_data: str, mime_type: str = "image/png", detail: str = "auto",
    ) -> None:
        """便捷方法：注入图片到对话上下文。

        图片首次注入时保留完整 base64 数据；LLM 调用完成后通过
        ``mark_images_sent()`` 或 ``manage_image_lifecycle()`` 管理降级。
        """
        self._image_seq += 1
        image_id = self._image_seq
        part = {
            "type": "image_url",
            "image_url": {
                "url": f"data:{mime_type};base64,{base64_data}",
                "detail": detail,
            },
        }
        msg = {"role": "user", "content": [part], "_image_id": image_id}
        self._messages.append(msg)
        self._fresh_image_ids.add(image_id)
        # 注册到生命周期管理器
        self._lifecycle.register(
            image_id, base64_data, mime_type, detail, self._current_round,
        )
        self._truncate_if_needed()

    def add_assistant_message(self, content: str) -> None:
        """添加助手纯文本回复。"""
        self._messages.append({"role": "assistant", "content": content})
        self._truncate_if_needed()

    def add_tool_call(self, tool_call_id: str, name: str, arguments: str) -> None:
        """添加助手的工具调用消息。"""
        self._messages.append({
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": tool_call_id,
                    "type": "function",
                    "function": {"name": name, "arguments": arguments},
                }
            ],
        })
        self._truncate_if_needed()

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
        self._messages.append(normalized)
        self._truncate_if_needed()

    def add_tool_result(self, tool_call_id: str, content: str) -> None:
        """添加工具执行结果消息。"""
        self._messages.append({
            "role": "tool",
            "tool_call_id": tool_call_id,
            "content": content,
        })
        self._truncate_if_needed()

    def replace_tool_result(self, tool_call_id: str, content: str) -> bool:
        """替换已有工具结果消息的内容（按 tool_call_id 匹配最后一条）。

        用于审批通过后将审批提示替换为实际工具执行结果。
        返回是否成功替换。
        """
        for msg in reversed(self._messages):
            if msg.get("role") == "tool" and msg.get("tool_call_id") == tool_call_id:
                msg["content"] = content
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

    def get_messages(self, system_prompts: list[str] | None = None) -> list[dict]:
        """获取完整消息列表（system prompt + 对话历史）。

        对于图片消息，会过滤掉内部标记字段 ``_image_id`` / ``_image_downgraded``，
        确保不泄露到发送给 LLM 的消息中。

        Args:
            system_prompts:
                可选的 system 消息列表；为空时使用默认 system prompt。
        """
        system_msgs = self.build_system_messages(system_prompts)
        output: list[dict] = []
        for msg in self._messages:
            if msg.get("_image_id") is not None:
                clean = {k: v for k, v in msg.items() if not k.startswith("_image_")}
                output.append(clean)
            else:
                output.append(msg)
        return system_msgs + output

    def trim_for_request(
        self,
        system_prompts: list[str],
        max_context_tokens: int,
        reserve_ratio: float = 0.1,
    ) -> list[dict]:
        """按最终请求消息预算裁剪历史，返回可直接发送的消息列表。"""
        if max_context_tokens <= 0:
            return self.get_messages(system_prompts=system_prompts)
        ratio = reserve_ratio
        if ratio < 0:
            ratio = 0
        elif ratio >= 1:
            ratio = 0.99
        threshold = max(1, int(max_context_tokens * (1 - ratio)))
        system_msgs = self.build_system_messages(system_prompts)
        self._truncate_history_to_threshold(threshold, system_msgs=system_msgs)
        # 截断后修复消息序列：确保首条消息为 user 角色，
        # 避免部分 provider（Claude / GLM 等）因 assistant-first 拒绝请求。
        self._ensure_starts_with_user()
        # 过滤内部标记字段，与 get_messages 保持一致
        output: list[dict] = []
        for msg in self._messages:
            if msg.get("_image_id") is not None:
                clean = {k: v for k, v in msg.items() if not k.startswith("_image_")}
                output.append(clean)
            else:
                output.append(msg)
        # 对旧轮次的工具返回值做结构化遮蔽，节约上下文空间
        from excelmanus.engine_core.observation_masker import mask_messages
        output = mask_messages(output)
        # 剥离非标准字段（thinking/reasoning/reasoning_content 等），
        # 防止不同 LLM provider 因未知字段返回 400 错误
        output = _sanitize_messages_for_api(output)
        return system_msgs + output

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
                self._messages.append({
                    "role": "tool",
                    "tool_call_id": tc_id,
                    "content": "[任务已中断，该工具未执行完成]",
                })
                repaired += 1

        return repaired

    def inject_messages(self, messages: list[dict]) -> None:
        """注入历史消息（用于会话恢复）。不触发截断。"""
        self._messages.extend(messages)

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
            i for i, m in enumerate(self._messages) if m.get("role") == "user"
        ]
        if not user_indices or turn_index < 0 or turn_index >= len(user_indices):
            raise IndexError(
                f"用户轮次索引 {turn_index} 超出范围（共 {len(user_indices)} 轮）"
            )
        cut_after = user_indices[turn_index]
        if keep_target:
            removed_count = len(self._messages) - cut_after - 1
            self._messages = self._messages[: cut_after + 1]
        else:
            removed_count = len(self._messages) - cut_after
            self._messages = self._messages[:cut_after]
        return removed_count

    def list_user_turns(self) -> list[dict]:
        """列出所有用户轮次摘要，返回 [{index, content_preview, msg_index}]。"""
        turns: list[dict] = []
        turn_idx = 0
        for i, m in enumerate(self._messages):
            if m.get("role") == "user":
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

    def reset_image_tracking(self) -> None:
        """重置图片追踪状态（rollback 后调用）。

        清除 fresh IDs 和 lifecycle 缓存，保留 _image_seq 以避免
        新图片 ID 冲突。_current_round 同步到当前用户轮次数。
        """
        self._fresh_image_ids.clear()
        self._lifecycle.clear()
        # 同步 round 到当前剩余消息的用户轮次数
        user_count = sum(1 for m in self._messages if m.get("role") == "user")
        self._current_round = user_count

    def clear(self) -> None:
        """清除所有对话历史（保留 system prompt 配置）。"""
        self._messages.clear()
        self._image_seq = 0
        self._fresh_image_ids.clear()
        self._lifecycle.clear()
        self._current_round = 0

    def mark_images_sent(self) -> None:
        """将已发送的图片消息降级为文本引用，释放 base64 内存。

        在每轮 LLM 调用完成后调用。fresh 图片在本轮已随完整 base64
        发送给 LLM，后续轮次只需保留短文本引用即可。

        对于多模态消息（text + image 混合），保留原始文本部分，
        仅将 image_url 部分替换为短文本引用。

        注意：视觉原生模式应改用 ``manage_image_lifecycle()``。
        """
        if not self._fresh_image_ids:
            return
        for i, msg in enumerate(self._messages):
            image_id = msg.get("_image_id")
            if image_id is not None and image_id in self._fresh_image_ids:
                self._degrade_image_message(i, image_id)
        self._fresh_image_ids.clear()

    def manage_image_lifecycle(self) -> None:
        """Provider-aware 图片生命周期管理（视觉原生模式）。

        替代 ``mark_images_sent()`` 的粗暴降级策略：
        - 图片在 keep_rounds 内保持完整 base64（利用 Provider 缓存）
        - 超期或超数量时 LRU 淘汰
        - 降级后仍缓存原始数据，支持按需重注入
        """
        self._current_round += 1

        # 更新所有 fresh 图片的引用轮次
        for msg in self._messages:
            image_id = msg.get("_image_id")
            if image_id is not None and image_id in self._fresh_image_ids:
                self._lifecycle.mark_round_sent(image_id, self._current_round)
        self._fresh_image_ids.clear()

        # 获取需要降级的图片
        to_degrade = self._lifecycle.get_ids_to_degrade(self._current_round)
        if not to_degrade:
            return

        degrade_set = set(to_degrade)
        for i, msg in enumerate(self._messages):
            image_id = msg.get("_image_id")
            if image_id is not None and image_id in degrade_set:
                if not msg.get("_image_downgraded"):
                    self._degrade_image_message(i, image_id)
                    self._lifecycle.mark_degraded(image_id)
                    cached = self._lifecycle._cache.get(image_id)
                    age = self._current_round - cached.inject_round if cached else 0
                    logger.info("图片生命周期: 降级图片 #%d (age=%d rounds)", image_id, age)

    def reinject_image(self, image_id: int) -> bool:
        """重注入已降级的图片（从本地缓存恢复）。

        适用于用户追问图片细节时，图片已从上下文中降级的场景。

        Returns:
            True 如果成功重注入，False 如果缓存中无该图片。
        """
        entry = self._lifecycle.get_reinject_data(image_id)
        if entry is None:
            return False
        # 重新注入（不分配新 ID，复用原 ID）
        part = {
            "type": "image_url",
            "image_url": {
                "url": f"data:{entry.mime_type};base64,{entry.raw_base64}",
                "detail": entry.detail,
            },
        }
        msg = {"role": "user", "content": [part], "_image_id": image_id}
        self._messages.append(msg)
        self._fresh_image_ids.add(image_id)
        entry.degraded = False
        entry.inject_round = self._current_round  # 重置注入轮次，避免立即被再次降级
        entry.last_referenced_round = self._current_round
        logger.info("图片生命周期: 重注入图片 #%d", image_id)
        self._truncate_if_needed()
        return True

    def _degrade_image_message(self, msg_index: int, image_id: int) -> None:
        """将指定消息中的图片降级为文本引用。"""
        msg = self._messages[msg_index]
        original_text = ""
        content = msg.get("content")
        if isinstance(content, list):
            text_parts = [
                p.get("text", "")
                for p in content
                if isinstance(p, dict) and p.get("type") == "text"
            ]
            original_text = "\n".join(t for t in text_parts if t)

        image_ref = f"[图片 #{image_id} 已在之前的对话中发送]"
        degraded_content = (
            f"{original_text}\n{image_ref}" if original_text else image_ref
        )
        self._messages[msg_index] = {
            "role": "user",
            "content": degraded_content,
            "_image_id": image_id,
            "_image_downgraded": True,
        }

    def _total_tokens(self) -> int:
        """计算当前所有消息（含 system prompt）的总 token 数。"""
        system_msg = {"role": "system", "content": self._system_prompt}
        total = self._token_counter.count_message(system_msg)
        for msg in self._messages:
            total += self._token_counter.count_message(msg)
        return total

    def _truncate_if_needed(self) -> None:
        """当 token 总量超过阈值时，从最早的消息开始截断。

        截断策略：
        1. 始终保留 system prompt（不在 _messages 中，由 get_messages 拼接）
        2. 从 _messages 头部逐条移除最早的消息
        3. 跳过孤立的 tool 结果消息（确保 tool_call 和 tool_result 成对移除）
        """
        self._truncate_history_to_threshold(self._truncation_threshold, system_msgs=None)

    def _total_tokens_with_system_messages(self, system_msgs: list[dict] | None) -> int:
        total = 0
        if system_msgs is None:
            system_msg = {"role": "system", "content": self._system_prompt}
            total += self._token_counter.count_message(system_msg)
        else:
            for msg in system_msgs:
                total += self._token_counter.count_message(msg)
        for msg in self._messages:
            total += self._token_counter.count_message(msg)
        return total

    def _truncate_history_to_threshold(
        self,
        threshold: int,
        system_msgs: list[dict] | None,
    ) -> None:
        while self._messages and self._total_tokens_with_system_messages(system_msgs) > threshold:
            # 仅剩最后一条时做内容收缩，避免单条超长消息长期越阈值。
            if len(self._messages) == 1:
                if not self._shrink_last_message_for_threshold(threshold, system_msgs):
                    # 无法收缩（例如 content 为 None 的 tool_call 壳消息）时，
                    # 直接丢弃最后一条，保证请求不会持续超预算。
                    self._messages.pop(0)
                    break
                # 收缩后仍可能因 system 过大而超阈值，此时保留最后一条不删
                if self._total_tokens_with_system_messages(system_msgs) > threshold:
                    break
                continue

            # 移除最早的消息，但至少保留最后一条（最近的消息）
            removed = self._messages.pop(0)

            # 如果移除的是带 tool_calls 的 assistant 消息，
            # 需要同时移除对应的 tool result 消息
            if removed.get("tool_calls"):
                call_ids = {
                    tc["id"] for tc in removed["tool_calls"] if "id" in tc
                }
                # 移除所有匹配的 tool result（它们紧跟在 tool_call 之后）
                self._messages = [
                    m for m in self._messages
                    if not (
                        m.get("role") == "tool"
                        and m.get("tool_call_id") in call_ids
                    )
                ]

            # 如果最早的消息是孤立的 tool result（对应的 tool_call 已不存在），
            # 继续移除以保持消息一致性。
            # 注意：必须检查 tool_call_id 是否真的孤立，避免误删有效的 tool result。
            while self._messages and self._messages[0].get("role") == "tool":
                # 收集剩余消息中所有有效的 tool_call id
                valid_call_ids: set[str] = {
                    tc["id"]
                    for m in self._messages
                    if m.get("tool_calls")
                    for tc in m["tool_calls"]
                    if "id" in tc
                }
                head_call_id = self._messages[0].get("tool_call_id")
                if head_call_id in valid_call_ids:
                    # 对应的 tool_call 仍存在，不是孤立消息，停止清理
                    break
                self._messages.pop(0)

    def _ensure_starts_with_user(self) -> None:
        """确保 _messages 首条消息为 user 角色。

        截断可能导致首条消息为 assistant（带或不带 tool_calls），
        部分 provider（Claude / GLM）要求首条非 system 消息必须是 user。
        此方法移除前导的非 user 消息及其关联的 tool result，直到
        遇到 user 消息或列表为空。
        """
        while self._messages and self._messages[0].get("role") != "user":
            removed = self._messages.pop(0)
            # 移除被删 assistant 的关联 tool results
            if removed.get("tool_calls"):
                call_ids = {
                    tc["id"] for tc in removed["tool_calls"]
                    if isinstance(tc, dict) and "id" in tc
                }
                if call_ids:
                    self._messages = [
                        m for m in self._messages
                        if not (
                            m.get("role") == "tool"
                            and m.get("tool_call_id") in call_ids
                        )
                    ]
            # 清理可能暴露在头部的孤立 tool results
            while self._messages and self._messages[0].get("role") == "tool":
                valid_call_ids: set[str] = {
                    tc["id"]
                    for m in self._messages
                    if m.get("tool_calls")
                    for tc in m["tool_calls"]
                    if isinstance(tc, dict) and "id" in tc
                }
                if self._messages[0].get("tool_call_id") in valid_call_ids:
                    break
                self._messages.pop(0)

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

        message_tokens = self._token_counter.count_message(msg)
        content_tokens = self._token_counter.count(content)
        base_tokens = message_tokens - content_tokens
        budget_for_content = threshold - (
            self._total_tokens_with_system_messages(system_msgs) - message_tokens
        ) - base_tokens
        if budget_for_content <= 0:
            msg["content"] = ""
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
            return True

        if len(best) >= len(content):
            best = content[-max(1, len(content) // 2):]

        # 防止收缩后内容未变导致外层 while 无限循环
        if best == content:
            msg["content"] = ""
            return True

        msg["content"] = best
        return True

    async def summarize_and_trim(
        self,
        threshold: int,
        system_msgs: list[dict] | None,
        *,
        client: object,
        summary_model: str,
        keep_recent_turns: int = 3,
    ) -> bool:
        """超阈值时：用轻量模型摘要旧消息 + 保留最近 N 轮。

        Args:
            threshold: token 阈值
            system_msgs: 当前系统消息（用于 token 计算）
            client: openai.AsyncOpenAI 兼容客户端
            summary_model: 摘要模型名称
            keep_recent_turns: 保留最近的 user turn 数

        Returns:
            是否执行了摘要操作
        """
        if self._total_tokens_with_system_messages(system_msgs) <= threshold:
            return False

        # 找到最近 keep_recent_turns 个 user 消息的起始索引
        user_indices = [
            i for i, m in enumerate(self._messages)
            if m.get("role") == "user"
        ]
        if len(user_indices) <= keep_recent_turns:
            # 消息太少，不值得摘要，走硬截断
            self._truncate_history_to_threshold(threshold, system_msgs)
            return False

        split_idx = user_indices[-keep_recent_turns]
        old_messages = self._messages[:split_idx]
        recent_messages = self._messages[split_idx:]

        if not old_messages:
            self._truncate_history_to_threshold(threshold, system_msgs)
            return False

        from excelmanus.memory_summarizer import summarize_history

        summary_text = await summarize_history(
            client, summary_model, old_messages,
        )

        if not summary_text:
            # 摘要失败，走硬截断兜底
            logger.warning("对话摘要为空，回退到硬截断")
            self._truncate_history_to_threshold(threshold, system_msgs)
            return False

        # 用两条合成消息替换旧历史
        synthetic: list[dict] = [
            {"role": "user", "content": "[系统] 请基于以下摘要继续工作。"},
            {"role": "assistant", "content": f"[对话摘要]\n{summary_text}"},
        ]
        self._messages = synthetic + recent_messages

        # 如果摘要后仍然超限，走硬截断兜底
        if self._total_tokens_with_system_messages(system_msgs) > threshold:
            self._truncate_history_to_threshold(threshold, system_msgs)

        logger.info(
            "对话历史已摘要压缩: %d 条旧消息 → 2 条合成消息 + %d 条保留消息",
            len(old_messages), len(recent_messages),
        )
        return True
