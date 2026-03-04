"""ClawHub Registry 客户端：搜索、下载、版本解析。

支持混合路径：
- 优先使用 clawhub CLI（如果可用）
- 降级到 HTTP API 直连
"""

from __future__ import annotations

import asyncio
import io
import shutil
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from excelmanus.logger import get_logger

logger = get_logger("skillpacks.clawhub")

# ClawHub V1 API 路由
_API_SEARCH = "/api/v1/search"
_API_SKILLS = "/api/v1/skills"
_API_RESOLVE = "/api/v1/resolve"
_API_DOWNLOAD = "/api/v1/download"

_DEFAULT_REGISTRY = "https://clawhub.ai"
_HTTP_TIMEOUT = 30.0
_SEARCH_LIMIT_DEFAULT = 15
_SEARCH_LIMIT_MAX = 50


# ── 数据模型 ─────────────────────────────────────────────


@dataclass(frozen=True)
class ClawHubSearchResult:
    """单条搜索结果。"""

    slug: str
    display_name: str
    summary: str
    version: str | None
    score: float
    updated_at: int | None = None


@dataclass(frozen=True)
class ClawHubSkillDetail:
    """技能详情。"""

    slug: str
    display_name: str
    summary: str
    tags: list[str]
    created_at: int
    updated_at: int
    latest_version: str | None = None
    latest_changelog: str = ""
    owner_handle: str | None = None
    owner_display_name: str | None = None
    stats: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ClawHubVersionInfo:
    """版本解析结果。"""

    match_version: str | None
    latest_version: str | None


@dataclass(frozen=True)
class ClawHubUpdateInfo:
    """单个技能的更新信息。"""

    slug: str
    installed_version: str | None
    latest_version: str | None
    update_available: bool


# ── 异常 ─────────────────────────────────────────────────


class ClawHubError(Exception):
    """ClawHub 操作失败。"""


class ClawHubNetworkError(ClawHubError):
    """网络请求失败。"""


class ClawHubNotFoundError(ClawHubError):
    """技能不存在。"""


# ── CLI 检测 ─────────────────────────────────────────────


def _detect_clawhub_cli() -> str | None:
    """检测 clawhub CLI 是否可用，返回路径或 None。"""
    return shutil.which("clawhub")


def _cli_available() -> bool:
    return _detect_clawhub_cli() is not None


# ── HTTP 客户端 ──────────────────────────────────────────


