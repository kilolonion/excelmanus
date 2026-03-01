"""compare_excel 工具函数单元测试。

覆盖场景：
- 行号对齐模式（默认）
- 关键列匹配模式
- 跨 Sheet 对比
- 边界情况（文件不存在、全相同、列差异、大文件 hash 优化）
"""

from __future__ import annotations

import json
from pathlib import Path

import openpyxl
import pytest

from excelmanus.tools.data_tools import compare_excel, init_guard


# ── fixtures ──────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _set_guard(tmp_path: Path):
    """每个测试前将 FileAccessGuard 指向 tmp_path。"""
    init_guard(str(tmp_path))


def _make_xlsx(path: Path, data: dict[str, list[list]]) -> Path:
    """创建带多 Sheet 的 xlsx 测试文件。

    Args:
        path: 文件路径
        data: {sheet_name: [[row1], [row2], ...]}，第一行为表头
    """
    wb = openpyxl.Workbook()
    first = True
    for sheet_name, rows in data.items():
        if first:
            ws = wb.active
            ws.title = sheet_name
            first = False
        else:
            ws = wb.create_sheet(sheet_name)
        for row in rows:
            ws.append(row)
    wb.save(path)
    return path


# ── 行号对齐模式 ─────────────────────────────────────────

class TestRowAlignedMode:
    """默认行号对齐模式的对比测试。"""

    def test_identical_files(self, tmp_path: Path):
        """两个完全相同的文件应返回 0 差异。"""
        rows = [["姓名", "分数"]] + [["学生" + str(i), 60 + i] for i in range(10)]
        data = {"Sheet1": rows}
        fa = _make_xlsx(tmp_path / "a.xlsx", data)
        fb = _make_xlsx(tmp_path / "b.xlsx", data)

        result = json.loads(compare_excel(str(fa), str(fb)))

        assert result["status"] == "ok"
        assert result["summary"]["cells_different"] == 0
        assert result["summary"]["rows_added"] == 0
        assert result["summary"]["rows_deleted"] == 0
        assert "完全相同" in result["hint"]

    def test_modified_cells(self, tmp_path: Path):
        """修改的单元格应被检测到。"""
        base = [["姓名", "分数"]] + [["学生" + str(i), 60 + i] for i in range(10)]
        fa = _make_xlsx(tmp_path / "a.xlsx", {"Sheet1": base})
        modified = [row[:] for row in base]
        modified[1] = ["学生0", 99]  # 修改第一行数据的分数
        fb = _make_xlsx(tmp_path / "b.xlsx", {"Sheet1": modified})

        result = json.loads(compare_excel(str(fa), str(fb)))

        assert result["status"] == "ok"
        assert result["summary"]["cells_different"] > 0
        assert result["summary"]["rows_modified"] == 1
        # sample_diffs 应包含差异
        assert len(result["sample_diffs"]) >= 1
        diff = result["sample_diffs"][0]
        assert str(diff["old"]) == "60"
        assert str(diff["new"]) == "99"

    def test_added_rows(self, tmp_path: Path):
        """文件 B 多出的行应计为 rows_added。"""
        base = [["姓名", "分数"]] + [["学生" + str(i), 60 + i] for i in range(10)]
        fa = _make_xlsx(tmp_path / "a.xlsx", {"Sheet1": base})
        extended = base + [["新同学", 100]]
        fb = _make_xlsx(tmp_path / "b.xlsx", {"Sheet1": extended})

        result = json.loads(compare_excel(str(fa), str(fb)))

        assert result["status"] == "ok"
        assert result["summary"]["rows_added"] == 1

    def test_deleted_rows(self, tmp_path: Path):
        """文件 A 多出的行应计为 rows_deleted。"""
        base = [["姓名", "分数"]] + [["学生" + str(i), 60 + i] for i in range(10)]
        fa = _make_xlsx(tmp_path / "a.xlsx", {"Sheet1": base})
        shorter = base[:10]  # 去掉最后 1 行数据（header+9 data）
        fb = _make_xlsx(tmp_path / "b.xlsx", {"Sheet1": shorter})

        result = json.loads(compare_excel(str(fa), str(fb)))

        assert result["status"] == "ok"
        assert result["summary"]["rows_deleted"] == 1

    def test_column_difference(self, tmp_path: Path):
        """新增/删除的列应被检测到。"""
        rows_a = [["姓名", "分数", "等级"]] + [["学生" + str(i), 60 + i, "A"] for i in range(10)]
        rows_b = [["姓名", "分数", "班级"]] + [["学生" + str(i), 60 + i, "一班"] for i in range(10)]
        fa = _make_xlsx(tmp_path / "a.xlsx", {"Sheet1": rows_a})
        fb = _make_xlsx(tmp_path / "b.xlsx", {"Sheet1": rows_b})

        result = json.loads(compare_excel(str(fa), str(fb)))

        assert result["status"] == "ok"
        assert "班级" in result["summary"]["columns_added"]
        assert "等级" in result["summary"]["columns_deleted"]


