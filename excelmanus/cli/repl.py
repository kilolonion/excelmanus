"""CLI REPL 循环 — 主交互循环、回合执行、状态提示。"""

from __future__ import annotations

import asyncio
import logging
import time
from contextlib import suppress
from typing import TYPE_CHECKING, Callable

from rich.console import Console
from rich.markdown import Markdown
from rich.padding import Padding
from rich.text import Text
from rich.cells import cell_len

from excelmanus.cli.theme import THEME
from excelmanus.cli.utils import separator_line
from excelmanus.events import EventType, ToolCallEvent
from excelmanus.renderer import StreamRenderer

if TYPE_CHECKING:
    from excelmanus.approval import PendingApproval
    from excelmanus.engine import AgentEngine
    from excelmanus.question_flow import PendingQuestion
    from excelmanus.types import ChatResult

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------
# 辅助函数
# ------------------------------------------------------------------


def _reply_text(result: "ChatResult | str") -> str:
    """兼容 chat() 新旧返回类型，统一提取展示文本。"""
    if hasattr(result, "reply"):
        return result.reply
    return str(result)


# ------------------------------------------------------------------
# 动态状态提示 (spinner)
# ------------------------------------------------------------------


class LiveStatusTicker:
    """CLI 动态状态提示：在等待回复期间输出 spinner 动画。"""

    _FRAMES = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")

    def __init__(self, console: Console, *, enabled: bool, interval: float = 0.12) -> None:
        self._console = console
        self._enabled = enabled
        self._interval = interval
        self._status_label = "思考中"
        self._frame_index = 0
        self._task: asyncio.Task[None] | None = None
        self._last_line_width = 0

    async def start(self) -> None:
        if not self._enabled or self._task is not None:
            return
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        task = self._task
        self._task = None
        if task is not None:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        if self._enabled:
            self._clear_line()

    def wrap_handler(
        self,
        handler: Callable[[ToolCallEvent], None],
    ) -> Callable[[ToolCallEvent], None]:
        if not self._enabled:
            return handler

        def _wrapped(event: ToolCallEvent) -> None:
            self._clear_line()
            self._update_state_from_event(event)
            handler(event)

        return _wrapped

    async def _run(self) -> None:
        while True:
            suffix = self._FRAMES[self._frame_index % len(self._FRAMES)]
            self._frame_index += 1
            line = f"  {THEME.AGENT_PREFIX} {self._status_label}{suffix}"
            line_width = cell_len(line)
            self._last_line_width = max(self._last_line_width, line_width)
            padding = " " * max(self._last_line_width - line_width, 0)
            self._console.print(Text(f"{line}{padding}", style=f"{THEME.DIM}"), end="\r")
            await asyncio.sleep(self._interval)

    def _update_state_from_event(self, event: ToolCallEvent) -> None:
        if event.event_type == EventType.TOOL_CALL_START:
            tool_name = event.tool_name.strip()
            self._status_label = f"调用工具 {tool_name}" if tool_name else "调用工具"
            return
        if event.event_type == EventType.SUBAGENT_START:
            name = (event.subagent_name or "").strip()
            self._status_label = f"子代理 {name}" if name else "调用子代理"
            return
        if event.event_type == EventType.SUBAGENT_ITERATION:
            name = (event.subagent_name or "").strip() or "subagent"
            turn = event.subagent_iterations or event.iteration or 0
            self._status_label = f"子代理 {name} 第 {turn} 轮"
            return
        if event.event_type == EventType.SUBAGENT_SUMMARY:
            self._status_label = "汇总子代理结果"
            return
        if event.event_type == EventType.SUBAGENT_END:
            self._status_label = "子代理收尾中"
            return
        if event.event_type == EventType.CHAT_SUMMARY:
            self._status_label = "整理结果"
            return
        if event.event_type in (EventType.TEXT_DELTA, EventType.THINKING_DELTA):
            self._status_label = ""
            return
        if event.event_type == EventType.PIPELINE_PROGRESS:
            msg = (event.pipeline_message or event.pipeline_stage or "").strip()
            self._status_label = msg if msg else "流水线处理中"
            return
        if event.event_type == EventType.FILES_CHANGED:
            self._status_label = "文件写入中"
            return
        if event.event_type == EventType.MEMORY_EXTRACTED:
            self._status_label = "记忆提取中"
            return
        if event.event_type == EventType.EXCEL_PREVIEW:
            self._status_label = "预览生成中"
            return
        if event.event_type == EventType.EXCEL_DIFF:
            self._status_label = "变更对比中"
            return
        if event.event_type == EventType.FILE_DOWNLOAD:
            self._status_label = "文件生成中"
            return
        self._status_label = "思考中"

    def _clear_line(self) -> None:
        if self._last_line_width <= 0:
            return
        self._console.print(" " * self._last_line_width, end="\r")


