"""file_tools 工具函数测试：覆盖全部 7 个工具的正常路径与异常路径。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from excelmanus.security import SecurityViolationError
from excelmanus.tools import file_tools


# ── fixtures ─────────────────────────────────────────────


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    """创建一个临时工作区并初始化 guard。"""
    # 基础文件结构
    (tmp_path / "hello.txt").write_text("你好\n世界\n第三行\n", encoding="utf-8")
    (tmp_path / "data.csv").write_text("a,b,c\n1,2,3\n4,5,6\n", encoding="utf-8")
    (tmp_path / "report.xlsx").write_bytes(b"\x00FAKE_EXCEL")
    (tmp_path / "subdir").mkdir()
    (tmp_path / "subdir" / "nested.txt").write_text("nested content", encoding="utf-8")
    (tmp_path / ".hidden").write_text("secret", encoding="utf-8")

    file_tools.init_guard(str(tmp_path))
    return tmp_path


# ── list_directory ───────────────────────────────────────



class TestListDirectory:
    # ── depth=0 扁平分页模式（原有行为） ──

    def test_flat_list_root(self, workspace: Path) -> None:
        result = json.loads(file_tools.list_directory(depth=0))
        assert result["total"] >= 3
        names = [e["name"] for e in result["entries"]]
        assert "hello.txt" in names
        assert "subdir" in names

    def test_flat_hidden_files_excluded_by_default(self, workspace: Path) -> None:
        result = json.loads(file_tools.list_directory(depth=0))
        names = [e["name"] for e in result["entries"]]
        assert ".hidden" not in names

    def test_flat_hidden_files_shown(self, workspace: Path) -> None:
        result = json.loads(file_tools.list_directory(show_hidden=True, depth=0))
        names = [e["name"] for e in result["entries"]]
        assert ".hidden" in names

    def test_flat_subdirectory(self, workspace: Path) -> None:
        result = json.loads(file_tools.list_directory("subdir", depth=0))
        assert result["total"] == 1
        assert result["entries"][0]["name"] == "nested.txt"

    def test_invalid_directory(self, workspace: Path) -> None:
        result = json.loads(file_tools.list_directory("nonexistent"))
        assert "error" in result

    def test_path_traversal_rejected(self, workspace: Path) -> None:
        with pytest.raises(SecurityViolationError):
            file_tools.list_directory("..")

    def test_pagination(self, workspace: Path) -> None:
        full = json.loads(file_tools.list_directory(depth=0))
        page = json.loads(file_tools.list_directory(offset=0, limit=2, depth=0))
        assert page["total"] == full["total"]
        assert page["offset"] == 0
        assert page["limit"] == 2
        assert page["returned"] == 2
        assert page["entries"] == full["entries"][:2]
        assert page["has_more"] is True

    def test_cursor_pagination(self, workspace: Path) -> None:
        full = json.loads(file_tools.list_directory(depth=0, use_default_excludes=False))
        page1 = json.loads(file_tools.list_directory(depth=0, limit=2, cursor="0", use_default_excludes=False))
        assert page1["offset"] == 0
        assert page1["returned"] == 2
        assert page1["next_cursor"] == "2"
        page2 = json.loads(
            file_tools.list_directory(
                depth=0,
                limit=2,
                cursor=page1["next_cursor"],
                use_default_excludes=False,
            )
        )
        assert page2["offset"] == 2
        assert page2["entries"] == full["entries"][2:4]

    def test_invalid_cursor(self, workspace: Path) -> None:
        result = json.loads(file_tools.list_directory(depth=0, cursor="bad_cursor"))
        assert "error" in result

    def test_default_excludes_noise_directories(self, workspace: Path) -> None:
        (workspace / "outputs").mkdir()
        (workspace / "outputs" / "debug.log").write_text("x", encoding="utf-8")
        result = json.loads(file_tools.list_directory(depth=0))
        names = [e["name"] for e in result["entries"]]
        assert "outputs" not in names
        assert result["omitted"]["ignored_by_pattern"] >= 1

    def test_disable_default_excludes(self, workspace: Path) -> None:
        (workspace / "outputs").mkdir()
        result = json.loads(file_tools.list_directory(depth=0, use_default_excludes=False))
        names = [e["name"] for e in result["entries"]]
        assert "outputs" in names

    def test_overview_mode(self, workspace: Path) -> None:
        (workspace / "cache").mkdir()
        (workspace / "cache" / "x.bin").write_bytes(b"abc")
        result = json.loads(file_tools.list_directory(mode="overview", limit=2))
        assert result["mode"] == "overview"
        assert "summary" in result
        assert "hotspots" in result
        assert "entries" in result
        assert result["returned"] <= 2

    def test_pagination_invalid_args(self, workspace: Path) -> None:
        result = json.loads(file_tools.list_directory(offset=-1, limit=10, depth=0))
        assert "error" in result
        result = json.loads(file_tools.list_directory(offset=0, limit=0, depth=0))
        assert "error" in result

    # ── 递归树模式 ──

    def test_tree_default_depth(self, workspace: Path) -> None:
        """默认 depth=2，返回树结构且包含子目录内容。"""
        result = json.loads(file_tools.list_directory())
        assert "tree" in result
        assert result["depth"] == 2
        names = [e["name"] for e in result["tree"]]
        assert "subdir" in names
        # subdir 应该被展开，包含 children
        subdir = next(e for e in result["tree"] if e["name"] == "subdir")
        assert "children" in subdir
        child_names = [c["name"] for c in subdir["children"]]
        assert "nested.txt" in child_names

    def test_tree_depth_1(self, workspace: Path) -> None:
        """depth=1 只列出当前层，目录不展开 children。"""
        result = json.loads(file_tools.list_directory(depth=1))
        assert result["depth"] == 1
        subdir = next(e for e in result["tree"] if e["name"] == "subdir")
        assert "children" not in subdir

    def test_tree_hidden_excluded(self, workspace: Path) -> None:
        result = json.loads(file_tools.list_directory(depth=1))
        names = [e["name"] for e in result["tree"]]
        assert ".hidden" not in names

    def test_tree_hidden_shown(self, workspace: Path) -> None:
        result = json.loads(file_tools.list_directory(show_hidden=True, depth=1))
        names = [e["name"] for e in result["tree"]]
        assert ".hidden" in names



# ── get_file_info ────────────────────────────────────────


class TestGetFileInfo:
    def test_file_info(self, workspace: Path) -> None:
        result = json.loads(file_tools.get_file_info("hello.txt"))
        assert result["name"] == "hello.txt"
        assert result["type"] == "file"
        assert result["extension"] == "txt"
        assert "size" in result
        assert "modified" in result

    def test_directory_info(self, workspace: Path) -> None:
        result = json.loads(file_tools.get_file_info("subdir"))
        assert result["type"] == "directory"
        assert result["children_count"] == 1
        assert result["extension"] is None

    def test_nonexistent(self, workspace: Path) -> None:
        result = json.loads(file_tools.get_file_info("no_such_file"))
        assert "error" in result

    def test_path_traversal_rejected(self, workspace: Path) -> None:
        with pytest.raises(SecurityViolationError):
            file_tools.get_file_info("../etc/passwd")


# ── find_files ─────────────────────────────────────────


class TestFindFiles:
    def test_search_txt(self, workspace: Path) -> None:
        result = json.loads(file_tools.find_files("*.txt"))
        assert result["total"] >= 1
        names = [m["name"] for m in result["matches"]]
        assert "hello.txt" in names

    def test_search_recursive(self, workspace: Path) -> None:
        result = json.loads(file_tools.find_files("**/*.txt"))
        names = [m["name"] for m in result["matches"]]
        assert "nested.txt" in names

    def test_search_no_match(self, workspace: Path) -> None:
        result = json.loads(file_tools.find_files("*.docx"))
        assert result["total"] == 0

    def test_search_max_results(self, workspace: Path) -> None:
        result = json.loads(file_tools.find_files("*", max_results=2))
        assert result["total"] <= 2
        assert result["truncated"] in (True, False)

    def test_search_hidden_excluded(self, workspace: Path) -> None:
        result = json.loads(file_tools.find_files(".*"))
        names = [m["name"] for m in result["matches"]]
        assert ".hidden" not in names

    def test_invalid_directory(self, workspace: Path) -> None:
        result = json.loads(file_tools.find_files("*", directory="nonexistent"))
        assert "error" in result


# ── read_text_file ───────────────────────────────────────


class TestReadTextFile:
    def test_read_txt(self, workspace: Path) -> None:
        result = json.loads(file_tools.read_text_file("hello.txt"))
        assert result["file"] == "hello.txt"
        assert "你好" in result["content"]
        assert result["lines_read"] == 3

    def test_read_csv(self, workspace: Path) -> None:
        result = json.loads(file_tools.read_text_file("data.csv"))
        assert "a,b,c" in result["content"]

    def test_read_with_max_lines(self, workspace: Path) -> None:
        result = json.loads(file_tools.read_text_file("hello.txt", max_lines=1))
        assert result["lines_read"] == 1
        assert result["truncated"] is True

    def test_read_exact_max_lines_not_truncated(self, workspace: Path) -> None:
        result = json.loads(file_tools.read_text_file("hello.txt", max_lines=3))
        assert result["lines_read"] == 3
        assert result["truncated"] is False

    def test_read_binary_file_error(self, workspace: Path) -> None:
        result = json.loads(file_tools.read_text_file("report.xlsx"))
        # 二进制文件可能不报错（取决于内容），但不会崩溃
        assert "file" in result or "error" in result

    def test_nonexistent_file(self, workspace: Path) -> None:
        result = json.loads(file_tools.read_text_file("no_such.txt"))
        assert "error" in result

    def test_directory_rejected(self, workspace: Path) -> None:
        result = json.loads(file_tools.read_text_file("subdir"))
        assert "error" in result

    def test_path_traversal_rejected(self, workspace: Path) -> None:
        with pytest.raises(SecurityViolationError):
            file_tools.read_text_file("../secret.txt")


# ── copy_file ────────────────────────────────────────────


class TestCopyFile:
    def test_copy_success(self, workspace: Path) -> None:
        result = json.loads(file_tools.copy_file("hello.txt", "hello_copy.txt"))
        assert result["status"] == "success"
        assert (workspace / "hello_copy.txt").exists()
        # 内容一致
        assert (workspace / "hello_copy.txt").read_text(encoding="utf-8") == (
            workspace / "hello.txt"
        ).read_text(encoding="utf-8")

    def test_copy_to_subdir(self, workspace: Path) -> None:
        result = json.loads(file_tools.copy_file("hello.txt", "newdir/hello.txt"))
        assert result["status"] == "success"
        assert (workspace / "newdir" / "hello.txt").exists()

    def test_copy_source_not_file(self, workspace: Path) -> None:
        result = json.loads(file_tools.copy_file("subdir", "subdir_copy"))
        assert "error" in result

    def test_copy_destination_exists(self, workspace: Path) -> None:
        result = json.loads(file_tools.copy_file("hello.txt", "data.csv"))
        assert "error" in result
        assert "已存在" in result["error"]

    def test_copy_path_traversal(self, workspace: Path) -> None:
        with pytest.raises(SecurityViolationError):
            file_tools.copy_file("hello.txt", "../outside.txt")


# ── rename_file ──────────────────────────────────────────


class TestRenameFile:
    def test_rename_success(self, workspace: Path) -> None:
        result = json.loads(file_tools.rename_file("hello.txt", "greeting.txt"))
        assert result["status"] == "success"
        assert not (workspace / "hello.txt").exists()
        assert (workspace / "greeting.txt").exists()

    def test_rename_to_subdir(self, workspace: Path) -> None:
        result = json.loads(file_tools.rename_file("data.csv", "archive/data.csv"))
        assert result["status"] == "success"
        assert (workspace / "archive" / "data.csv").exists()

    def test_rename_source_not_file(self, workspace: Path) -> None:
        result = json.loads(file_tools.rename_file("subdir", "subdir_new"))
        assert "error" in result

    def test_rename_destination_exists(self, workspace: Path) -> None:
        result = json.loads(file_tools.rename_file("hello.txt", "data.csv"))
        assert "error" in result

    def test_rename_path_traversal(self, workspace: Path) -> None:
        with pytest.raises(SecurityViolationError):
            file_tools.rename_file("hello.txt", "../outside.txt")


# ── delete_file ──────────────────────────────────────────


class TestDeleteFile:
    def test_delete_without_confirm(self, workspace: Path) -> None:
        result = json.loads(file_tools.delete_file("hello.txt"))
        assert result["status"] == "pending_confirmation"
        assert (workspace / "hello.txt").exists()  # 未实际删除

    def test_delete_with_confirm(self, workspace: Path) -> None:
        result = json.loads(file_tools.delete_file("hello.txt", confirm=True))
        assert result["status"] == "success"
        assert not (workspace / "hello.txt").exists()

    def test_delete_directory_rejected(self, workspace: Path) -> None:
        result = json.loads(file_tools.delete_file("subdir"))
        assert "error" in result
        assert "目录" in result["error"]

    def test_delete_nonexistent(self, workspace: Path) -> None:
        result = json.loads(file_tools.delete_file("no_such.txt"))
        assert "error" in result

    def test_delete_path_traversal(self, workspace: Path) -> None:
        with pytest.raises(SecurityViolationError):
            file_tools.delete_file("../important.txt")


# ── get_tools 注册 ───────────────────────────────────────


class TestGetTools:
    def test_tool_count(self) -> None:
        tools = file_tools.get_tools()
        assert len(tools) == 4  # Batch 5 精简: get_file_info/find_files/read_text_file 已删除

    def test_tool_names(self) -> None:
        names = {t.name for t in file_tools.get_tools()}
        expected = {
            "list_directory",
            # get_file_info, find_files, read_text_file: Batch 5 精简
            "copy_file",
            "rename_file",
            "delete_file",
        }
        assert names == expected

    def test_list_directory_disables_global_truncation(self) -> None:
        tools = {tool.name: tool for tool in file_tools.get_tools()}
        assert tools["list_directory"].max_result_chars == 0
