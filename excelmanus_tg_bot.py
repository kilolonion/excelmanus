"""ExcelManus Telegram Bot — 通过 Telegram 与 ExcelManus API 交互。

用法：
  EXCELMANUS_TG_TOKEN=xxx python3 excelmanus_tg_bot.py

环境变量：
  EXCELMANUS_TG_TOKEN   — Telegram Bot Token（必填）
  EXCELMANUS_API_URL    — ExcelManus API 地址（默认 http://localhost:8000）
  EXCELMANUS_TG_USERS   — 允许使用的 Telegram user ID，逗号分隔（留空=不限制）
"""

import asyncio
import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

import httpx
from telegram import Update, BotCommand, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)
from telegram.constants import ParseMode, ChatAction

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("excelmanus-tg")

API_URL = os.environ.get("EXCELMANUS_API_URL", "http://localhost:8000")
TG_TOKEN = os.environ.get("EXCELMANUS_TG_TOKEN", "")
ALLOWED_USERS: set[int] = set()
_raw = os.environ.get("EXCELMANUS_TG_USERS", "")
if _raw.strip():
    ALLOWED_USERS = {int(uid.strip()) for uid in _raw.split(",") if uid.strip()}

# 每个用户维护一个 session_id
user_sessions: dict[int, str] = {}

# 待处理的审批/问题（user_id → 信息）
pending_interactions: dict[int, dict] = {}

# HTTP client
http_client = httpx.AsyncClient(timeout=httpx.Timeout(300, connect=10))


def _check_user(user_id: int) -> bool:
    if not ALLOWED_USERS:
        return True
    return user_id in ALLOWED_USERS


@dataclass
class ChatResult:
    """流式聊天的结构化结果。"""
    reply: str = ""
    session_id: str = ""
    tool_calls: list[dict] = field(default_factory=list)
    approval: dict | None = None      # pending_approval 事件
    question: dict | None = None       # user_question 事件
    error: str | None = None


async def _stream_chat(session_id: str | None, message: str) -> ChatResult:
    """调用 ExcelManus SSE 流式接口，返回结构化结果。"""
    payload: dict = {"message": message}
    if session_id:
        payload["session_id"] = session_id

    result = ChatResult(session_id=session_id or "")
    reply_parts: list[str] = []

    async with http_client.stream(
        "POST",
        f"{API_URL}/api/v1/chat/stream",
        json=payload,
        headers={"Accept": "text/event-stream"},
    ) as resp:
        resp.raise_for_status()
        event_type = ""

        async for line in resp.aiter_lines():
            if line.startswith("event:"):
                event_type = line[6:].strip()
                continue
            if not line.startswith("data:"):
                continue
            try:
                data = json.loads(line[5:].strip())
            except json.JSONDecodeError:
                continue

            if event_type == "session_init":
                result.session_id = data.get("session_id", result.session_id)

            elif event_type in ("text", "text_delta"):
                chunk = data.get("content", "")
                if chunk:
                    reply_parts.append(chunk)

            elif event_type == "tool_call_start":
                result.tool_calls.append({
                    "name": data.get("tool_name", "unknown"),
                    "status": "running",
                })

            elif event_type == "tool_call_end":
                name = data.get("tool_name", "")
                success = data.get("success", True)
                for tc in reversed(result.tool_calls):
                    if tc["name"] == name and tc["status"] == "running":
                        tc["status"] = "done" if success else "error"
                        break

            elif event_type == "pending_approval":
                result.approval = data

            elif event_type == "user_question":
                result.question = data

            elif event_type == "reply":
                content = data.get("content", "")
                if content and not reply_parts:
                    reply_parts.append(content)

            elif event_type == "error":
                result.error = data.get("error", "未知错误")

            event_type = ""

    # 组装回复文本
    sections: list[str] = []
    if result.tool_calls:
        icons = {"done": "✅", "error": "❌", "running": "🔧"}
        chain = " → ".join(
            f"{icons.get(tc['status'], '🔧')} {tc['name']}" for tc in result.tool_calls
        )
        sections.append(f"⚙️ {chain}")
    text = "".join(reply_parts).strip()
    if text:
        sections.append(text)
    if result.error:
        sections.append(f"❌ {result.error}")
    result.reply = "\n\n".join(sections) if sections else ""
    return result


