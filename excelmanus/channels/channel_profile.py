"""渠道 Profile：定义各平台的 LLM-facing 特征描述和输出适配提示词。

当消息来自 Bot 渠道时，将渠道特定的格式/交互指南注入 system prompt，
让 LLM 针对目标平台优化输出。Web UI 不注入任何额外提示（零开销）。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ChannelProfile:
    """渠道输出特征描述。"""

    name: str                         # "telegram" | "qq" | "feishu"
    display_name: str                 # 用户可见名称
    default_max_message_length: int   # 平台消息字符上限（回退默认值）
    supports_markdown_tables: bool    # 平台是否渲染 Markdown 表格
    format_guidelines: str            # 格式指南（注入 LLM）
    interaction_guidelines: str       # 交互指南（注入 LLM）
    compact_guidelines: str = ""      # 精简版指南（token 紧张时使用）


# ── 通用 Bot 渠道指南（所有 Bot 渠道共享） ──

_COMMON_BOT_GUIDELINES = """\
**分块友好**
- 每个独立段落/步骤用空行分隔，方便自动分块发送
- 单次回复控制在 3000 字以内；超长内容主动分段，用标题或分隔线分隔
- 避免单个超长段落（>500 字不换行）

**回复风格**
- 每个步骤完成后给简要确认（一句话），不要攒到最后一起汇报
- 使用 emoji 标记状态（✅ 成功 / ❌ 失败 / ⚠️ 警告 / 📊 数据）增强可读性
- 错误信息保持简洁（1-2 行摘要），不要输出完整堆栈或调试日志

**数据展示**
- 用户无法实时预览文件，数据结果请在消息中给出关键摘要（前几行 + 统计）
- 文件操作完成后明确说明文件名，用户可通过命令下载
- 图表/可视化无法内嵌显示，生成后提示用户下载文件查看

**交互适配**
- 在自由文本中需要用户选择时，输出编号选项（1/2/3），方便用户快速回复数字
- 使用 ask_user 工具时，选项会自动渲染为平台按钮，无需在文本中重复编号
- 长任务开头重述目标（Bot 聊天历史容易被推远）
- 代码块控制行宽 ≤60 字符，避免小屏水平滚动

**多文件操作**
- 涉及多个文件变更时，在消息中内联摘要每个文件的变更（+N行/-M行，改了什么）
- 不要只说"已完成"，用户无法打开 diff 面板查看

**公式与 VBA**
- 用户无法在 Bot 中复制粘贴公式到 Excel，应直接通过工具写入单元格
- 建议公式/VBA 方案时，主动提出"我来帮你直接写入"而非让用户手动操作

