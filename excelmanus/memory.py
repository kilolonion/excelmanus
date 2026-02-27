"""对话记忆模块：管理多轮对话上下文与 token 截断。"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

import tiktoken

from excelmanus.config import ExcelManusConfig

logger = logging.getLogger(__name__)

IMAGE_TOKEN_ESTIMATE = 1500  # 图片 token 估算值（用于 memory 截断）

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
        else:
            self._messages.append({"role": "user", "content": content})
        self._truncate_if_needed()

    def add_image_message(
        self, base64_data: str, mime_type: str = "image/png", detail: str = "auto",
    ) -> None:
        """便捷方法：注入图片到对话上下文。

        图片首次注入时保留完整 base64 数据；LLM 调用完成后通过
        ``mark_images_sent()`` 将其降级为短文本引用，避免后续轮次
        重复携带巨大的 base64 payload。
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
        # 过滤内部标记字段，与 get_messages 保持一致
        output: list[dict] = []
        for msg in self._messages:
            if msg.get("_image_id") is not None:
                clean = {k: v for k, v in msg.items() if not k.startswith("_image_")}
                output.append(clean)
            else:
                output.append(msg)
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

    def clear(self) -> None:
        """清除所有对话历史（保留 system prompt 配置）。"""
        self._messages.clear()
        self._image_seq = 0
        self._fresh_image_ids.clear()

    def mark_images_sent(self) -> None:
        """将已发送的图片消息降级为文本引用，释放 base64 内存。

        在每轮 LLM 调用完成后调用。fresh 图片在本轮已随完整 base64
        发送给 LLM，后续轮次只需保留短文本引用即可。

        对于多模态消息（text + image 混合），保留原始文本部分，
        仅将 image_url 部分替换为短文本引用。
        """
        if not self._fresh_image_ids:
            return
        for i, msg in enumerate(self._messages):
            image_id = msg.get("_image_id")
            if image_id is not None and image_id in self._fresh_image_ids:
                # 在多模态内容降级前提取文本部分
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
                self._messages[i] = {
                    "role": "user",
                    "content": degraded_content,
                    "_image_id": image_id,
                    "_image_downgraded": True,
                }
        self._fresh_image_ids.clear()

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