# ------------------------------------------------------------------
# 回合执行
# ------------------------------------------------------------------


async def chat_with_feedback(
    console: Console,
    engine: "AgentEngine",
    *,
    user_input: str,
    renderer: StreamRenderer,
    slash_command: str | None = None,
    raw_args: str | None = None,
    mention_contexts: list | None = None,
    approval_resolver: "Callable[[PendingApproval], Any] | None" = None,
    images: list[dict] | None = None,
) -> tuple[str, bool]:
    """统一封装 chat 调用，增加等待期动态状态反馈。返回 (reply_text, streamed)。"""
    from excelmanus.cli.prompt import is_interactive_terminal

    ticker = LiveStatusTicker(console, enabled=is_interactive_terminal())
    event_handler = ticker.wrap_handler(renderer.handle_event)

    await ticker.start()
    try:
        chat_kwargs: dict[str, object] = {"on_event": event_handler}
        if slash_command is not None:
            chat_kwargs["slash_command"] = slash_command
        if raw_args is not None:
            chat_kwargs["raw_args"] = raw_args
        if mention_contexts is not None:
            chat_kwargs["mention_contexts"] = mention_contexts
        if approval_resolver is not None:
            chat_kwargs["approval_resolver"] = approval_resolver
        if images:
            chat_kwargs["images"] = images
        reply = _reply_text(await engine.chat(user_input, **chat_kwargs))
        streamed = renderer._streaming_text or renderer._streaming_thinking
        renderer.finish_streaming()
        return reply, streamed
    finally:
        await ticker.stop()


async def run_chat_turn(
    console: Console,
    engine: "AgentEngine",
    *,
    user_input: str,
    slash_command: str | None = None,
    raw_args: str | None = None,
    mention_contexts: list | None = None,
    error_label: str = "处理请求",
    approval_resolver: "Callable[[PendingApproval], Any] | None" = None,
    images: list[dict] | None = None,
) -> tuple[str, bool] | None:
    """统一回合执行入口：使用 StreamRenderer 渲染，调用引擎。"""
    renderer = StreamRenderer(console)
    try:
        console.print()
        reply, streamed = await chat_with_feedback(
            console,
            engine,
            user_input=user_input,
            renderer=renderer,
            slash_command=slash_command,
            raw_args=raw_args,
            mention_contexts=mention_contexts,
            images=images,
            approval_resolver=approval_resolver,
        )

        _has_pending_q = bool(getattr(engine, "has_pending_question", lambda: False)())
        _has_pending_a = bool(getattr(engine, "has_pending_approval", lambda: False)())
        _skip_reply = _has_pending_q or _has_pending_a

        if not streamed and not _skip_reply:
            console.print()
            # Claude Code 风格：回复文本用左缩进 Markdown 块，无 ● 前缀
            console.print(Padding(Markdown(reply), (0, 2, 0, 2)))

        return reply, streamed
    except KeyboardInterrupt:
        raise
    except Exception as exc:
        logger.error("%s时发生错误: %s", error_label, exc, exc_info=True)
        from excelmanus.cli_errors import render_error_panel
        render_error_panel(console, error=exc, error_label=error_label)
        return None


# ------------------------------------------------------------------
# 交互式模型选择器
# ------------------------------------------------------------------


