"""文件访问守卫单元测试。"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from excelmanus.security import FileAccessGuard, SecurityViolationError


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """创建临时工作目录并返回。"""
    return tmp_path


@pytest.fixture
def guard(workspace: Path) -> FileAccessGuard:
    """基于临时工作目录创建 FileAccessGuard 实例。"""
    return FileAccessGuard(str(workspace))


class TestResolveAndValidate:
    """resolve_and_validate 方法测试。"""

    def test_relative_path_within_workspace(
        self, guard: FileAccessGuard, workspace: Path
    ) -> None:
        """相对路径应解析为工作目录下的绝对路径。"""
        (workspace / "data.xlsx").touch()
        result = guard.resolve_and_validate("data.xlsx")
        assert result == workspace / "data.xlsx"

    def test_nested_relative_path(
        self, guard: FileAccessGuard, workspace: Path
    ) -> None:
        """嵌套相对路径应正确解析。"""
        sub = workspace / "sub"
        sub.mkdir()
        (sub / "file.xlsx").touch()
        result = guard.resolve_and_validate("sub/file.xlsx")
        assert result == sub / "file.xlsx"

    def test_absolute_path_within_workspace(
        self, guard: FileAccessGuard, workspace: Path
    ) -> None:
        """工作目录内的绝对路径应通过校验。"""
        target = workspace / "report.xlsx"
        target.touch()
        result = guard.resolve_and_validate(str(target))
        assert result == target

    def test_path_traversal_rejected(self, guard: FileAccessGuard) -> None:
        """包含 .. 的路径穿越应被拒绝。"""
        with pytest.raises(SecurityViolationError, match="路径"):
            guard.resolve_and_validate("../../../etc/passwd")

    def test_absolute_path_outside_workspace(self, guard: FileAccessGuard) -> None:
        """工作目录外的绝对路径应被拒绝。"""
        with pytest.raises(SecurityViolationError, match="路径越界"):
            guard.resolve_and_validate("/tmp/evil.xlsx")

    def test_dot_dot_in_middle_rejected(
        self, guard: FileAccessGuard, workspace: Path
    ) -> None:
        """路径中间的 .. 若导致越界应被拒绝。"""
        with pytest.raises(SecurityViolationError, match="路径"):
            guard.resolve_and_validate("sub/../../outside.xlsx")

    def test_dot_dot_staying_inside_rejected(
        self, guard: FileAccessGuard, workspace: Path
    ) -> None:
        """路径中的 .. 即使最终仍在工作目录内也应拒绝。"""
        sub = workspace / "a" / "b"
        sub.mkdir(parents=True)
        (workspace / "a" / "ok.txt").touch()
        with pytest.raises(SecurityViolationError, match="路径穿越特征被拒绝"):
            guard.resolve_and_validate("a/b/../ok.txt")

    def test_url_encoded_dot_dot_rejected(
        self, guard: FileAccessGuard
    ) -> None:
        """URL 编码的 ../ 也应识别并拒绝。"""
        with pytest.raises(SecurityViolationError, match="路径穿越特征被拒绝"):
            guard.resolve_and_validate("foo/%2e%2e/bar.txt")

    def test_url_encoded_slash_in_dot_dot_segment_rejected(
        self, guard: FileAccessGuard
    ) -> None:
        """..%2F 形式的编码穿越片段应拒绝。"""
        with pytest.raises(SecurityViolationError, match="路径穿越特征被拒绝"):
            guard.resolve_and_validate("foo/..%2Fbar.txt")

    def test_symlink_inside_workspace_allowed(
        self, guard: FileAccessGuard, workspace: Path
    ) -> None:
        """指向工作目录内的符号链接应允许。"""
        real_file = workspace / "real.xlsx"
        real_file.touch()
        link = workspace / "link.xlsx"
        link.symlink_to(real_file)
        result = guard.resolve_and_validate("link.xlsx")
        assert result == real_file

    def test_symlink_escaping_workspace_rejected(
        self, workspace: Path, tmp_path_factory: pytest.TempPathFactory
    ) -> None:
        """指向工作目录外的符号链接应被拒绝。"""
        # 创建一个独立于 workspace 的外部目录
        outside = tmp_path_factory.mktemp("outside")
        evil_file = outside / "evil.xlsx"
        evil_file.touch()

        # 在 workspace 内创建子目录作为受限工作区
        restricted = workspace / "restricted"
        restricted.mkdir()
        guard = FileAccessGuard(str(restricted))

        link = restricted / "escape_link.xlsx"
        link.symlink_to(evil_file)
        with pytest.raises(SecurityViolationError, match="路径越界"):
            guard.resolve_and_validate("escape_link.xlsx")

    def test_dangling_symlink_rejected(
        self, workspace: Path
    ) -> None:
        """悬空符号链接应被拒绝，避免 strict=False 导致的目标判断不准确。"""
        target = workspace / "missing.xlsx"
        link = workspace / "dangling_link.xlsx"
        link.symlink_to(target)
        with pytest.raises(SecurityViolationError, match="符号链接|不存在"):
            guard = FileAccessGuard(str(workspace))
            guard.resolve_and_validate("dangling_link.xlsx")

    def test_nonexistent_regular_path_inside_workspace_allowed(
        self, guard: FileAccessGuard, workspace: Path
    ) -> None:
        """不存在的常规目标文件（用于新建）在工作目录内应允许。"""
        result = guard.resolve_and_validate("new_dir/new_file.xlsx")
        assert result == workspace / "new_dir" / "new_file.xlsx"

    def test_workspace_root_property(self, workspace: Path) -> None:
        """workspace_root 属性应返回规范化后的路径。"""
        guard = FileAccessGuard(str(workspace))
        assert guard.workspace_root == workspace.resolve()

    def test_current_dir_dot(
        self, guard: FileAccessGuard, workspace: Path
    ) -> None:
        """'.' 应解析为工作目录本身。"""
        result = guard.resolve_and_validate(".")
        assert result == workspace


# ── Property 19：文件访问边界（属性测试） ─────────────────

import tempfile

from hypothesis import given, strategies as st


# 路径穿越 payload 策略：生成包含 .. 的越界路径
path_traversal_st = st.one_of(
    # 经典 ../ 穿越
    st.builds(
        lambda n: "../" * n + "etc/passwd",
        st.integers(min_value=1, max_value=10),
    ),
    # 中间穿越
    st.builds(
        lambda prefix, n: f"{prefix}/" + "../" * n + "outside.txt",
        st.text(alphabet="abcdefghijklmnop", min_size=1, max_size=5),
        st.integers(min_value=2, max_value=10),
    ),
    # 绝对路径越界
    st.builds(
        lambda suffix: f"/tmp/{suffix}",
        st.text(alphabet="abcdefghijklmnop", min_size=1, max_size=10),
    ),
)


class TestProperty19FileAccessBoundary:
    """Property 19：路径越界或路径穿越必须被拒绝，并抛出 SecurityViolationError。

    **Validates: Requirements 8.1, 8.2, 6.8**
    """

    @given(path=path_traversal_st)
    def test_traversal_paths_rejected(self, path: str) -> None:
        """任意路径穿越或越界路径必须抛出 SecurityViolationError。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            guard = FileAccessGuard(tmpdir)
            with pytest.raises(SecurityViolationError):
                guard.resolve_and_validate(path)

    @given(
        depth=st.integers(min_value=1, max_value=20),
    )
    def test_dot_dot_depth_always_rejected(self, depth: int) -> None:
        """任意深度的 ../ 穿越都必须被拒绝。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            guard = FileAccessGuard(tmpdir)
            evil_path = "../" * depth + "secret.txt"
            with pytest.raises(SecurityViolationError):
                guard.resolve_and_validate(evil_path)

    @given(
        filename=st.text(
            alphabet=st.characters(whitelist_categories=("Ll", "N"), whitelist_characters="_-."),
            min_size=1,
            max_size=20,
        ).filter(lambda s: not s.startswith(".") and not s.startswith("/"))
    )
    def test_safe_relative_paths_allowed(self, filename: str) -> None:
        """工作目录内的合法相对路径应被允许。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            guard = FileAccessGuard(tmpdir)
            # 创建文件以确保路径有效
            (Path(tmpdir) / filename).touch()
            result = guard.resolve_and_validate(filename)
            assert result == (Path(tmpdir) / filename).resolve()
