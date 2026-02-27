"""Skill 导入器单元测试。"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from excelmanus.skillpacks.importer import (
    SkillImportError,
    _collect_directory_files,
    _parse_github_url,
    import_from_local_path,
    parse_skill_md,
    preview_skill_md,
)


# ── parse_skill_md ──────────────────────────────────────


class TestParseSkillMd:
    def test_basic_parse(self):
        text = textwrap.dedent("""\
            ---
            name: test-skill
            description: A test skill.
            ---
            Step 1: Do something.
            Step 2: Do something else.
        """)
        result = parse_skill_md(text)
        assert result["name"] == "test-skill"
        assert result["description"] == "A test skill."
        assert "Step 1" in result["instructions"]
        assert "Step 2" in result["instructions"]

    def test_missing_frontmatter_raises(self):
        with pytest.raises(SkillImportError, match="frontmatter"):
            parse_skill_md("No frontmatter here.")

    def test_missing_name_raises(self):
        text = textwrap.dedent("""\
            ---
            description: Missing name.
            ---
            Body.
        """)
        with pytest.raises(SkillImportError, match="name"):
            parse_skill_md(text)

    def test_missing_description_raises(self):
        text = textwrap.dedent("""\
            ---
            name: no-desc
            ---
            Body.
        """)
        with pytest.raises(SkillImportError, match="description"):
            parse_skill_md(text)

    def test_extra_fields_preserved(self):
        text = textwrap.dedent("""\
            ---
            name: rich-skill
            description: Rich.
            version: "2.0"
            resources:
              - scripts/helper.py
            ---
            Instructions here.
        """)
        result = parse_skill_md(text)
        assert result["version"] == "2.0"
        assert result["resources"] == ["scripts/helper.py"]


# ── preview_skill_md ────────────────────────────────────


class TestPreviewSkillMd:
    def test_preview_returns_summary(self):
        text = textwrap.dedent("""\
            ---
            name: preview-test
            description: Preview test.
            resources:
              - ref.md
            ---
            Long instructions here.
        """)
        result = preview_skill_md(text)
        assert result["name"] == "preview-test"
        assert result["description"] == "Preview test."
        assert result["has_resources"] is True
        assert "ref.md" in result["resources"]
        assert "name" in result["frontmatter_keys"]


# ── _parse_github_url ───────────────────────────────────


class TestParseGithubUrl:
    def test_blob_url(self):
        url = "https://github.com/org/repo/blob/main/skills/my-skill/SKILL.md"
        owner, repo, ref, dir_path, filename = _parse_github_url(url)
        assert owner == "org"
        assert repo == "repo"
        assert ref == "main"
        assert dir_path == "skills/my-skill"
        assert filename == "SKILL.md"

    def test_raw_url(self):
        url = "https://raw.githubusercontent.com/org/repo/main/skills/my-skill/SKILL.md"
        owner, repo, ref, dir_path, filename = _parse_github_url(url)
        assert owner == "org"
        assert repo == "repo"
        assert ref == "main"
        assert dir_path == "skills/my-skill"
        assert filename == "SKILL.md"

    def test_blob_url_root_file(self):
        url = "https://github.com/org/repo/blob/main/SKILL.md"
        owner, repo, ref, dir_path, filename = _parse_github_url(url)
        assert dir_path == ""
        assert filename == "SKILL.md"

    def test_unsupported_url_raises(self):
        with pytest.raises(SkillImportError, match="不支持的 URL"):
            _parse_github_url("https://example.com/not-github")


# ── _collect_directory_files ────────────────────────────


class TestCollectDirectoryFiles:
    def test_collects_files(self, tmp_path: Path):
        skill_dir = tmp_path / "my-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text("content")
        scripts = skill_dir / "scripts"
        scripts.mkdir()
        (scripts / "helper.py").write_text("# helper")
        (skill_dir / ".hidden").write_text("hidden")

        files = _collect_directory_files(skill_dir)
        assert "SKILL.md" in files
        assert "scripts/helper.py" in files
        # 隐藏文件被排除
        assert ".hidden" not in files

    def test_ignores_pycache(self, tmp_path: Path):
        d = tmp_path / "skill"
        d.mkdir()
        (d / "SKILL.md").write_text("x")
        pycache = d / "__pycache__"
        pycache.mkdir()
        (pycache / "mod.pyc").write_text("x")

        files = _collect_directory_files(d)
        assert all("__pycache__" not in f for f in files)


# ── import_from_local_path ──────────────────────────────


class TestImportFromLocalPath:
    def _make_skill_dir(self, tmp_path: Path, name: str = "test-skill") -> Path:
        skill_dir = tmp_path / "source" / name
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(textwrap.dedent(f"""\
            ---
            name: {name}
            description: A test skill.
            ---
            Do the thing.
        """))
        scripts = skill_dir / "scripts"
        scripts.mkdir()
        (scripts / "run.sh").write_text("#!/bin/bash\necho hello")
        return skill_dir

    def test_import_copies_files(self, tmp_path: Path):
        skill_dir = self._make_skill_dir(tmp_path)
        dest = tmp_path / "dest"
        dest.mkdir()

        result = import_from_local_path(
            str(skill_dir / "SKILL.md"),
            str(dest),
        )
        assert result.name == "test-skill"
        assert result.source_type == "local_path"
        assert "SKILL.md" in result.files_copied
        assert "scripts/run.sh" in result.files_copied

        # 文件实际存在
        assert (dest / "test-skill" / "SKILL.md").exists()
        assert (dest / "test-skill" / "scripts" / "run.sh").exists()

    def test_import_rejects_non_skill_md(self, tmp_path: Path):
        bad_file = tmp_path / "readme.md"
        bad_file.write_text("not a skill")
        dest = tmp_path / "dest"
        dest.mkdir()

        with pytest.raises(SkillImportError, match="SKILL.md"):
            import_from_local_path(str(bad_file), str(dest))

    def test_import_rejects_existing_without_overwrite(self, tmp_path: Path):
        skill_dir = self._make_skill_dir(tmp_path)
        dest = tmp_path / "dest"
        (dest / "test-skill").mkdir(parents=True)

        with pytest.raises(SkillImportError, match="已存在"):
            import_from_local_path(str(skill_dir / "SKILL.md"), str(dest))

    def test_import_overwrites_with_flag(self, tmp_path: Path):
        skill_dir = self._make_skill_dir(tmp_path)
        dest = tmp_path / "dest"
        (dest / "test-skill").mkdir(parents=True)

        result = import_from_local_path(
            str(skill_dir / "SKILL.md"),
            str(dest),
            overwrite=True,
        )
        assert result.name == "test-skill"
        assert (dest / "test-skill" / "SKILL.md").exists()

    def test_import_nonexistent_file_raises(self, tmp_path: Path):
        with pytest.raises(SkillImportError, match="不存在"):
            import_from_local_path(
                str(tmp_path / "nonexistent" / "SKILL.md"),
                str(tmp_path / "dest"),
            )