async def interactive_model_select(engine: "AgentEngine") -> str | None:
    """交互式模型选择器（箭头键导航）。"""
    from excelmanus.cli.prompt import is_interactive_terminal

    if not is_interactive_terminal():
        return None

    try:
        from prompt_toolkit import Application
        from prompt_toolkit.formatted_text import FormattedText
        from prompt_toolkit.key_binding import KeyBindings
        from prompt_toolkit.layout import HSplit, Layout, Window
        from prompt_toolkit.layout.controls import FormattedTextControl
        from prompt_toolkit.styles import Style
    except ImportError:
        return None

    rows = engine.list_models()
    if not rows:
        return None

    initial_cursor = 0
    for i, row in enumerate(rows):
        if row.get("active"):
            initial_cursor = i
            break

    cursor = [initial_cursor]
    result_holder: list[str | None] = []

    kb = KeyBindings()

    @kb.add("up")
    def _move_up(event) -> None:  # type: ignore[no-untyped-def]
        cursor[0] = (cursor[0] - 1) % len(rows)

    @kb.add("down")
    def _move_down(event) -> None:  # type: ignore[no-untyped-def]
        cursor[0] = (cursor[0] + 1) % len(rows)

    @kb.add("enter")
    def _confirm(event) -> None:  # type: ignore[no-untyped-def]
        result_holder.append(rows[cursor[0]]["name"])
        event.app.exit()

    @kb.add("escape")
    def _escape(event) -> None:  # type: ignore[no-untyped-def]
        result_holder.append(None)
        event.app.exit()

    def _get_formatted_text() -> FormattedText:
        fragments: list[tuple[str, str]] = []
        fragments.append(("class:header", f"  {THEME.AGENT_PREFIX} 选择模型\n"))
        fragments.append(("class:separator", f"  {'─' * 50}\n"))

        for i, row in enumerate(rows):
            is_cursor = i == cursor[0]
            is_active = bool(row.get("active"))

            prefix = f"  {THEME.CURSOR} " if is_cursor else "    "
            name = row["name"]
            model = row["model"]
            desc = f"  {row['description']}" if row.get("description") else ""
            marker = f" {THEME.SUCCESS}" if is_active else ""
            line = f"{prefix}{name} → {model}{desc}{marker}\n"

            if is_cursor:
                style = "class:selected"
            elif is_active:
                style = "class:active"
            else:
                style = "class:option"
            fragments.append((style, line))

        fragments.append(("", "\n"))
        fragments.append(("class:hint", "  ↑↓ 移动 · Enter 确认 · Esc 取消\n"))
        return FormattedText(fragments)

    control = FormattedTextControl(_get_formatted_text)
    window = Window(content=control, always_hide_cursor=True)
    layout = Layout(HSplit([window]))

    style = Style.from_dict(
        {
            "header": f"bold {THEME.PRIMARY_LIGHT}",
            "separator": "dim",
            "selected": f"bold {THEME.PRIMARY_LIGHT}",
            "active": f"bold {THEME.GOLD}",
            "option": "",
            "hint": f"italic {THEME.DIM}",
        }
    )

    app: Application[None] = Application(
        layout=layout,
        key_bindings=kb,
        style=style,
        full_screen=False,
    )

    await app.run_async()

    if not result_holder:
        return None
    return result_holder[0]


# ------------------------------------------------------------------
# REPL 主循环
# ------------------------------------------------------------------


def _build_approval_resolver(
    console: Console,
) -> "Callable[[PendingApproval], Any]":
    """构造内联审批解析器：包装 interactive_approval_select，返回 engine 约定字符串。"""
    from excelmanus.cli.approval import (
        APPROVAL_ACCEPT,
        APPROVAL_FULLACCESS,
        APPROVAL_REJECT,
        interactive_approval_select,
    )

    _CHOICE_MAP = {
        APPROVAL_ACCEPT: "accept",
        APPROVAL_FULLACCESS: "fullaccess",
        APPROVAL_REJECT: "reject",
    }

    async def _resolver(pending: "PendingApproval") -> str | None:
        try:
            choice = await interactive_approval_select(pending)
        except (KeyboardInterrupt, EOFError):
            return "reject"
        except Exception as exc:
            logger.warning("内联审批选择器异常，视为 reject：%s", exc)
            return "reject"
        if choice is None:
            return "reject"
        return _CHOICE_MAP.get(choice, "reject")

    return _resolver