# ── 关键列匹配模式 ───────────────────────────────────────

class TestKeyColumnMode:
    """指定 key_columns 时的关键列匹配模式测试。"""

    def test_key_match_modified(self, tmp_path: Path):
        """通过关键列匹配找到修改的行。"""
        rows_a = [["ID", "姓名", "分数"]] + [[i, "学生" + str(i), 60 + i] for i in range(1, 11)]
        rows_b = [row[:] for row in rows_a]
        rows_b[1] = [1, "学生1", 99]  # 修改 ID=1 的分数
        fa = _make_xlsx(tmp_path / "a.xlsx", {"Sheet1": rows_a})
        fb = _make_xlsx(tmp_path / "b.xlsx", {"Sheet1": rows_b})

        result = json.loads(compare_excel(
            str(fa), str(fb), key_columns=["ID"],
        ))

        assert result["status"] == "ok"
        assert result["summary"]["rows_modified"] == 1
        assert result["summary"]["rows_added"] == 0
        assert result["summary"]["rows_deleted"] == 0

    def test_key_match_added_deleted(self, tmp_path: Path):
        """通过关键列匹配找到新增和删除的行。"""
        rows_a = [["ID", "姓名"]] + [[i, "学生" + str(i)] for i in range(1, 11)]
        rows_b = [["ID", "姓名"]] + [[i, "学生" + str(i)] for i in range(1, 10)] + [[99, "新同学"]]
        fa = _make_xlsx(tmp_path / "a.xlsx", {"Sheet1": rows_a})
        fb = _make_xlsx(tmp_path / "b.xlsx", {"Sheet1": rows_b})

        result = json.loads(compare_excel(
            str(fa), str(fb), key_columns=["ID"],
        ))

        assert result["status"] == "ok"
        assert result["summary"]["rows_added"] == 1    # ID=99
        assert result["summary"]["rows_deleted"] == 1   # ID=10

    def test_invalid_key_falls_back(self, tmp_path: Path):
        """无效的关键列应回退到行号对齐模式。"""
        rows = [["姓名", "分数"]] + [["学生" + str(i), 60 + i] for i in range(10)]
        data = {"Sheet1": rows}
        fa = _make_xlsx(tmp_path / "a.xlsx", data)
        fb = _make_xlsx(tmp_path / "b.xlsx", data)

        result = json.loads(compare_excel(
            str(fa), str(fb), key_columns=["不存在的列"],
        ))

        assert result["status"] == "ok"
        assert result["summary"]["cells_different"] == 0


# ── 跨 Sheet 对比 ────────────────────────────────────────

class TestCrossSheet:
    """同一文件不同 Sheet 的对比测试。"""

    def test_cross_sheet_diff(self, tmp_path: Path):
        """同一文件不同 Sheet 的对比。"""
        rows_orig = [["姓名", "分数"]] + [["学生" + str(i), 60 + i] for i in range(10)]
        rows_mod = [row[:] for row in rows_orig]
        rows_mod[1] = ["学生0", 99]
        fp = _make_xlsx(tmp_path / "data.xlsx", {
            "原始": rows_orig,
            "修改后": rows_mod,
        })

        result = json.loads(compare_excel(
            str(fp), str(fp), sheet_a="原始", sheet_b="修改后",
        ))

        assert result["status"] == "ok"
        assert result["diff_mode"] == "cross_sheet"
        assert result["summary"]["cells_different"] > 0

    def test_cross_file_mode(self, tmp_path: Path):
        """不同文件的对比应标记为 cross_file。"""
        rows_a = [["x"]] + [[i] for i in range(10)]
        rows_b = [["x"]] + [[i + 100] for i in range(10)]
        fa = _make_xlsx(tmp_path / "a.xlsx", {"Sheet1": rows_a})
        fb = _make_xlsx(tmp_path / "b.xlsx", {"Sheet1": rows_b})

        result = json.loads(compare_excel(str(fa), str(fb)))

        assert result["diff_mode"] == "cross_file"


# ── 边界情况 ──────────────────────────────────────────────