def _split_message(text: str, max_len: int = 4000) -> list[str]:
    if len(text) <= max_len:
        return [text]
    parts = []
    while text:
        if len(text) <= max_len:
            parts.append(text)
            break
        idx = text.rfind("\n", 0, max_len)
        if idx < max_len // 2:
            idx = max_len
        parts.append(text[:idx])
        text = text[idx:].lstrip("\n")
    return parts


def _risk_emoji(level: str) -> str:
    return {"green": "🟢", "yellow": "🟡", "red": "🔴"}.get(level.lower(), "⚠️")


async def _send_reply(message, text: str) -> None:
    """发送回复，Markdown 失败时降级纯文本。"""
    for part in _split_message(text):
        try:
            await message.reply_text(part, parse_mode=ParseMode.MARKDOWN)
        except Exception:
            await message.reply_text(part)


async def _send_approval(chat_id: int, user_id: int, approval: dict, context) -> None:
    """发送审批消息，带 inline keyboard。"""
    aid = approval.get("approval_id", "")
    tool = approval.get("approval_tool_name", "unknown")
    risk = approval.get("risk_level", "yellow")
    args = approval.get("args_summary", {})

    # 构建参数摘要（简洁版）
    args_text = ""
    if args:
        lines = []
        for k, v in list(args.items())[:5]:
            v_str = str(v)
            if len(v_str) > 80:
                v_str = v_str[:77] + "..."
            lines.append(f"  {k}: {v_str}")
        args_text = "\n".join(lines)

    text = f"🔒 操作审批\n\n{_risk_emoji(risk)} 风险等级: {risk.upper()}\n📝 工具: {tool}"
    if args_text:
        text += f"\n\n{args_text}"

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ 批准", callback_data=f"approve:{aid}"),
            InlineKeyboardButton("❌ 拒绝", callback_data=f"reject:{aid}"),
        ]
    ])

    msg = await context.bot.send_message(
        chat_id=chat_id, text=text, reply_markup=keyboard
    )

    # 记录待处理审批
    pending_interactions[user_id] = {
        "type": "approval",
        "approval_id": aid,
        "message_id": msg.message_id,
        "chat_id": chat_id,
    }


async def _send_question(chat_id: int, user_id: int, question: dict, context) -> None:
    """发送 askuser 问题，有选项时用 inline keyboard。"""
    qid = question.get("id", "")
    header = question.get("header", "")
    text = question.get("text", "")
    options = question.get("options", [])

    msg_text = "💬 ExcelManus 想确认：\n"
    if header:
        msg_text += f"\n{header}\n"
    if text:
        msg_text += f"\n{text}"

    if options:
        keyboard_buttons = []
        for i, opt in enumerate(options):
            label = opt.get("label", f"选项 {i+1}")
            keyboard_buttons.append(
                [InlineKeyboardButton(label, callback_data=f"answer:{qid}:{label}")]
            )
        keyboard_buttons.append(
            [InlineKeyboardButton("💬 自由回复（直接发文字）", callback_data="noop")]
        )
        keyboard = InlineKeyboardMarkup(keyboard_buttons)
        msg = await context.bot.send_message(
            chat_id=chat_id, text=msg_text, reply_markup=keyboard
        )
    else:
        msg_text += "\n\n直接回复文字即可"
        msg = await context.bot.send_message(chat_id=chat_id, text=msg_text)

    pending_interactions[user_id] = {
        "type": "question",
        "question_id": qid,
        "message_id": msg.message_id,
        "chat_id": chat_id,
    }