**安全脱敏**
- Bot 消息可能被转发，不要在回复中暴露文件路径中的用户名、系统路径等敏感信息
- 引用文件时只使用文件名，不输出完整绝对路径"""


_TELEGRAM_FORMAT = """\
- 仅使用基础 Markdown：**粗体**、_斜体_、`行内代码`、```代码块```
- **不要使用 Markdown 表格**（Telegram 不渲染），改用对齐的纯文本或代码块展示表格数据
- 列表用 `- ` 或 `1. `，避免深层嵌套（>2 层）
- 链接使用 [文本](URL) 格式"""

_TELEGRAM_INTERACTION = """\
- Telegram 消息上限约 4096 字符，超长回复会被自动分块
- 用户可通过 Bot 命令（/help /new /mode 等）控制会话"""


_QQ_FORMAT = """\
- 使用纯文本为主，QQ 对 Markdown 支持有限
- **不要使用 Markdown 表格**，改用代码块或对齐文本
- 代码块用 ``` 包裹，保持简短
- 粗体/斜体等标记可能不渲染，用【】或 emoji 替代强调"""

_QQ_INTERACTION = """\
- QQ 消息上限较短（约 2000 字符），回复务必简洁
- 被动回复窗口有时间限制，长任务中保持定期反馈
- 用户通过文字命令交互，避免依赖按钮或卡片"""


_FEISHU_FORMAT = """\
- 飞书支持富文本和卡片消息，可使用较丰富的 Markdown
- 可使用 Markdown 表格（飞书能渲染）
- 支持粗体、斜体、代码块、引用块、有序/无序列表
- 代码块标注语言以获得语法高亮"""

_FEISHU_INTERACTION = """\
- 飞书消息上限约 4000 字符，超长自动分块
- 支持卡片式交互，审批/问答会以卡片形式展示
- 用户可在卡片上直接点击按钮操作"""


# ── 精简版指南（token 紧张时使用） ──

_COMPACT_GUIDELINES = """\
- 回复简洁（≤3000字），段落间空行分隔，代码行宽≤60
- emoji 标记状态（✅❌⚠️📊），错误信息1-2行
- 文件操作后说明文件名，数据给关键摘要
- 不输出完整路径/堆栈，引用文件只用文件名"""


CHANNEL_PROFILES: dict[str, ChannelProfile] = {
    "telegram": ChannelProfile(
        name="telegram",
        display_name="Telegram Bot",
        default_max_message_length=4096,
        supports_markdown_tables=False,
        format_guidelines=_TELEGRAM_FORMAT,
        interaction_guidelines=_TELEGRAM_INTERACTION,
        compact_guidelines=_COMPACT_GUIDELINES,
    ),
    "qq": ChannelProfile(
        name="qq",
        display_name="QQ Bot",
        default_max_message_length=2000,
        supports_markdown_tables=False,
        format_guidelines=_QQ_FORMAT,
        interaction_guidelines=_QQ_INTERACTION,
        compact_guidelines=_COMPACT_GUIDELINES,
    ),
    "feishu": ChannelProfile(
        name="feishu",
        display_name="飞书 Bot",
        default_max_message_length=4000,
        supports_markdown_tables=True,
        format_guidelines=_FEISHU_FORMAT,
        interaction_guidelines=_FEISHU_INTERACTION,
        compact_guidelines=_COMPACT_GUIDELINES,
    ),
}


def get_channel_profile(channel: str | None) -> ChannelProfile | None:
    """获取渠道 Profile。Web / None / 未知渠道返回 None。"""
    if not channel or channel == "web":
        return None
    return CHANNEL_PROFILES.get(channel)


def build_channel_notice(
    channel: str | None,
    *,
    max_message_length: int | None = None,
    compact: bool = False,
) -> str:
    """根据渠道名组装 system prompt 注入文本。

    Web UI 或未知渠道返回空字符串（零开销）。

    Args:
        channel: 渠道标识符，如 "telegram" / "qq" / "feishu" / "web" / None。
        max_message_length: 运行时实际消息上限（来自 adapter capabilities），
            优先于 Profile 中的 default_max_message_length。
        compact: 若为 True，使用精简版指南（token 紧张时）。

    Returns:
        格式化的提示词文本，或空字符串。
    """
    profile = get_channel_profile(channel)
    if profile is None:
        return ""

    effective_max = max_message_length or profile.default_max_message_length

    if compact and profile.compact_guidelines:
        return (
            f"## 输出渠道适配\n"
            f"当前用户通过 **{profile.display_name}** 交互（消息上限 {effective_max} 字符）。\n\n"
            f"{profile.compact_guidelines}"
        )

    return (
        f"## 输出渠道适配\n"
        f"当前用户通过 **{profile.display_name}** 与你交互，"
        f"消息上限约 {effective_max} 字符。"
        f"请遵循以下规则：\n\n"
        f"{_COMMON_BOT_GUIDELINES}\n\n"
        f"**格式限制（{profile.display_name}）**\n"
        f"{profile.format_guidelines}\n\n"
        f"**平台特性**\n"
        f"{profile.interaction_guidelines}"
    )
