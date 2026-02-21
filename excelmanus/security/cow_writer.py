"""工具层写入保护：路径校验 + Bench CoW + 原子写入。

macro_tools 等需要在 agent 进程内直接写入 Excel 的模块共用此层，
与 run_code 沙盒中的 Auto-CoW 行为保持一致。
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from excelmanus.security import FileAccessGuard


class CowWriter:
    """工具层写入保护。

    使用方式::

        writer = CowWriter(guard)
        target = writer.resolve(file_path)   # 校验路径，bench 目录自动 CoW
        # ... 用 pandas/openpyxl 操作 target ...
        writer.atomic_save_workbook(wb, target)
        cow_mapping = writer.cow_mapping      # 返回给 engine
    """

    def __init__(self, guard: FileAccessGuard) -> None:
        self.guard = guard
        self.cow_mapping: dict[str, str] = {}
        self._bench_protected_dirs: list[Path] = self._load_bench_protected_dirs()

    # ── 公共 API ──────────────────────────────────────────────

    def resolve(self, file_path: str) -> Path:
        """校验并解析路径。bench 保护目录内的文件自动 CoW 到 outputs/。"""
        resolved = self.guard.resolve_and_validate(file_path)
        if self._is_bench_protected(resolved):
            return self._cow_copy(resolved)
        return resolved

    def atomic_save_workbook(self, wb: Any, target: Path) -> None:
        """openpyxl Workbook 原子写入（tempfile + os.replace）。"""
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            wb.save(str(target))
            return
        fd, tmp = tempfile.mkstemp(suffix=".xlsx", dir=str(target.parent))
        os.close(fd)
        try:
            wb.save(tmp)
            os.replace(tmp, str(target))
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    def atomic_save_dataframe(
        self,
        df: Any,
        target: Path,
        sheet_name: str,
        *,
        preserve_other_sheets: bool = True,
        index: bool = False,
    ) -> None:
        """pandas DataFrame 原子写入。

        当 *preserve_other_sheets* 为 True 且目标文件已存在时，
        使用 mode="a" + if_sheet_exists="replace" 保留其他 sheet。
        """
        import pandas as pd

        target.parent.mkdir(parents=True, exist_ok=True)

        if preserve_other_sheets and target.exists():
            # 先写到临时文件，再替换
            fd, tmp = tempfile.mkstemp(suffix=".xlsx", dir=str(target.parent))
            os.close(fd)
            try:
                shutil.copy2(str(target), tmp)
                with pd.ExcelWriter(
                    tmp,
                    engine="openpyxl",
                    mode="a",
                    if_sheet_exists="replace",
                ) as w:
                    df.to_excel(w, sheet_name=sheet_name, index=index)
                os.replace(tmp, str(target))
            except BaseException:
                Path(tmp).unlink(missing_ok=True)
                raise
        else:
            df.to_excel(str(target), sheet_name=sheet_name, index=index)

    # ── 内部实现 ──────────────────────────────────────────────

    def _load_bench_protected_dirs(self) -> list[Path]:
        """从环境变量加载 bench 保护目录列表。"""
        raw = os.environ.get("EXCELMANUS_BENCH_PROTECTED_DIRS", "bench/external")
        dirs: list[Path] = []
        for d in raw.split(","):
            d = d.strip()
            if d:
                dirs.append((self.guard.workspace_root / d).resolve())
        return dirs

    def _is_bench_protected(self, resolved: Path) -> bool:
        """判断路径是否在 bench 保护目录内。"""
        for protected in self._bench_protected_dirs:
            try:
                resolved.relative_to(protected)
                return True
            except ValueError:
                continue
        return False

    def _cow_copy(self, resolved: Path) -> Path:
        """Copy-on-Write：将文件复制到 outputs/ 并记录映射。"""
        rel_str = str(resolved)
        if rel_str in self.cow_mapping:
            return Path(self.cow_mapping[rel_str])

        output_dir = self.guard.workspace_root / "outputs"
        output_dir.mkdir(parents=True, exist_ok=True)
        redirect = output_dir / resolved.name

        # 若目标已存在，加后缀避免冲突
        if redirect.exists():
            stem = redirect.stem
            suffix = redirect.suffix
            counter = 1
            while redirect.exists():
                redirect = output_dir / f"{stem}_{counter}{suffix}"
                counter += 1

        if resolved.exists():
            shutil.copy2(str(resolved), str(redirect))

        # 记录映射（相对路径）
        try:
            rel_src = str(resolved.relative_to(self.guard.workspace_root))
            rel_dst = str(redirect.relative_to(self.guard.workspace_root))
        except ValueError:
            rel_src = str(resolved)
            rel_dst = str(redirect)
        self.cow_mapping[rel_src] = rel_dst

        return redirect