async def _handle_chat_result(
    update: Update, context: ContextTypes.DEFAULT_TYPE,
    chat_result: ChatResult, user_id: int
) -> None:
    """处理聊天结果：发送回复、审批、问题。"""
    chat_id = update.effective_chat.id

    # 发送文本回复
    if chat_result.reply:
        await _send_reply(update.message, chat_result.reply)

    # 发送审批请求
    if chat_result.approval:
        await _send_approval(chat_id, user_id, chat_result.approval, context)

    # 发送问题
    if chat_result.question:
        await _send_question(chat_id, user_id, chat_result.question, context)

    # 如果什么都没有
    if not chat_result.reply and not chat_result.approval and not chat_result.question:
        await update.message.reply_text("（无回复内容）")


# ── 回调处理（inline keyboard 按钮点击）──

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.data:
        return
    await query.answer()

    user_id = query.from_user.id if query.from_user else 0
    if not _check_user(user_id):
        return

    data = query.data

    if data == "noop":
        return

    if data.startswith("approve:") or data.startswith("reject:"):
        action, aid = data.split(":", 1)
        session_id = user_sessions.get(user_id)
        cmd = f"/accept {aid}" if action == "approve" else f"/reject {aid}"

        # 更新按钮消息
        result_text = "✅ 已批准" if action == "approve" else "❌ 已拒绝"
        try:
            old_text = query.message.text or ""
            await query.edit_message_text(
                text=f"{old_text}\n\n{result_text}",
                reply_markup=None,
            )
        except Exception:
            pass

        # 发送命令给 API
        try:
            await update.effective_chat.send_action(ChatAction.TYPING)
            chat_result = await _stream_chat(session_id, cmd)
            user_sessions[user_id] = chat_result.session_id

            if chat_result.reply:
                for part in _split_message(chat_result.reply):
                    try:
                        await context.bot.send_message(
                            chat_id=query.message.chat_id,
                            text=part,
                            parse_mode=ParseMode.MARKDOWN,
                        )
                    except Exception:
                        await context.bot.send_message(
                            chat_id=query.message.chat_id, text=part
                        )

            # 可能还有后续审批
            if chat_result.approval:
                await _send_approval(
                    query.message.chat_id, user_id, chat_result.approval, context
                )
            if chat_result.question:
                await _send_question(
                    query.message.chat_id, user_id, chat_result.question, context
                )

        except Exception as e:
            logger.exception("Approval callback error")
            await context.bot.send_message(
                chat_id=query.message.chat_id, text=f"❌ 处理审批失败: {e}"
            )

        pending_interactions.pop(user_id, None)
        return

    if data.startswith("answer:"):
        parts = data.split(":", 2)
        if len(parts) < 3:
            return
        _, qid, answer = parts
        session_id = user_sessions.get(user_id)

        # 更新按钮消息
        try:
            old_text = query.message.text or ""
            await query.edit_message_text(
                text=f"{old_text}\n\n💬 已回答: {answer}",
                reply_markup=None,
            )
        except Exception:
            pass

        # 发送回答
        try:
            await update.effective_chat.send_action(ChatAction.TYPING)
            chat_result = await _stream_chat(session_id, answer)
            user_sessions[user_id] = chat_result.session_id

            if chat_result.reply:
                for part in _split_message(chat_result.reply):
                    try:
                        await context.bot.send_message(
                            chat_id=query.message.chat_id,
                            text=part,
                            parse_mode=ParseMode.MARKDOWN,
                        )
                    except Exception:
                        await context.bot.send_message(
                            chat_id=query.message.chat_id, text=part
                        )

            if chat_result.approval:
                await _send_approval(
                    query.message.chat_id, user_id, chat_result.approval, context
                )
            if chat_result.question:
                await _send_question(
                    query.message.chat_id, user_id, chat_result.question, context
                )

        except Exception as e:
            logger.exception("Question callback error")
            await context.bot.send_message(
                chat_id=query.message.chat_id, text=f"❌ 处理回答失败: {e}"
            )

        pending_interactions.pop(user_id, None)


