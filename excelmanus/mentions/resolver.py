"""上下文解析器：将 Mention 列表解析为实际内容。

对每种 Mention 类型（file/folder/skill/mcp/img）执行内容提取，
生成注入系统提示词的 context_block。所有 file/folder 引用通过
FileAccessGuard 进行安全校验。
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from excelmanus.excel_extensions import EXCEL_EXTENSIONS
from excelmanus.mentions.parser import Mention, ResolvedMention
from excelmanus.security.guard import FileAccessGuard, SecurityViolationError

if TYPE_CHECKING:
    from excelmanus.mcp.manager import MCPManager
    from excelmanus.skillpacks.loader import SkillpackLoader

# Excel 文件扩展名（向后兼容保留私有别名）
_EXCEL_EXTENSIONS = EXCEL_EXTENSIONS

# 目录树排除项
_EXCLUDED_NAMES = {".venv", "node_modules", "__pycache__"}


def _count_tokens(text: str) -> int:
    """使用 tiktoken 计算 token 数，失败时降级为字符估算。"""
    try:
        import tiktoken

        enc = tiktoken.get_encoding("o200k_base")
        return len(enc.encode(text))
    except Exception:
        # 降级：1 token ≈ 4 字符
        return len(text) // 4


def _truncate_to_tokens(text: str, max_tokens: int) -> str:
    """将文本截断到 max_tokens 以内，按行截断。"""
    try:
        import tiktoken

        enc = tiktoken.get_encoding("o200k_base")
        tokens = enc.encode(text)
        if len(tokens) <= max_tokens:
            return text
        truncated = enc.decode(tokens[:max_tokens])
        return truncated
    except Exception:
        # 降级：1 token ≈ 4 字符
        max_chars = max_tokens * 4
        if len(text) <= max_chars:
            return text
        return text[:max_chars]


class MentionResolver:
    """将 Mention 列表解析为实际内容。"""

    def __init__(
        self,
        workspace_root: str,
        guard: FileAccessGuard,
        skill_loader: SkillpackLoader | None = None,
        mcp_manager: MCPManager | None = None,
        max_file_tokens: int = 2000,
        max_folder_depth: int = 2,
    ) -> None:
        self._workspace_root = workspace_root
        self._guard = guard
        self._skill_loader = skill_loader
        self._mcp_manager = mcp_manager
        self._max_file_tokens = max_file_tokens
        self._max_folder_depth = max_folder_depth

    async def resolve(self, mentions: list[Mention]) -> list[ResolvedMention]:
        """解析所有 Mention，返回 ResolvedMention 列表。"""
        results: list[ResolvedMention] = []
        for mention in mentions:
            if mention.kind == "file":
                results.append(self._resolve_file(mention))
            elif mention.kind == "folder":
                results.append(self._resolve_folder(mention))
            elif mention.kind == "skill":
                results.append(self._resolve_skill(mention))
            elif mention.kind == "mcp":
                results.append(await self._resolve_mcp(mention))
            elif mention.kind == "img":
                # img 类型保持现有行为，不生成 context_block
                results.append(ResolvedMention(mention=mention))
            else:
                results.append(
                    ResolvedMention(
                        mention=mention,
                        error=f"未知的引用类型：{mention.kind}",
                    )
                )
        return results

    def _remember_file_version(self, mention: Mention, path: Path) -> None:
        from excelmanus.workbook_commit import content_version_of_file, remember_content_version

        version = mention.content_version or content_version_of_file(path)
        if version:
            remember_content_version(mention.value, version)
            try:
                rel = str(path.resolve().relative_to(Path(self._workspace_root).resolve()))
                remember_content_version(rel.replace("\\", "/"), version)
            except ValueError:
                pass

    # ── file 解析 ─────────────────────────────────────────

    def _resolve_file(self, mention: Mention) -> ResolvedMention:
        """解析文件引用：安全校验 + 内容提取 + token 预算限制。"""
        # 安全校验
        try:
            resolved_path = self._guard.resolve_and_validate(mention.value)
        except SecurityViolationError as exc:
            return ResolvedMention(mention=mention, error=str(exc))

        if not resolved_path.exists():
            return ResolvedMention(
                mention=mention, error=f"文件不存在：{mention.value}"
            )

        if not resolved_path.is_file():
            return ResolvedMention(
                mention=mention, error=f"路径不是文件：{mention.value}"
            )

        suffix = resolved_path.suffix.lower()
        snap = None
        if suffix in _EXCEL_EXTENSIONS:
            from excelmanus.workbook.snapshot import SnapshotError, SnapshotStale, open_snapshot_at
            from excelmanus.workspace.refs import WorkspaceRef

            try:
                try:
                    rel = resolved_path.relative_to(Path(self._workspace_root)).as_posix()
                except ValueError:
                    rel = str(mention.value).replace("\\", "/")
                snap = open_snapshot_at(
                    resolved_path,
                    relative=rel,
                    workspace=WorkspaceRef.from_root(self._workspace_root),
                    expected_version=mention.content_version,
                )
            except SnapshotStale as exc:
                return ResolvedMention(
                    mention=mention,
                    error=str(exc),
                    # 注册表里的正名是 STALE_READ（失败类别 conflict）。
                    error_code="STALE_READ",
                    error_fields=exc.fields,
                )
            except SnapshotError as exc:
                return ResolvedMention(
                    mention=mention,
                    error=str(exc),
                    error_code=exc.code,
                    error_fields=exc.fields,
                )
            resolved_path = snap.backing_path
        if suffix in _EXCEL_EXTENSIONS:
            if mention.range_spec:
                resolved = self._resolve_excel_range(mention, resolved_path)
            else:
                resolved = self._resolve_excel_file(mention, resolved_path)
        else:
            resolved = self._resolve_text_file(mention, resolved_path)
        if not resolved.error and snap is not None:
            resolved.content_version = snap.content_version
            self._remember_file_version(mention, Path(mention.value))
        return resolved

    def _resolve_excel_file(
        self, mention: Mention, path: Path
    ) -> ResolvedMention:
        """解析 Excel 文件：丰富的结构化摘要（工作表列表、行列数、列结构、数据类型）。"""
        try:
            from openpyxl import load_workbook

            wb = load_workbook(str(path), read_only=True, data_only=True)
            try:
                lines: list[str] = []
                total_sheets = len(wb.sheetnames)
                lines.append(f"[File] {mention.value} ({total_sheets}个工作表)")

                for i, name in enumerate(wb.sheetnames):
                    ws = wb[name]
                    rows = ws.max_row or 0
                    cols = ws.max_column or 0
                    lines.append(f"  [{i + 1}] \"{name}\" ({rows}行×{cols}列)")

                    # 首个 sheet 提供列结构描述
                    if i == 0 and rows > 0:
                        header_row = next(ws.iter_rows(max_row=1, values_only=True), None)
                        if header_row:
                            from openpyxl.utils import get_column_letter
                            headers = [
                                str(c) if c is not None else "" for c in header_row
                            ]
                            # 列结构：字母+表头名
                            col_descs = []
                            for ci, h in enumerate(headers):
                                col_letter = get_column_letter(ci + 1)
                                if h:
                                    col_descs.append(f"{col_letter}(\"{h}\")")
                                else:
                                    col_descs.append(col_letter)
                            lines.append(f"      Columns: {' | '.join(col_descs)}")

                context = "\n".join(lines)
            finally:
                wb.close()

            # token 预算限制
            context = _truncate_to_tokens(context, self._max_file_tokens)
            return ResolvedMention(mention=mention, context_block=context)

        except Exception as exc:
            return ResolvedMention(
                mention=mention,
                error=f"文件读取失败：{mention.value}：{exc}",
            )

    @staticmethod
    def _parse_range_spec(range_spec: str) -> tuple[str | None, str]:
        """解析 range_spec 为 (sheet_name, cell_range)。

        支持格式：
        - "Sheet1!A1:C10" → ("Sheet1", "A1:C10")
        - "A1:C10" → (None, "A1:C10")
        - "Sheet1!A1" → ("Sheet1", "A1")
        - "A1" → (None, "A1")
        命名区域保持名称本身，禁止扩成 Name:Name。
        """
        from excelmanus.workbook.refs import CellRef, NamedRef, RectRef, TableRef, parse_ref

        area = parse_ref(range_spec)
        part = area.areas[0]
        if isinstance(part, NamedRef):
            return part.sheet, part.name
        if isinstance(part, TableRef):
            return part.sheet, part.to_a1(include_sheet=False)
        if isinstance(part, CellRef):
            return part.sheet, part.to_a1(include_sheet=False)
        if isinstance(part, RectRef):
            return part.sheet, part.to_a1(include_sheet=False)
        return None, range_spec

    @staticmethod
    def _infer_col_type(values: list[str]) -> str:
        """推断列数据类型（采样非空值）。"""
        non_empty = [v for v in values if v]
        if not non_empty:
            return "empty"
        nums = 0
        dates = 0
        for v in non_empty[:20]:  # 采样前 20 个
            try:
                float(v.replace(",", ""))
                nums += 1
                continue
            except ValueError:
                pass
            # 简单日期检测
            if any(sep in v for sep in ("-", "/")) and any(c.isdigit() for c in v):
                dates += 1
        total = len(non_empty[:20])
        if nums > total * 0.7:
            return "numeric"
        if dates > total * 0.7:
            return "date"
        return "text"

    def _resolve_excel_range(
        self, mention: Mention, path: Path
    ) -> ResolvedMention:
        """读取 Excel 文件指定 range 的单元格数据，生成浏览器检查器风格的富上下文描述。

        生成的上下文包含：
        - 选区定位：文件路径、工作表名、单元格范围
        - 工作表全局信息：总行列数、选区在表中的位置
        - 列结构描述：列字母、首行值（可能是表头）、推断数据类型
        - 数据内容：管道分隔表格
        - 空值统计
        """
        try:
            from openpyxl import load_workbook
            from excelmanus.workbook.data import WorkbookRefBindError, _bind_area_in_workbook
            from excelmanus.workbook.refs import parse_ref
            from excelmanus.workbook.snapshot import (
                RefUnsupported,
                SheetRequired,
                SnapshotError,
                require_default_sheet,
            )

            wb = load_workbook(str(path), read_only=False, data_only=True)
            try:
                range_spec = str(mention.range_spec or "")
                try:
                    area = parse_ref(range_spec)
                except Exception as exc:
                    return ResolvedMention(
                        mention=mention,
                        error=f"无法解析引用：{exc}",
                        error_code="RANGE_INVALID",
                    )
                default_sheet = None
                named_sheets = area.sheets()
                if len(named_sheets) == 1:
                    # Browser mentions use Sheet!A1:B2,D4:E5 for same-sheet unions.
                    default_sheet = named_sheets[0]
                try:
                    if default_sheet is None and any(not part.sheet for part in area.areas):
                        default_sheet = require_default_sheet(list(wb.sheetnames), None)
                    rects = _bind_area_in_workbook(wb, area, default_sheet=default_sheet)
                except SheetRequired as exc:
                    return ResolvedMention(mention=mention, error=str(exc), error_code="SHEET_REQUIRED")
                except (WorkbookRefBindError, SnapshotError, RefUnsupported) as exc:
                    code = getattr(exc, "code", "REF_UNSUPPORTED")
                    return ResolvedMention(
                        mention=mention,
                        error=str(exc),
                        error_code=str(code),
                        error_fields=dict(getattr(exc, "fields", None) or {}),
                    )
                if not rects:
                    return ResolvedMention(mention=mention, error="引用没有可绑定区域", error_code="RANGE_INVALID")
                # Share both read and text budgets across areas; never read their bounding box.
                previews = rects[:50]
                cell_budget = max(1, 2000 // len(previews))
                summary = ""
                if len(rects) > 1:
                    summary = f"[SelectionSet] {len(rects)} 个区域：{range_spec}\n"
                if len(rects) > len(previews):
                    summary += f"[Preview] 仅预览前 {len(previews)} 个区域，其余区域仍在引用中。\n"
                token_budget = max(1, (self._max_file_tokens - _count_tokens(summary)) // len(previews))
                blocks = []
                for rect in previews:
                    block = self._describe_excel_area(wb, rect, cell_budget)
                    if _count_tokens(block) > token_budget:
                        marker = "\n[Preview] 本区域内容因预算截断，引用范围保持不变。"
                        block = _truncate_to_tokens(block, max(1, token_budget - _count_tokens(marker))) + marker
                    blocks.append(block)
                context = summary + "\n\n".join(blocks)
            finally:
                wb.close()

            # token 预算限制
            context = _truncate_to_tokens(context, self._max_file_tokens)
            return ResolvedMention(mention=mention, context_block=context)

        except Exception as exc:
            return ResolvedMention(
                mention=mention,
                error=f"范围读取失败：{mention.value}[{mention.range_spec}]：{exc}",
                error_code="RANGE_INVALID" if isinstance(exc, ValueError) else "TOOL_ERROR",
            )

    def _describe_excel_area(self, wb, rect, cell_budget: int) -> str:
        """Describe one area with a bounded read, keeping gaps between selections untouched."""
        from openpyxl.utils import get_column_letter

        sheet_name = rect.sheet
        ws = wb[sheet_name]
        min_col, min_row, max_col, max_row = rect.min_col, rect.min_row, rect.max_col, rect.max_row
        cell_range = rect.to_a1(include_sheet=False)
        # Whole axes retain their reference, but preview only the worksheet's used extent.
        if rect.whole_column:
            max_row = max(min_row, min(max_row, ws.max_row or 1))
        if rect.whole_row:
            max_col = max(min_col, min(max_col, ws.max_column or 1))

        # ── 工作表全局信息 ──
        sheet_total_rows = ws.max_row or 0
        sheet_total_cols = ws.max_column or 0
        sheet_index = wb.sheetnames.index(sheet_name) if sheet_name in wb.sheetnames else 0
        total_sheets = len(wb.sheetnames)
        requested_range = cell_range
        requested_rows = max_row - min_row + 1
        requested_cols = max_col - min_col + 1
        # 先限制读取量，再做文本预算；避免巨大选区先整块载入内存。
        max_col = min(max_col, min_col + min(100, cell_budget) - 1)
        max_row = min(max_row, min_row + max(1, cell_budget // (max_col - min_col + 1)) - 1)

        # 构建列头（字母标识）
        col_headers = [
            get_column_letter(c) for c in range(min_col, max_col + 1)
        ]

        # 读取单元格数据
        data_rows: list[list[str]] = []
        for row in ws.iter_rows(
            min_row=min_row,
            max_row=max_row,
            min_col=min_col,
            max_col=max_col,
            values_only=True,
        ):
            data_rows.append(
                [str(v) if v is not None else "" for v in row]
            )

        # ── 选区元数据 ──
        range_label = f"{sheet_name}!{get_column_letter(min_col)}{min_row}:{get_column_letter(max_col)}{max_row}"
        num_rows = len(data_rows)
        num_cols = len(col_headers)

        # 首行值（可能是表头）
        first_row_values = data_rows[0] if data_rows else []
        # 判断首行是否为表头：全部非空且非纯数字
        has_header = bool(first_row_values) and all(
            v and not v.replace(".", "").replace(",", "").lstrip("-").isdigit()
            for v in first_row_values
        )

        # 列类型推断（跳过首行如果是表头）
        col_types: list[str] = []
        for ci in range(num_cols):
            col_values = [
                data_rows[ri][ci]
                for ri in range(1 if has_header else 0, num_rows)
                if ci < len(data_rows[ri])
            ]
            col_types.append(self._infer_col_type(col_values))

        # 空值统计
        empty_count = sum(
            1 for row in data_rows for v in row if not v
        )
        total_cells = num_rows * num_cols

        # ── 组装富上下文 ──
        lines: list[str] = []

        # 1) 选区定位摘要
        lines.append(f"[Selection] {sheet_name}!{requested_range} ({requested_cols}列×{requested_rows}行)")
        if rect.whole_column or rect.whole_row:
            lines.append("[Preview] 整行/整列按工作表已用范围预览，引用仍包含完整行/列。")
        if requested_rows != num_rows or requested_cols != num_cols:
            lines.append(f"[Preview] 已截断：仅展示 {range_label}，不是完整选区内容。")
        lines.append(f"[Requested] {requested_range}；数据为磁盘缓存值，公式可能尚未重算。")
        lines.append(
            f"[Sheet] \"{sheet_name}\" (第{sheet_index + 1}/{total_sheets}个工作表, "
            f"全表{sheet_total_rows}行×{sheet_total_cols}列)"
        )

        # 2) 选区在表中的位置描述
        pos_parts: list[str] = []
        if min_row == 1:
            pos_parts.append("从第1行开始（含表头行）")
        else:
            pos_parts.append(f"从第{min_row}行开始")
        if max_row == sheet_total_rows:
            pos_parts.append("到最后一行")
        else:
            pos_parts.append(f"到第{max_row}行")
        if min_col == 1 and max_col == sheet_total_cols:
            pos_parts.append("覆盖全部列")
        else:
            pos_parts.append(
                f"列{get_column_letter(min_col)}-{get_column_letter(max_col)}"
                f"(全表共{get_column_letter(sheet_total_cols)}列)"
            )
        lines.append(f"[Position] {', '.join(pos_parts)}")

        # 3) 列结构描述
        col_descs: list[str] = []
        for ci, col_letter in enumerate(col_headers):
            header_label = first_row_values[ci] if has_header and ci < len(first_row_values) else ""
            ctype = col_types[ci] if ci < len(col_types) else "unknown"
            type_label = {"numeric": "数值", "date": "日期", "text": "文本", "empty": "空"}.get(ctype, ctype)
            if header_label:
                col_descs.append(f"{col_letter}(\"{header_label}\",{type_label})")
            else:
                col_descs.append(f"{col_letter}({type_label})")
        lines.append(f"[Columns] {' | '.join(col_descs)}")

        # 4) 数据质量提示
        if empty_count > 0:
            pct = round(empty_count / total_cells * 100)
            lines.append(f"[DataQuality] {empty_count}/{total_cells}个单元格为空({pct}%)")

        # 5) 数据内容表格
        lines.append("")
        lines.append("| " + " | ".join(col_headers) + " |")
        lines.append("| " + " | ".join("---" for _ in col_headers) + " |")
        for row_data in data_rows:
            lines.append("| " + " | ".join(row_data) + " |")

        return "\n".join(lines)

    def _resolve_text_file(
        self, mention: Mention, path: Path
    ) -> ResolvedMention:
        """解析文本文件：读取前 N 行，受 token 预算限制。"""
        try:
            collected_lines: list[str] = []
            current_text = ""

            with open(path, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    candidate = current_text + line
                    token_count = _count_tokens(candidate)
                    if token_count > self._max_file_tokens:
                        # 当前行会超出预算，停止
                        break
                    collected_lines.append(line.rstrip("\n"))
                    current_text = candidate

            context = "\n".join(collected_lines)
            # 最终截断保障
            context = _truncate_to_tokens(context, self._max_file_tokens)
            return ResolvedMention(mention=mention, context_block=context)

        except Exception as exc:
            return ResolvedMention(
                mention=mention,
                error=f"文件读取失败：{mention.value}：{exc}",
            )

    # ── folder 解析 ───────────────────────────────────────

    def _resolve_folder(self, mention: Mention) -> ResolvedMention:
        """解析文件夹引用：安全校验 + 树形目录结构。"""
        # 安全校验
        try:
            resolved_path = self._guard.resolve_and_validate(mention.value)
        except SecurityViolationError as exc:
            return ResolvedMention(mention=mention, error=str(exc))

        if not resolved_path.exists():
            return ResolvedMention(
                mention=mention, error=f"目录不存在：{mention.value}"
            )

        if not resolved_path.is_dir():
            return ResolvedMention(
                mention=mention, error=f"路径不是目录：{mention.value}"
            )

        tree = self._build_tree(resolved_path, depth=0)
        return ResolvedMention(mention=mention, context_block=tree)

    def _build_tree(
        self,
        dir_path: Path,
        depth: int,
        prefix: str = "",
        is_last: bool = True,
    ) -> str:
        """递归构建目录树文本，深度 ≤ max_folder_depth，排除隐藏/排除项。"""
        lines: list[str] = []

        if depth == 0:
            # 根目录名
            lines.append(f"{dir_path.name}/")
        else:
            connector = "└── " if is_last else "├── "
            name = dir_path.name
            if dir_path.is_dir():
                name += "/"
            lines.append(f"{prefix}{connector}{name}")

        if depth >= self._max_folder_depth:
            return "\n".join(lines)

        if not dir_path.is_dir():
            return "\n".join(lines)

        # 列出子项，排除隐藏文件和排除目录
        try:
            entries = sorted(dir_path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        except PermissionError:
            return "\n".join(lines)

        filtered = [
            e for e in entries
            if not e.name.startswith(".") and e.name not in _EXCLUDED_NAMES
        ]

        if depth == 0:
            child_prefix = ""
        else:
            child_prefix = prefix + ("    " if is_last else "│   ")

        for i, entry in enumerate(filtered):
            is_entry_last = i == len(filtered) - 1
            if entry.is_dir():
                subtree = self._build_tree(
                    entry,
                    depth=depth + 1,
                    prefix=child_prefix,
                    is_last=is_entry_last,
                )
                lines.append(subtree)
            else:
                connector = "└── " if is_entry_last else "├── "
                lines.append(f"{child_prefix}{connector}{entry.name}")

        return "\n".join(lines)

    # ── skill 解析 ────────────────────────────────────────

    def _resolve_skill(self, mention: Mention) -> ResolvedMention:
        """解析 Skill 引用：加载 SKILL.md 内容。"""
        if self._skill_loader is None:
            return ResolvedMention(
                mention=mention, error=f"技能不存在：{mention.value}"
            )

        skill = self._skill_loader.get_skillpack(mention.value)
        if skill is None:
            return ResolvedMention(
                mention=mention, error=f"技能不存在：{mention.value}"
            )

        context = skill.render_context()
        return ResolvedMention(mention=mention, context_block=context)

    # ── mcp 解析 ──────────────────────────────────────────

    async def _resolve_mcp(self, mention: Mention) -> ResolvedMention:
        """解析 MCP 服务引用：查询工具列表。"""
        if self._mcp_manager is None:
            return ResolvedMention(
                mention=mention,
                error=f"MCP 服务未连接或不存在：{mention.value}",
            )

        connected = self._mcp_manager.connected_servers()
        if mention.value not in connected:
            return ResolvedMention(
                mention=mention,
                error=f"MCP 服务未连接或不存在：{mention.value}",
            )

        # 从 get_server_info 获取工具列表
        server_info_list = self._mcp_manager.get_server_info()
        tools_desc: list[str] = []
        for info in server_info_list:
            if info.get("name") == mention.value:
                tool_names = info.get("tools", [])
                tools_desc = tool_names
                break

        if tools_desc:
            tools_str = ", ".join(tools_desc)
            context = f"Server: {mention.value}\nTools: {tools_str}"
        else:
            context = f"Server: {mention.value}\nTools: (无工具)"

        return ResolvedMention(mention=mention, context_block=context)