async def repl_loop(console: Console, engine: "AgentEngine") -> None:
    """异步 REPL 主循环。"""
    from excelmanus.cli.approval import (
        APPROVAL_ACCEPT,
        APPROVAL_FULLACCESS,
        APPROVAL_REJECT,
        interactive_approval_select,
    )
    from excelmanus.cli.commands import (
        EXIT_COMMANDS,
        SESSION_CONTROL_ALIASES,
        SHORTCUT_ACTION_SHOW_HELP,
        SUBAGENT_ALIASES,
        extract_slash_raw_args,
        handle_config_command,
        handle_skills_subcommand,
        render_farewell,
        render_history,
        render_mcp,
        render_skills,
        resolve_shortcut_action,
        resolve_skill_slash_command,
        suggest_similar_commands,
    )
    from excelmanus.cli.help import render_help
    from excelmanus.cli.prompt import (
        read_multiline_user_input,
        read_user_input,
    )
    from excelmanus.cli.question import (
        build_answer_from_select,
        interactive_question_select,
    )

    import excelmanus.cli.prompt as prompt_mod

    # 初始化 @ 提及补全器
    try:
        from excelmanus.mentions.completer import MentionCompleter

        _mention = MentionCompleter(
            workspace_root=engine._config.workspace_root,
            engine=engine,
        )
        prompt_mod._MENTION_COMPLETER = _mention
        # 同步到合并补全器（使 / 和 @ 都通过统一入口）
        if hasattr(prompt_mod, "_MERGED_COMPLETER") and prompt_mod._MERGED_COMPLETER is not None:
            prompt_mod._MERGED_COMPLETER.mention_completer = _mention
    except Exception as exc:
        logger.warning("@ 提及补全器初始化失败：%s", exc)

    # 同步斜杠命令与参数建议（含模型名）
    _sync_slash_commands(engine)

    # 构造内联审批解析器，使审批在同一轮对话内完成
    _approval_resolver = _build_approval_resolver(console)

    while True:
        has_pending_question = bool(
            getattr(engine, "has_pending_question", lambda: False)()
        )
        waiting_multiselect = bool(
            getattr(engine, "is_waiting_multiselect_answer", lambda: False)()
        )

        # ── 问题交互式选择器 ──
        if has_pending_question:
            current_q_getter = getattr(engine, "current_pending_question", None)
            current_q = current_q_getter() if callable(current_q_getter) else None
            if current_q and current_q.options:
                try:
                    select_result = await interactive_question_select(current_q)
                except (KeyboardInterrupt, EOFError):
                    render_farewell(console)
                    return
                except Exception as exc:
                    logger.warning("交互式选择器异常，回退到普通输入：%s", exc)
                    select_result = None

                if select_result is not None and not select_result.escaped:
                    user_input = build_answer_from_select(current_q, select_result)
                    if user_input:
                        try:
                            await run_chat_turn(
                                console, engine,
                                user_input=user_input,
                                error_label="处理待回答问题",
                            )
                        except KeyboardInterrupt:
                            render_farewell(console)
                            return
                        continue

        # ── 审批交互式选择器 ──
        has_pending_approval = bool(
            getattr(engine, "has_pending_approval", lambda: False)()
        )
        if has_pending_approval and not has_pending_question:
            pending_getter = getattr(engine, "current_pending_approval", None)
            pending_apv = pending_getter() if callable(pending_getter) else None
            if pending_apv is not None:
                try:
                    approval_choice = await interactive_approval_select(pending_apv)
                except (KeyboardInterrupt, EOFError):
                    render_farewell(console)
                    return
                except Exception as exc:
                    logger.warning("审批交互式选择器异常：%s", exc)
                    approval_choice = None

                if approval_choice is not None:
                    if approval_choice == APPROVAL_ACCEPT:
                        user_input = f"/accept {pending_apv.approval_id}"
                    elif approval_choice == APPROVAL_REJECT:
                        user_input = f"/reject {pending_apv.approval_id}"
                    elif approval_choice == APPROVAL_FULLACCESS:
                        user_input = "/fullaccess on"
                    else:
                        user_input = f"/reject {pending_apv.approval_id}"

                    try:
                        await run_chat_turn(
                            console, engine,
                            user_input=user_input,
                            error_label="处理审批操作",
                        )
                        if approval_choice == APPROVAL_FULLACCESS:
                            await run_chat_turn(
                                console, engine,
                                user_input=f"/accept {pending_apv.approval_id}",
                                error_label="处理审批操作",
                            )
                    except KeyboardInterrupt:
                        render_farewell(console)
                        return
                    continue

        # ── 读取用户输入 ──
        try:
            if waiting_multiselect:
                console.print(
                    f"  [{THEME.DIM}]多选回答模式：每行输入一个选项，空行提交。[/{THEME.DIM}]"
                )
                user_input = (await read_multiline_user_input()).strip()
            else:
                _model_hint = getattr(engine, "current_model_name", None) or ""
                _turn = getattr(engine, "turn_count", 0)
                if callable(_turn):
                    _turn = _turn()
                _turn = _turn if isinstance(_turn, int) else 0
                _full_access = getattr(engine, "full_access_enabled", False)
                _plan_mode = getattr(engine, "plan_mode_enabled", False)
                user_input = (await read_user_input(
                    model_hint=_model_hint if isinstance(_model_hint, str) else "",
                    turn_number=_turn if isinstance(_turn, int) else 0,
                    full_access=bool(_full_access),
                    plan_mode=bool(_plan_mode),
                )).strip()
        except (KeyboardInterrupt, EOFError):
            render_farewell(console)
            return

        if not user_input:
            continue

        if user_input.lower() in EXIT_COMMANDS:
            render_farewell(console)
            return

        # ── 待回答问题 ──
        if has_pending_question:
            try:
                await run_chat_turn(
                    console, engine,
                    user_input=user_input,
                    error_label="处理待回答问题",
                )
            except KeyboardInterrupt:
                render_farewell(console)
                return
            continue

        shortcut_action = resolve_shortcut_action(user_input)
        if shortcut_action == SHORTCUT_ACTION_SHOW_HELP:
            render_help(console, engine)
            continue

        # ── 斜杠命令 ──
        if user_input.lower() == "/help":
            render_help(console, engine)
            continue

        if user_input.lower() == "/history":
            render_history(console, engine)
            continue

        if user_input.lower() == "/clear":
            engine.clear_memory()
            console.print(f"  [{THEME.PRIMARY_LIGHT}]{THEME.SUCCESS} 对话历史已清除。[/{THEME.PRIMARY_LIGHT}]")
            continue

        if user_input.lower().startswith("/save"):
            try:
                _handle_save_command(console, engine, user_input)
            except Exception as exc:
                logger.error("处理 /save 命令失败: %s", exc, exc_info=True)
                console.print(f"  [{THEME.RED}]{THEME.FAILURE} /save 命令执行失败：{exc}[/{THEME.RED}]")
            continue

        if user_input.lower() == "/skills":
            render_skills(console, engine)
            continue

        if user_input.lower() == "/mcp":
            render_mcp(console, engine)
            continue

        if user_input.lower().startswith("/config"):
            handle_config_command(console, user_input, engine._config.workspace_root)
            continue

        if user_input.lower().startswith("/skills "):
            try:
                handled = handle_skills_subcommand(
                    console, engine, user_input,
                    sync_callback=lambda: _sync_slash_commands(engine),
                )
            except Exception as exc:
                logger.error("处理 /skills 子命令失败: %s", exc, exc_info=True)
                console.print(f"  [{THEME.RED}]{THEME.FAILURE} /skills 子命令执行失败：{exc}[/{THEME.RED}]")
                handled = True
            if handled:
                continue

        # /model 交互式选择器
        lowered_parts = user_input.lower().split()
        lowered_cmd = lowered_parts[0] if lowered_parts else ""
        if lowered_cmd == "/model" and (
            len(lowered_parts) == 1 or (len(lowered_parts) == 2 and lowered_parts[1] == "list")
        ):
            try:
                selected_name = await interactive_model_select(engine)
            except (KeyboardInterrupt, EOFError):
                render_farewell(console)
                return
            except Exception as exc:
                logger.warning("交互式模型选择器异常：%s", exc)
                selected_name = None

            if selected_name is not None:
                result_msg = engine.switch_model(selected_name)
                console.print(f"  [{THEME.CYAN}]{result_msg}[/{THEME.CYAN}]")
                _sync_slash_commands(engine)
            else:
                console.print(f"  [{THEME.DIM}]已取消选择。[/{THEME.DIM}]")
            continue

        # 会话控制命令
        if lowered_cmd in SESSION_CONTROL_ALIASES:
            if lowered_cmd in SUBAGENT_ALIASES:
                try:
                    await run_chat_turn(
                        console, engine,
                        user_input=user_input,
                        error_label="处理子代理命令",
                        approval_resolver=_approval_resolver,
                    )
                except KeyboardInterrupt:
                    render_farewell(console)
                    return
            else:
                reply = _reply_text(await engine.chat(user_input))
                console.print(f"  [{THEME.CYAN}]{reply}[/{THEME.CYAN}]")
            continue

        # Skill 斜杠命令
        resolved_skill = (
            resolve_skill_slash_command(engine, user_input)
            if user_input.startswith("/")
            else None
        )
        if resolved_skill:
            raw_args = extract_slash_raw_args(user_input)
            argument_hint_getter = getattr(engine, "get_skillpack_argument_hint", None)
            argument_hint = (
                argument_hint_getter(resolved_skill)
                if callable(argument_hint_getter)
                else ""
            )
            if not raw_args and isinstance(argument_hint, str) and argument_hint.strip():
                console.print(f"  [{THEME.GOLD}]参数提示：{argument_hint.strip()}[/{THEME.GOLD}]")
            try:
                await run_chat_turn(
                    console, engine,
                    user_input=user_input,
                    slash_command=resolved_skill,
                    raw_args=raw_args,
                    error_label="处理技能命令",
                    approval_resolver=_approval_resolver,
                )
            except KeyboardInterrupt:
                render_farewell(console)
                return
            continue

        # 未知斜杠命令
        if user_input.startswith("/"):
            similar = suggest_similar_commands(user_input)
            if similar:
                suggestion = ", ".join(similar)
                console.print(
                    f"  [{THEME.GOLD}]未知命令：{user_input}。你是否想输入：{suggestion}[/{THEME.GOLD}]"
                )
            else:
                console.print(
                    f"  [{THEME.GOLD}]未知命令：{user_input}。使用 /help 查看可用命令。[/{THEME.GOLD}]"
                )
            continue

        # ── 自然语言指令 ──
        mention_contexts = None
        try:
            from excelmanus.mentions import MentionParser, MentionResolver
            from excelmanus.security.guard import FileAccessGuard

            parse_result = MentionParser.parse(user_input)
            if parse_result.mentions:
                guard = FileAccessGuard(engine._config.workspace_root)
                skill_loader = getattr(engine, "_skill_loader", None)
                if skill_loader is None:
                    _router = getattr(engine, "_skill_router", None)
                    if _router is not None:
                        skill_loader = getattr(_router, "_loader", None)
                mcp_manager = getattr(engine, "_mcp_manager", None)
                resolver = MentionResolver(
                    workspace_root=engine._config.workspace_root,
                    guard=guard,
                    skill_loader=skill_loader,
                    mcp_manager=mcp_manager,
                )
                mention_contexts = await resolver.resolve(list(parse_result.mentions))
        except Exception as exc:
            logger.debug("@ 提及解析失败，跳过上下文注入：%s", exc)

        # ── @img 图片附件解析 ──
        cli_images: list[dict] | None = None
        try:
            from excelmanus.cli.commands import parse_image_attachments
            clean_text, img_paths = parse_image_attachments(user_input)
            if img_paths:
                import base64 as _b64
                import mimetypes as _mt
                from pathlib import Path as _P
                ws = _P(engine._config.workspace_root)
                loaded: list[dict] = []
                for p in img_paths:
                    fp = _P(p) if _P(p).is_absolute() else ws / p
                    if not fp.is_file():
                        console.print(f"  [{THEME.GOLD}]⚠ 图片文件不存在: {p}[/{THEME.GOLD}]")
                        continue
                    mime = _mt.guess_type(fp.name)[0] or "image/png"
                    data = _b64.b64encode(fp.read_bytes()).decode()
                    loaded.append({"data": data, "media_type": mime})
                if loaded:
                    cli_images = loaded
                    user_input = clean_text
        except Exception as exc:
            logger.debug("@img 解析失败：%s", exc)

        try:
            await run_chat_turn(
                console, engine,
                user_input=user_input,
                mention_contexts=mention_contexts,
                images=cli_images,
                error_label="处理请求",
                approval_resolver=_approval_resolver,
            )
        except KeyboardInterrupt:
            render_farewell(console)
            return


