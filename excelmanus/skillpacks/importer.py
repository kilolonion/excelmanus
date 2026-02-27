"""Skill 导入器：从本地文件路径或 GitHub URL 导入 SKILL.md 及附属资源。"""

from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from excelmanus.logger import get_logger
from excelmanus.skillpacks.loader import SkillpackLoader, SkillpackValidationError

logger = get_logger("skillpacks.importer")

# GitHub blob URL 模式：github.com/:owner/:repo/blob/:ref/:path
_GITHUB_BLOB_RE = re.compile(
    r"^https?://github\.com/"
    r"(?P<owner>[^/]+)/(?P<repo>[^/]+)"
    r"/blob/(?P<ref>[^/]+)/(?P<path>.+)$"
)

# 扫描同目录时忽略的文件/目录模式
_IGNORED_NAMES = {".git", ".DS_Store", "__pycache__", ".gitignore", "node_modules"}

# GitHub API 请求超时（秒）
_HTTP_TIMEOUT = 15.0


class SkillImportError(Exception):
    """导入过程失败。"""


class SkillImportResult:
    """导入结果。"""

    def __init__(
        self,
        *,
        name: str,
        description: str,
        source_type: str,
        files_copied: list[str],
        dest_dir: str,
    ) -> None:
        self.name = name
        self.description = description
        self.source_type = source_type
        self.files_copied = files_copied
        self.dest_dir = dest_dir

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "source_type": self.source_type,
            "files_copied": self.files_copied,
            "dest_dir": self.dest_dir,
        }


def parse_skill_md(text: str) -> dict[str, Any]:
    """解析 SKILL.md 文本，返回 frontmatter 字段 + instructions。

    Returns:
        dict 包含 name, description, instructions 等 frontmatter 字段。
    """
    fm_pattern = re.compile(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", re.DOTALL)
    match = fm_pattern.match(text)
    if not match:
        raise SkillImportError("SKILL.md 缺少 YAML frontmatter（文件应以 --- 开始）。")

    frontmatter_raw, body = match.groups()
    try:
        frontmatter = SkillpackLoader.parse_frontmatter(frontmatter_raw)
    except SkillpackValidationError as exc:
        raise SkillImportError(f"frontmatter 解析失败：{exc}") from exc

    if not frontmatter.get("name"):
        raise SkillImportError("SKILL.md frontmatter 缺少必填字段 `name`。")
    if not frontmatter.get("description"):
        raise SkillImportError("SKILL.md frontmatter 缺少必填字段 `description`。")

    result = dict(frontmatter)
    result["instructions"] = body.strip()
    return result


def import_from_local_path(
    skill_md_path: str,
    project_skills_dir: str,
    *,
    overwrite: bool = False,
) -> SkillImportResult:
    """从本地文件路径导入 SKILL.md 及同目录附属文件。

    Args:
        skill_md_path: SKILL.md 文件的绝对路径。
        project_skills_dir: project 层技能目录（将在此创建子目录）。
        overwrite: 是否覆盖已存在的同名技能目录。

    Returns:
        SkillImportResult 描述导入结果。
    """
    src_file = Path(skill_md_path).expanduser().resolve()
    if not src_file.exists():
        raise SkillImportError(f"文件不存在：{src_file}")
    if not src_file.is_file():
        raise SkillImportError(f"路径不是文件：{src_file}")
    if src_file.name.upper() != "SKILL.MD":
        raise SkillImportError(
            f"文件名必须为 SKILL.md（当前：{src_file.name}）。"
        )

    text = src_file.read_text(encoding="utf-8")
    parsed = parse_skill_md(text)
    name: str = parsed["name"]

    src_dir = src_file.parent
    dest_root = Path(project_skills_dir).expanduser().resolve()
    dest_dir = dest_root / name

    if dest_dir.exists() and not overwrite:
        raise SkillImportError(
            f"技能 `{name}` 目录已存在：{dest_dir}。"
            "如需覆盖，请使用 overwrite=true。"
        )

    # 收集同目录下的文件（递归，排除忽略项）
    files_to_copy = _collect_directory_files(src_dir)

    # 确保 SKILL.md 在列表中
    skill_md_rel = src_file.relative_to(src_dir)
    if skill_md_rel not in [Path(f) for f in files_to_copy]:
        files_to_copy.insert(0, str(skill_md_rel))

    # 执行复制
    dest_dir.mkdir(parents=True, exist_ok=True)
    copied: list[str] = []
    for rel_path_str in files_to_copy:
        src = src_dir / rel_path_str
        dst = dest_dir / rel_path_str
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(src), str(dst))
        copied.append(rel_path_str)

    logger.info(
        "从本地路径导入 Skill `%s`，共 %d 个文件 → %s",
        name, len(copied), dest_dir,
    )

    return SkillImportResult(
        name=name,
        description=parsed.get("description", ""),
        source_type="local_path",
        files_copied=copied,
        dest_dir=str(dest_dir),
    )


