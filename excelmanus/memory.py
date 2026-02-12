"""对话记忆模块：管理多轮对话上下文与 token 截断。"""

from __future__ import annotations

from excelmanus.config import ExcelManusConfig

# 默认系统提示词，描述 ExcelManus 的能力
_DEFAULT_SYSTEM_PROMPT = (
    "你是 ExcelManus，一个智能 Excel 操作助手。"
    "你可以通过工具调用来完成以下任务：\n"
    "- 读取和写入 Excel 文件\n"
    "- 数据分析与筛选\n"
    "- 数据转换\n"
    "- 生成图表（柱状图、折线图、饼图、散点图、雷达图）\n"
    "- 单元格格式化与列宽调整\n\n"
    "请根据用户的自然语言指令，选择合适的工具完成操作。"
    "如果需要多步操作，请逐步执行并在完成后汇报结果。"
)


class TokenCounter:
    """简易 token 计数器。

    采用启发式估算：英文约 4 字符/token，中文约 2 字符/token。
    为简化实现，统一使用 1 token ≈ 3 字符的折中估算。
    """

    @staticmethod
    def count(text: str) -> int:
        """估算文本的 token 数量。"""
        if not text:
            return 0
        return max(1, len(text) // 3)

    @staticmethod
    def count_message(message: dict) -> int:
        """估算单条消息的 token 数量（含结构开销）。"""
        tokens = 4  # 每条消息的固定开销（role、分隔符等）
        for key, value in message.items():
            if value is None:
                continue
            if isinstance(value, str):
                tokens += TokenCounter.count(value)
            elif isinstance(value, list):
                # tool_calls 列表：序列化后估算
                tokens += TokenCounter.count(str(value))
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
        self._max_context_tokens: int = 128_000
        self._token_counter = TokenCounter()
        # 预留 10% 的 token 空间给模型输出
        self._truncation_threshold = int(self._max_context_tokens * 0.9)

    @property
    def system_prompt(self) -> str:
        """获取当前系统提示词。"""
        return self._system_prompt

    @system_prompt.setter
    def system_prompt(self, value: str) -> None:
        """设置系统提示词。"""
        self._system_prompt = value

    def add_user_message(self, content: str) -> None:
        """添加用户消息。"""
        self._messages.append({"role": "user", "content": content})
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

    def get_messages(self) -> list[dict]:
        """获取完整消息列表（system prompt + 对话历史）。

        返回的列表以 system 消息开头，后接对话历史。
        """
        system_msg = {"role": "system", "content": self._system_prompt}
        return [system_msg] + list(self._messages)

    def clear(self) -> None:
        """清除所有对话历史（保留 system prompt 配置）。"""
        self._messages.clear()

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
        while self._messages and self._total_tokens() > self._truncation_threshold:
            # 仅剩最后一条时做内容收缩，避免单条超长消息长期越阈值。
            if len(self._messages) == 1:
                if not self._shrink_last_message():
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
            # 继续移除以保持消息一致性
            while (
                self._messages
                and self._messages[0].get("role") == "tool"
            ):
                self._messages.pop(0)

    def _shrink_last_message(self) -> bool:
        """尽量收缩最后一条消息内容，返回是否完成收缩。"""
        msg = self._messages[0]
        content = msg.get("content")
        if not isinstance(content, str) or not content:
            return False

        total = self._total_tokens()
        overflow_tokens = total - self._truncation_threshold
        if overflow_tokens <= 0:
            return True

        # 估算每 token 约 3 字符，保留消息尾部（最近内容更有价值）。
        overflow_chars = max(1, overflow_tokens * 3)
        keep_chars = len(content) - overflow_chars - 8
        if keep_chars <= 0:
            msg["content"] = ""
            return True

        new_content = f"[截断]{content[-keep_chars:]}"
        # 保证每次收缩都确实缩短，避免在极低阈值下死循环。
        if len(new_content) >= len(content):
            new_content = content[-max(1, len(content) // 2):]
        msg["content"] = new_content
        return True