class ClawHubClient:
    """ClawHub Registry HTTP 客户端。"""

    def __init__(
        self,
        registry_url: str = _DEFAULT_REGISTRY,
        *,
        prefer_cli: bool = True,
        timeout: float = _HTTP_TIMEOUT,
    ) -> None:
        self._registry_url = registry_url.rstrip("/")
        self._prefer_cli = prefer_cli
        self._timeout = timeout
        self._cli_path: str | None | bool = None  # None=未检测, False=不可用
        self._http_client: httpx.AsyncClient | None = None

    def _ensure_client(self) -> httpx.AsyncClient:
        """懒初始化共享 HTTP 连接池。"""
        if self._http_client is None or self._http_client.is_closed:
            self._http_client = httpx.AsyncClient(timeout=self._timeout)
        return self._http_client

    async def close(self) -> None:
        """关闭共享 HTTP 连接池。"""
        if self._http_client is not None and not self._http_client.is_closed:
            await self._http_client.aclose()
            self._http_client = None

    @property
    def registry_url(self) -> str:
        return self._registry_url

    def _get_cli_path(self) -> str | None:
        if self._cli_path is None:
            path = _detect_clawhub_cli()
            self._cli_path = path if path else False
        return self._cli_path if self._cli_path else None

    def _should_use_cli(self) -> bool:
        return self._prefer_cli and self._get_cli_path() is not None

    # ── 搜索 ──────────────────────────────────────────

    async def search(
        self,
        query: str,
        *,
        limit: int = _SEARCH_LIMIT_DEFAULT,
    ) -> list[ClawHubSearchResult]:
        """搜索 ClawHub 技能。"""
        limit = min(max(limit, 1), _SEARCH_LIMIT_MAX)

        if self._should_use_cli():
            try:
                return await self._search_via_cli(query, limit)
            except Exception:
                logger.debug("CLI 搜索失败，降级到 API", exc_info=True)

        return await self._search_via_api(query, limit)

    async def _search_via_api(
        self, query: str, limit: int
    ) -> list[ClawHubSearchResult]:
        url = f"{self._registry_url}{_API_SEARCH}"
        params = {"q": query, "limit": str(limit)}
        data = await self._http_get(url, params=params)
        results: list[ClawHubSearchResult] = []
        for item in data.get("results", []):
            results.append(
                ClawHubSearchResult(
                    slug=item.get("slug", ""),
                    display_name=item.get("displayName", ""),
                    summary=item.get("summary", "") or "",
                    version=item.get("version"),
                    score=item.get("score", 0),
                    updated_at=item.get("updatedAt"),
                )
            )
        return results

    async def _search_via_cli(
        self, query: str, limit: int
    ) -> list[ClawHubSearchResult]:
        cli = self._get_cli_path()
        if not cli:
            raise ClawHubError("CLI 不可用")
        args = [cli, "search", query, "--limit", str(limit)]
        if self._registry_url != _DEFAULT_REGISTRY:
            args.extend(["--registry", self._registry_url])
        stdout = await self._run_cli(args)
        # CLI 输出格式为每行一个 slug（简单解析）
        results: list[ClawHubSearchResult] = []
        for line in stdout.strip().splitlines():
            line = line.strip()
            if not line or line.startswith("─") or line.startswith("No "):
                continue
            # CLI 输出格式: "slug  display_name  version"
            parts = line.split(None, 2)
            if parts:
                results.append(
                    ClawHubSearchResult(
                        slug=parts[0],
                        display_name=parts[1] if len(parts) > 1 else parts[0],
                        summary=parts[2] if len(parts) > 2 else "",
                        version=None,
                        score=0,
                    )
                )
        return results

    # ── 技能详情 ──────────────────────────────────────

    async def get_skill(self, slug: str) -> ClawHubSkillDetail:
        """获取技能详情。"""
        url = f"{self._registry_url}{_API_SKILLS}/{slug}"
        data = await self._http_get(url)
        skill = data.get("skill")
        if not skill:
            raise ClawHubNotFoundError(f"技能 `{slug}` 不存在")
        latest = data.get("latestVersion") or {}
        owner = data.get("owner") or {}
        tags_raw = skill.get("tags", [])
        tags = tags_raw if isinstance(tags_raw, list) else []
        return ClawHubSkillDetail(
            slug=skill.get("slug", slug),
            display_name=skill.get("displayName", slug),
            summary=skill.get("summary", "") or "",
            tags=tags,
            created_at=skill.get("createdAt", 0),
            updated_at=skill.get("updatedAt", 0),
            latest_version=latest.get("version"),
            latest_changelog=latest.get("changelog", ""),
            owner_handle=owner.get("handle"),
            owner_display_name=owner.get("displayName"),
            stats=skill.get("stats", {}),
        )

    # ── 版本解析 ──────────────────────────────────────

    async def resolve_version(
        self, slug: str, version: str | None = None
    ) -> ClawHubVersionInfo:
        """解析技能版本。"""
        url = f"{self._registry_url}{_API_RESOLVE}"
        body: dict[str, str] = {"slug": slug}
        if version:
            body["version"] = version
        data = await self._http_post(url, json_body=body)
        match = data.get("match")
        latest = data.get("latestVersion")
        return ClawHubVersionInfo(
            match_version=match.get("version") if match else None,
            latest_version=latest.get("version") if latest else None,
        )

    # ── 版本并行解析 ──────────────────────────────────

    async def _resolve_version_parallel(self, slug: str) -> str:
        """并行解析版本：resolve_version 和 get_skill 同时发起，取先到的有效结果。"""
        logger.info("[clawhub] 并行解析版本 slug=%s ...", slug)

        async def _via_resolve() -> str | None:
            try:
                info = await self.resolve_version(slug)
                return info.latest_version
            except ClawHubError as exc:
                logger.debug("[clawhub] resolve_version 失败：%s", exc)
                return None

        async def _via_detail() -> str | None:
            try:
                detail = await self.get_skill(slug)
                return detail.latest_version
            except ClawHubError as exc:
                logger.debug("[clawhub] get_skill 失败：%s", exc)
                return None

        results = await asyncio.gather(_via_resolve(), _via_detail())
        version = results[0] or results[1]
        if not version:
            raise ClawHubNotFoundError(f"技能 `{slug}` 无可用版本")
        logger.info("[clawhub] 并行解析完成 → %s", version)
        return version

    # ── 下载并解压 ────────────────────────────────────

    async def download_and_extract(
        self,
        slug: str,
        dest_dir: Path,
        *,
        version: str | None = None,
        overwrite: bool = False,
    ) -> tuple[str, list[str]]:
        """下载技能 bundle 并解压到目标目录。

        Returns:
            (resolved_version, list_of_extracted_files)
        """
        if self._should_use_cli():
            try:
                return await self._install_via_cli(slug, dest_dir, version, overwrite)
            except Exception:
                logger.debug("CLI 安装失败，降级到 API", exc_info=True)

        return await self._download_via_api(slug, dest_dir, version, overwrite)

    async def _download_via_api(
        self,
        slug: str,
        dest_dir: Path,
        version: str | None,
        overwrite: bool,
    ) -> tuple[str, list[str]]:
        # 并行解析版本：resolve_version 和 get_skill 同时发起
        if not version:
            version = await self._resolve_version_parallel(slug)

        skill_dir = dest_dir / slug
        if skill_dir.exists() and not overwrite:
            raise ClawHubError(
                f"技能 `{slug}` 目录已存在：{skill_dir}。"
                "如需覆盖，请使用 overwrite=true。"
            )

        # 下载 zip
        url = f"{self._registry_url}{_API_DOWNLOAD}"
        params = {"slug": slug, "version": version}
        logger.info("[clawhub] 下载 %s v%s from %s ...", slug, version, url)
        zip_bytes = await self._http_get_bytes(url, params=params)
        logger.info("[clawhub] 下载完成，%d bytes", len(zip_bytes))

        # 解压
        skill_dir.mkdir(parents=True, exist_ok=True)
        extracted: list[str] = []
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            for member in zf.infolist():
                if member.is_dir():
                    continue
                # 安全检查：防止路径穿越
                name = member.filename
                if name.startswith("/") or ".." in name:
                    logger.warning("跳过不安全路径：%s", name)
                    continue
                # 去掉可能的顶层目录前缀
                parts = name.split("/", 1)
                rel_path = parts[1] if len(parts) > 1 and parts[0] == slug else name
                target = skill_dir / rel_path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(zf.read(member))
                extracted.append(rel_path)

        logger.info(
            "从 ClawHub 安装 `%s` v%s，共 %d 个文件 → %s",
            slug, version, len(extracted), skill_dir,
        )
        return version, extracted

    async def _install_via_cli(
        self,
        slug: str,
        dest_dir: Path,
        version: str | None,
        overwrite: bool,
    ) -> tuple[str, list[str]]:
        cli = self._get_cli_path()
        if not cli:
            raise ClawHubError("CLI 不可用")
        args = [cli, "install", slug, "--dir", str(dest_dir)]
        if version:
            args.extend(["--version", version])
        if overwrite:
            args.append("--force")
        if self._registry_url != _DEFAULT_REGISTRY:
            args.extend(["--registry", self._registry_url])
        await self._run_cli(args)

        # 解析实际安装的版本
        resolved_version = version or "latest"
        skill_dir = dest_dir / slug
        extracted: list[str] = []
        if skill_dir.exists():
            for f in skill_dir.rglob("*"):
                if f.is_file():
                    extracted.append(str(f.relative_to(skill_dir)))
        return resolved_version, extracted

    # ── 批量检查更新 ─────────────────────────────────

    async def check_updates(
        self,
        installed: dict[str, str | None],
    ) -> list[ClawHubUpdateInfo]:
        """批量检查已安装技能的更新。

        Args:
            installed: {slug: installed_version_or_None}

        Returns:
            每个技能的更新信息列表。
        """
        results: list[ClawHubUpdateInfo] = []

        async def _check_one(slug: str, current: str | None) -> ClawHubUpdateInfo:
            try:
                info = await self.resolve_version(slug, current)
                latest = info.latest_version
                has_update = bool(
                    latest and current and latest != current
                )
                return ClawHubUpdateInfo(
                    slug=slug,
                    installed_version=current,
                    latest_version=latest,
                    update_available=has_update,
                )
            except Exception as exc:
                logger.warning("检查 `%s` 更新失败：%s", slug, exc)
                return ClawHubUpdateInfo(
                    slug=slug,
                    installed_version=current,
                    latest_version=None,
                    update_available=False,
                )

        tasks = [_check_one(slug, ver) for slug, ver in installed.items()]
        results = await asyncio.gather(*tasks)
        return list(results)

    # ── HTTP 辅助 ─────────────────────────────────────

    @staticmethod
    def _extract_error_detail(resp: httpx.Response) -> str:
        """从 HTTP 响应中提取错误详情片段。"""
        try:
            body = resp.text[:300]
        except Exception:
            body = ""
        return f" — {body}" if body else ""

    async def _http_get(
        self,
        url: str,
        *,
        params: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        try:
            client = self._ensure_client()
            resp = await client.get(url, params=params)
            if resp.status_code == 404:
                raise ClawHubNotFoundError(f"资源不存在：{url}")
            if resp.status_code != 200:
                raise ClawHubNetworkError(
                    f"HTTP {resp.status_code}：{url}{self._extract_error_detail(resp)}"
                )
            return resp.json()
        except (httpx.TimeoutException, httpx.ConnectError) as exc:
            raise ClawHubNetworkError(f"网络请求失败：{exc}") from exc

    async def _http_post(
        self,
        url: str,
        *,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        try:
            client = self._ensure_client()
            resp = await client.post(url, json=json_body)
            if resp.status_code == 404:
                raise ClawHubNotFoundError(f"资源不存在：{url}")
            if resp.status_code not in (200, 201):
                raise ClawHubNetworkError(
                    f"HTTP {resp.status_code}：{url}{self._extract_error_detail(resp)}"
                )
            return resp.json()
        except (httpx.TimeoutException, httpx.ConnectError) as exc:
            raise ClawHubNetworkError(f"网络请求失败：{exc}") from exc

    async def _http_get_bytes(
        self,
        url: str,
        *,
        params: dict[str, str] | None = None,
    ) -> bytes:
        try:
            client = self._ensure_client()
            resp = await client.get(url, params=params)
            if resp.status_code == 404:
                raise ClawHubNotFoundError(f"资源不存在：{url}")
            if resp.status_code != 200:
                raise ClawHubNetworkError(
                    f"HTTP {resp.status_code}：{url}{self._extract_error_detail(resp)}"
                )
            return resp.content
        except (httpx.TimeoutException, httpx.ConnectError) as exc:
            raise ClawHubNetworkError(f"网络请求失败：{exc}") from exc

    # ── CLI 辅助 ──────────────────────────────────────

    async def _run_cli(self, args: list[str]) -> str:
        """异步运行 CLI 命令并返回 stdout。"""
        logger.debug("运行 CLI：%s", " ".join(args))
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_bytes, stderr_bytes = await proc.communicate()
        stdout = stdout_bytes.decode("utf-8", errors="replace")
        stderr = stderr_bytes.decode("utf-8", errors="replace")
        if proc.returncode != 0:
            raise ClawHubError(
                f"clawhub CLI 退出码 {proc.returncode}：{stderr or stdout}"
            )
        return stdout