async def import_from_github_url(
    url: str,
    project_skills_dir: str,
    *,
    overwrite: bool = False,
) -> SkillImportResult:
    """从 GitHub URL 导入 SKILL.md 及同目录附属文件。

    支持格式：
      - github.com/:owner/:repo/blob/:ref/:path/SKILL.md
      - raw.githubusercontent.com/:owner/:repo/:ref/:path/SKILL.md

    Args:
        url: GitHub URL。
        project_skills_dir: project 层技能目录。
        overwrite: 是否覆盖。

    Returns:
        SkillImportResult 描述导入结果。
    """
    owner, repo, ref, dir_path, filename = _parse_github_url(url)

    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
        # 1. 获取 SKILL.md 内容
        raw_url = (
            f"https://raw.githubusercontent.com/{owner}/{repo}/{ref}/"
            f"{dir_path}/{filename}" if dir_path else
            f"https://raw.githubusercontent.com/{owner}/{repo}/{ref}/{filename}"
        )
        resp = await client.get(raw_url)
        if resp.status_code != 200:
            raise SkillImportError(
                f"无法获取 SKILL.md（HTTP {resp.status_code}）：{raw_url}"
            )
        skill_text = resp.text
        parsed = parse_skill_md(skill_text)
        name: str = parsed["name"]

        # 2. 通过 GitHub Contents API 获取同目录文件列表
        api_dir = dir_path if dir_path else ""
        contents_url = (
            f"https://api.github.com/repos/{owner}/{repo}/contents/{api_dir}"
            f"?ref={ref}"
        )
        sibling_files: list[dict[str, str]] = []
        try:
            dir_resp = await client.get(
                contents_url,
                headers={"Accept": "application/vnd.github.v3+json"},
            )
            if dir_resp.status_code == 200:
                items = dir_resp.json()
                if isinstance(items, list):
                    sibling_files = await _fetch_github_tree_recursive(
                        client, owner, repo, ref, items, base_path="",
                    )
        except Exception as exc:
            logger.warning("获取 GitHub 目录内容失败（非致命）：%s", exc)

    # 3. 写入本地
    dest_root = Path(project_skills_dir).expanduser().resolve()
    dest_dir = dest_root / name

    if dest_dir.exists() and not overwrite:
        raise SkillImportError(
            f"技能 `{name}` 目录已存在：{dest_dir}。"
            "如需覆盖，请使用 overwrite=true。"
        )

    dest_dir.mkdir(parents=True, exist_ok=True)
    copied: list[str] = []

    # 写 SKILL.md
    skill_md_dest = dest_dir / "SKILL.md"
    skill_md_dest.write_text(skill_text, encoding="utf-8")
    copied.append("SKILL.md")

    # 写附属文件
    for item in sibling_files:
        rel = item["path"]
        content = item["content"]
        if rel.upper() == "SKILL.MD":
            continue
        dest_file = dest_dir / rel
        dest_file.parent.mkdir(parents=True, exist_ok=True)
        dest_file.write_text(content, encoding="utf-8")
        copied.append(rel)

    logger.info(
        "从 GitHub 导入 Skill `%s`，共 %d 个文件 → %s",
        name, len(copied), dest_dir,
    )

    return SkillImportResult(
        name=name,
        description=parsed.get("description", ""),
        source_type="github_url",
        files_copied=copied,
        dest_dir=str(dest_dir),
    )


def preview_skill_md(text: str) -> dict[str, Any]:
    """预览解析 SKILL.md 文本，返回结构化信息（不写盘）。"""
    parsed = parse_skill_md(text)
    return {
        "name": parsed.get("name", ""),
        "description": parsed.get("description", ""),
        "instructions_preview": (parsed.get("instructions", "") or "")[:500],
        "has_resources": bool(parsed.get("resources")),
        "resources": parsed.get("resources", []),
        "version": parsed.get("version", "1.0.0"),
        "frontmatter_keys": sorted(
            k for k in parsed.keys() if k != "instructions"
        ),
    }