# ------------------------------------------------------------------
# 内部辅助
# ------------------------------------------------------------------


def _sync_slash_commands(engine: "AgentEngine") -> None:
    """同步斜杠命令建议到 prompt 模块。"""
    from excelmanus.cli.commands import (
        build_prompt_command_sync_payload,
    )
    from excelmanus.cli.prompt import apply_prompt_command_sync

    payload = build_prompt_command_sync_payload(engine)
    apply_prompt_command_sync(payload)


def _handle_save_command(
    console: Console,
    engine: "AgentEngine",
    user_input: str,
) -> None:
    """处理 /save 命令。"""
    parts = user_input.strip().split(None, 1)
    path = parts[1].strip() if len(parts) > 1 else None

    save_fn = getattr(engine, "save_conversation", None)
    if not callable(save_fn):
        console.print(f"  [{THEME.DIM}]保存功能不可用。[/{THEME.DIM}]")
        return

    saved_path = save_fn(path)
    if saved_path:
        console.print(
            f"  [{THEME.PRIMARY_LIGHT}]{THEME.SUCCESS}[/{THEME.PRIMARY_LIGHT}]"
            f" 对话已保存至 [{THEME.CYAN}]{saved_path}[/{THEME.CYAN}]"
        )
    else:
        console.print(f"  [{THEME.RED}]{THEME.FAILURE} 保存失败[/{THEME.RED}]")