class TestEdgeCases:
    """边界情况测试。"""

    def test_file_not_found(self, tmp_path: Path):
        """文件不存在应返回 error。"""
        result = json.loads(compare_excel(
            str(tmp_path / "不存在.xlsx"),
            str(tmp_path / "也不存在.xlsx"),
        ))
        assert "error" in result or "not_found" in json.dumps(result, ensure_ascii=False).lower() or "不存在" in json.dumps(result, ensure_ascii=False)

    def test_max_diffs_truncation(self, tmp_path: Path):
        """超过 max_diffs 应截断。"""
        rows_a = [["v"]] + [[i] for i in range(60)]
        rows_b = [["v"]] + [[i + 1000] for i in range(60)]
        fa = _make_xlsx(tmp_path / "a.xlsx", {"Sheet1": rows_a})
        fb = _make_xlsx(tmp_path / "b.xlsx", {"Sheet1": rows_b})

        result = json.loads(compare_excel(str(fa), str(fb), max_diffs=5))

        assert result["status"] == "ok"
        assert result["truncated"] is True
        assert len(result["sample_diffs"]) <= 10  # sample_diffs 最多 10 个

    def test_empty_files(self, tmp_path: Path):
        """两个空文件对比应返回 0 差异。"""
        fa = _make_xlsx(tmp_path / "a.xlsx", {"Sheet1": []})
        fb = _make_xlsx(tmp_path / "b.xlsx", {"Sheet1": []})

        result = json.loads(compare_excel(str(fa), str(fb)))

        assert result["status"] == "ok"
        assert result["summary"]["cells_different"] == 0

    def test_sheets_only_in_one_file(self, tmp_path: Path):
        """跨文件对比时应检测到独有的 Sheet。"""
        shared = [["x"]] + [[i] for i in range(10)]
        fa = _make_xlsx(tmp_path / "a.xlsx", {
            "共有Sheet": shared,
            "仅A有": [["y"]] + [[i] for i in range(10)],
        })
        fb = _make_xlsx(tmp_path / "b.xlsx", {
            "共有Sheet": shared,
            "仅B有": [["z"]] + [[i] for i in range(10)],
        })

        result = json.loads(compare_excel(str(fa), str(fb)))

        assert result["status"] == "ok"
        assert "仅A有" in result["summary"]["sheets_only_in_a"]
        assert "仅B有" in result["summary"]["sheets_only_in_b"]

    def test_csv_support(self, tmp_path: Path):
        """CSV 文件对比应正常工作。"""
        csv_a = tmp_path / "a.csv"
        csv_b = tmp_path / "b.csv"
        csv_a.write_text("姓名,分数\n张三,90\n", encoding="utf-8")
        csv_b.write_text("姓名,分数\n张三,95\n", encoding="utf-8")

        result = json.loads(compare_excel(str(csv_a), str(csv_b)))

        assert result["status"] == "ok"
        assert result["summary"]["cells_different"] > 0


# ── Policy 注册测试 ───────────────────────────────────────

class TestPolicyRegistration:
    """验证 compare_excel 在策略集合中的注册。"""

    def test_in_read_only_safe(self):
        from excelmanus.tools.policy import READ_ONLY_SAFE_TOOLS
        assert "compare_excel" in READ_ONLY_SAFE_TOOLS

    def test_in_parallelizable(self):
        from excelmanus.tools.policy import PARALLELIZABLE_READONLY_TOOLS
        assert "compare_excel" in PARALLELIZABLE_READONLY_TOOLS

    def test_in_data_read_category(self):
        from excelmanus.tools.policy import TOOL_CATEGORIES
        assert "compare_excel" in TOOL_CATEGORIES["data_read"]

    def test_in_short_descriptions(self):
        from excelmanus.tools.policy import TOOL_SHORT_DESCRIPTIONS
        assert "compare_excel" in TOOL_SHORT_DESCRIPTIONS


# ── 工具注册测试 ──────────────────────────────────────────

class TestToolRegistration:
    """验证 compare_excel 通过 get_tools() 导出。"""

    def test_registered_in_get_tools(self):
        from excelmanus.tools.data_tools import get_tools
        tools = get_tools()
        names = [t.name for t in tools]
        assert "compare_excel" in names

    def test_tool_schema(self):
        from excelmanus.tools.data_tools import get_tools
        tools = get_tools()
        tool = next(t for t in tools if t.name == "compare_excel")
        schema = tool.input_schema
        assert "file_a" in schema["properties"]
        assert "file_b" in schema["properties"]
        assert "key_columns" in schema["properties"]
        assert schema["properties"]["key_columns"]["type"] == "array"
        assert "file_a" in schema["required"]
        assert "file_b" in schema["required"]
        assert tool.write_effect == "none"