# ── 内部辅助 ─────────────────────────────────────────────


def _collect_directory_files(directory: Path) -> list[str]:
    """递归收集目录下所有文件的相对路径，跳过忽略项。"""
    results: list[str] = []
    for item in sorted(directory.rglob("*")):
        if not item.is_file():
            continue
        # 跳过隐藏文件和忽略项
        parts = item.relative_to(directory).parts
        if any(p.startswith(".") or p in _IGNORED_NAMES for p in parts):
            continue
        results.append(str(item.relative_to(directory)))
    return results


def _parse_github_url(url: str) -> tuple[str, str, str, str, str]:
    """解析 GitHub URL，返回 (owner, repo, ref, dir_path, filename)。

    支持：
      - github.com/:owner/:repo/blob/:ref/:path
      - raw.githubusercontent.com/:owner/:repo/:ref/:path
    """
    url = url.strip()

    # 尝试 github.com blob URL
    m = _GITHUB_BLOB_RE.match(url)
    if m:
        owner = m.group("owner")
        repo = m.group("repo")
        ref = m.group("ref")
        full_path = m.group("path")
        parts = full_path.rsplit("/", 1)
        if len(parts) == 2:
            dir_path, filename = parts
        else:
            dir_path, filename = "", parts[0]
        return owner, repo, ref, dir_path, filename

    # 尝试 raw.githubusercontent.com URL
    parsed = urlparse(url)
    if parsed.hostname == "raw.githubusercontent.com":
        # 路径格式: /:owner/:repo/:ref/:file_path
        segments = parsed.path.strip("/").split("/", 3)
        if len(segments) < 4:
            raise SkillImportError(f"无法解析 raw GitHub URL：{url}")
        owner, repo, ref, full_path = segments
        parts = full_path.rsplit("/", 1)
        if len(parts) == 2:
            dir_path, filename = parts
        else:
            dir_path, filename = "", parts[0]
        return owner, repo, ref, dir_path, filename

    raise SkillImportError(
        f"不支持的 URL 格式。请提供 github.com/.../blob/.../SKILL.md 格式的链接：{url}"
    )


async def _fetch_github_tree_recursive(
    client: httpx.AsyncClient,
    owner: str,
    repo: str,
    ref: str,
    items: list[dict],
    base_path: str,
    *,
    max_files: int = 50,
    max_file_size: int = 512 * 1024,
) -> list[dict[str, str]]:
    """递归获取 GitHub 目录下的所有文件内容。

    Returns:
        list of {"path": relative_path, "content": text_content}
    """
    results: list[dict[str, str]] = []

    for item in items:
        if len(results) >= max_files:
            break
        item_name = item.get("name", "")
        item_type = item.get("type", "")

        # 跳过忽略项
        if item_name.startswith(".") or item_name in _IGNORED_NAMES:
            continue

        rel_path = f"{base_path}/{item_name}" if base_path else item_name

        if item_type == "file":
            size = item.get("size", 0)
            if size > max_file_size:
                logger.warning("跳过大文件 %s（%d bytes）", rel_path, size)
                continue
            download_url = item.get("download_url", "")
            if not download_url:
                continue
            try:
                file_resp = await client.get(download_url)
                if file_resp.status_code == 200:
                    results.append({
                        "path": rel_path,
                        "content": file_resp.text,
                    })
            except Exception as exc:
                logger.warning("下载文件 %s 失败：%s", rel_path, exc)

        elif item_type == "dir":
            # 递归获取子目录
            sub_url = item.get("url", "")
            if not sub_url:
                continue
            try:
                sub_resp = await client.get(
                    sub_url,
                    headers={"Accept": "application/vnd.github.v3+json"},
                )
                if sub_resp.status_code == 200:
                    sub_items = sub_resp.json()
                    if isinstance(sub_items, list):
                        sub_results = await _fetch_github_tree_recursive(
                            client, owner, repo, ref,
                            sub_items, rel_path,
                            max_files=max_files - len(results),
                            max_file_size=max_file_size,
                        )
                        results.extend(sub_results)
            except Exception as exc:
                logger.warning("获取子目录 %s 失败：%s", rel_path, exc)

    return results