# ── 命令处理 ──

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or not update.message:
        return
    if not _check_user(update.effective_user.id):
        await update.message.reply_text("⛔ 无权限使用此 Bot")
        return
    await update.message.reply_text(
        "👋 ExcelManus Bot 已就绪！\n\n"
        "直接发消息即可与 ExcelManus 对话。\n"
        "发送 Excel 文件（.xlsx/.xls/.csv）可上传到工作区。\n\n"
        "输入 /help 查看所有命令"
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or not update.message:
        return
    if not _check_user(update.effective_user.id):
        return
    await update.message.reply_text(
        "📖 ExcelManus Bot 命令\n\n"
        "💬 对话\n"
        "  直接发文字 → 与 AI 对话\n"
        "  发送文件 → 上传到工作区并分析\n"
        "  /new — 新建对话（清除历史）\n\n"
        "🤖 模型管理\n"
        "  /model — 查看模型列表\n"
        "  /model <名称> — 切换模型\n"
        "  /addmodel — 添加新模型（查看格式）\n"
        "  /delmodel <名称> — 删除模型\n\n"
        "📎 支持的文件\n"
        "  Excel: .xlsx .xls .csv\n"
        "  图片: .png .jpg .jpeg"
    )


async def cmd_new(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or not update.message:
        return
    if not _check_user(update.effective_user.id):
        return
    user_sessions.pop(update.effective_user.id, None)
    pending_interactions.pop(update.effective_user.id, None)
    await update.message.reply_text("🆕 已新建对话，历史已清除。")


async def cmd_model(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or not update.message:
        return
    if not _check_user(update.effective_user.id):
        return

    args = context.args
    try:
        resp = await http_client.get(f"{API_URL}/api/v1/models")
        resp.raise_for_status()
        models = resp.json().get("models", [])

        if args:
            target = " ".join(args)
            switch_resp = await http_client.put(
                f"{API_URL}/api/v1/models/active",
                json={"name": target},
            )
            if switch_resp.status_code == 200:
                await update.message.reply_text(f"✅ 已切换到模型: {target}")
            else:
                err = switch_resp.json().get("error", "未知错误")
                await update.message.reply_text(f"❌ 切换失败: {err}")
            return

        if not models:
            await update.message.reply_text("暂无可用模型")
            return

        lines = ["🤖 可用模型：\n"]
        for m in models:
            name = m["name"]
            model_id = m.get("model", "")
            desc = m.get("description", "")
            line = f"  {'→ ' if m.get('active') else '   '}{name}"
            if m.get("active"):
                line += " ✅"
            line += f"\n    {model_id}"
            if desc:
                line += f"\n    {desc}"
            lines.append(line)

        lines.append(f"\n切换: /model <名称>")
        lines.append(f"添加: /addmodel")
        await update.message.reply_text("\n".join(lines))

    except Exception as e:
        await update.message.reply_text(f"❌ 出错了: {e}")


async def cmd_addmodel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or not update.message:
        return
    if not _check_user(update.effective_user.id):
        return

    args = context.args
    if not args or len(args) < 4:
        await update.message.reply_text(
            "📝 添加模型格式：\n\n"
            "/addmodel <名称> <模型ID> <base_url> <api_key> [描述]\n\n"
            "示例：\n"
            "/addmodel gpt4 gpt-4o https://api.openai.com/v1 sk-xxx 我的GPT4"
        )
        return

    name, model_id, base_url, api_key = args[0], args[1], args[2], args[3]
    description = " ".join(args[4:]) if len(args) > 4 else ""

    try:
        resp = await http_client.post(
            f"{API_URL}/api/v1/config/models/profiles",
            json={"name": name, "model": model_id, "base_url": base_url,
                   "api_key": api_key, "description": description},
        )
        if resp.status_code == 201:
            await update.message.reply_text(
                f"✅ 已添加: {name}\n   {model_id}\n\n切换: /model {name}"
            )
        elif resp.status_code == 409:
            await update.message.reply_text(f"⚠️ 名称已存在: {name}")
        else:
            await update.message.reply_text(f"❌ 失败: {resp.text}")
    except Exception as e:
        await update.message.reply_text(f"❌ 出错了: {e}")


async def cmd_delmodel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or not update.message:
        return
    if not _check_user(update.effective_user.id):
        return

    args = context.args
    if not args:
        await update.message.reply_text("用法: /delmodel <模型名称>")
        return

    try:
        resp = await http_client.delete(f"{API_URL}/api/v1/config/models/profiles/{args[0]}")
        if resp.status_code == 200:
            await update.message.reply_text(f"🗑 已删除: {args[0]}")
        else:
            await update.message.reply_text(f"❌ 失败: {resp.json().get('error', resp.text)}")
    except Exception as e:
        await update.message.reply_text(f"❌ 出错了: {e}")


# ── 消息处理 ──

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or not update.message:
        return
    if not _check_user(update.effective_user.id):
        return

    user_id = update.effective_user.id
    text = update.message.text or update.message.caption or ""
    if not text.strip():
        return

    await update.message.chat.send_action(ChatAction.TYPING)
    try:
        await update.message.set_reaction("👀")
    except Exception:
        pass

    session_id = user_sessions.get(user_id)

    try:
        chat_result = await _stream_chat(session_id, text)
        user_sessions[user_id] = chat_result.session_id
        await _handle_chat_result(update, context, chat_result, user_id)

        try:
            await update.message.set_reaction("⚡")
        except Exception:
            pass

    except httpx.HTTPStatusError as e:
        try:
            await update.message.set_reaction("❌")
        except Exception:
            pass
        await update.message.reply_text(f"❌ API 错误: {e.response.status_code}")
    except Exception as e:
        logger.exception("Chat error")
        try:
            await update.message.set_reaction("❌")
        except Exception:
            pass
        await update.message.reply_text(f"❌ 出错了: {type(e).__name__}: {e}")


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or not update.message or not update.message.document:
        return
    if not _check_user(update.effective_user.id):
        return

    doc = update.message.document
    filename = doc.file_name or "uploaded_file"
    ext = Path(filename).suffix.lower()

    if ext not in (".xlsx", ".xls", ".csv", ".png", ".jpg", ".jpeg"):
        await update.message.reply_text("⚠️ 仅支持 Excel (.xlsx/.xls/.csv) 和图片文件")
        return

    await update.message.chat.send_action(ChatAction.UPLOAD_DOCUMENT)

    tg_file = await doc.get_file()
    workspace = os.environ.get(
        "EXCELMANUS_WORKSPACE", "/root/.openclaw/workspace/excelmanus/workspace"
    )
    os.makedirs(workspace, exist_ok=True)
    await tg_file.download_to_drive(os.path.join(workspace, filename))

    try:
        await update.message.set_reaction("📎")
    except Exception:
        pass

    caption = update.message.caption or f"帮我分析 {filename}"
    user_id = update.effective_user.id
    session_id = user_sessions.get(user_id)
    msg = f"@file:{filename} {caption}"

    await update.message.chat.send_action(ChatAction.TYPING)

    try:
        chat_result = await _stream_chat(session_id, msg)
        user_sessions[user_id] = chat_result.session_id
        await _handle_chat_result(update, context, chat_result, user_id)
    except Exception as e:
        logger.exception("Document chat error")
        await update.message.reply_text(f"❌ 出错了: {e}")


# ── 启动 ──

async def post_init(app: Application) -> None:
    await app.bot.set_my_commands([
        BotCommand("start", "开始使用"),
        BotCommand("help", "查看所有命令"),
        BotCommand("new", "新建对话"),
        BotCommand("model", "查看/切换模型"),
        BotCommand("addmodel", "添加模型"),
        BotCommand("delmodel", "删除模型"),
    ])


def main() -> None:
    if not TG_TOKEN:
        print("❌ 请设置 EXCELMANUS_TG_TOKEN 环境变量")
        return

    app = Application.builder().token(TG_TOKEN).post_init(post_init).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("new", cmd_new))
    app.add_handler(CommandHandler("model", cmd_model))
    app.add_handler(CommandHandler("addmodel", cmd_addmodel))
    app.add_handler(CommandHandler("delmodel", cmd_delmodel))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("ExcelManus Telegram Bot 启动中...")
    logger.info("API: %s", API_URL)
    if ALLOWED_USERS:
        logger.info("允许的用户: %s", ALLOWED_USERS)
    else:
        logger.info("⚠️ 未设置用户限制，所有人可用")

    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
