"""Read-only GitHub Release checks, independent of source-branch upgrades."""

from __future__ import annotations

import json
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import Future
from dataclasses import replace
from pathlib import Path

from excelmanus.updater import VersionInfo, get_current_version

RELEASES_URL = "https://github.com/kilolonion/excelmanus/releases"
RELEASE_API = "https://api.github.com/repos/kilolonion/excelmanus/releases/latest"
_REQUEST_TIMEOUT = 4.0
_SUCCESS_TTL = 300.0
_FAILURE_TTL = 30.0
_lock = threading.Lock()
_cache: dict[tuple[str, str], tuple[float, VersionInfo]] = {}
_pending: dict[tuple[str, str], Future] = {}


def _stable_version(tag: str) -> tuple[int, int, int]:
    match = re.fullmatch(r"v?(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:\+[\w.-]+)?", tag)
    if not match:
        raise ValueError("发布信息没有有效的正式版本号")
    return tuple(map(int, match.groups()))


def _release_tag(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or parsed.netloc != "github.com" or parsed.query or parsed.fragment:
        raise ValueError("发布地址无效")
    prefix = "/kilolonion/excelmanus/releases/tag/"
    if not parsed.path.startswith(prefix):
        raise ValueError("发布地址不属于 ExcelManus")
    tag = urllib.parse.unquote(parsed.path[len(prefix):])
    _stable_version(tag)
    return tag


class _ReleaseRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _release_tag(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _fetch_release() -> tuple[str, str, str, str]:
    # urllib honours proxy environment variables and Windows system proxies.
    opener = urllib.request.build_opener(_ReleaseRedirect())
    headers = {"User-Agent": "ExcelManus-Updater", "Accept": "application/vnd.github+json"}
    errors = []
    try:
        with opener.open(urllib.request.Request(RELEASE_API, headers=headers), timeout=_REQUEST_TIMEOUT) as response:
            payload = response.read(1_000_001)
        if len(payload) > 1_000_000:
            raise ValueError("发布信息过大")
        data = json.loads(payload)
        if not isinstance(data, dict) or data.get("draft") or data.get("prerelease"):
            raise ValueError("发布信息无效")
        tag, url = data.get("tag_name"), data.get("html_url")
        if not isinstance(tag, str) or not isinstance(url, str) or _release_tag(url) != tag:
            raise ValueError("发布版本与地址不一致")
        notes = data.get("body")
        return tag, url, notes[:20_000] if isinstance(notes, str) else "", "github_release_api"
    except Exception as exc:
        errors.append(f"GitHub API：{_error_reason(exc)}")
    try:
        request = urllib.request.Request(f"{RELEASES_URL}/latest", headers={**headers, "Accept": "text/html"})
        with opener.open(request, timeout=_REQUEST_TIMEOUT) as response:
            url = response.geturl()
            tag = _release_tag(url)
        # The redirect identifies GitHub's latest stable release. No need to
        # download the HTML or consult tags (which may never have been released).
        return tag, url, "", "github_release_page"
    except Exception as exc:
        errors.append(f"GitHub 发布页：{_error_reason(exc)}")
    raise RuntimeError("；".join(errors) + "。请检查服务进程的网络或代理设置后重试")


def _error_reason(exc: Exception) -> str:
    if isinstance(exc, urllib.error.HTTPError):
        if exc.code in {403, 429}:
            return f"访问受限或请求频率超限（HTTP {exc.code}）"
        if exc.code == 404:
            return "未找到正式发布（HTTP 404）"
        return f"HTTP {exc.code}"
    if isinstance(exc, TimeoutError) or isinstance(getattr(exc, "reason", None), TimeoutError):
        return "连接超时"
    if isinstance(exc, (ValueError, UnicodeError)):
        return "发布数据无效"
    # Network exception strings can contain proxy credentials; do not expose them.
    return "连接失败"


def check_release_updates(project_root: str | Path | None = None, *, force: bool = False) -> VersionInfo:
    root = Path(project_root or Path(__file__).resolve().parent.parent).resolve()
    current = get_current_version(root)
    key = (str(root), current)
    with _lock:
        cached = _cache.get(key)
        if not force and cached and time.monotonic() - cached[0] < (_FAILURE_TTL if cached[1].check_failed else _SUCCESS_TTL):
            return replace(cached[1])
        future = _pending.get(key)
        owner = future is None
        if owner:
            future = _pending[key] = Future()
    if not owner:
        return replace(future.result())
    info = VersionInfo(current=current, latest=current, check_method="github_release_api", release_url=RELEASES_URL)
    try:
        tag, info.release_url, info.release_notes, info.check_method = _fetch_release()
        info.latest = tag.removeprefix("v")
        latest = _stable_version(tag)
        match = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)(-[\w.-]+)?(?:\+[\w.-]+)?", current)
        if not match:
            raise ValueError("无法比较当前安装的版本号")
        installed = tuple(map(int, match.groups()[:3]))
        info.has_update = latest > installed or (latest == installed and bool(match[4]))
    except Exception as exc:
        info.check_failed = True
        info.error = str(exc)
    finally:
        with _lock:
            now = time.monotonic()
            # Discard expired roots/versions so long-running multi-root callers
            # do not accumulate entries indefinitely.
            for old_key, (saved, _) in list(_cache.items()):
                if now - saved >= _SUCCESS_TTL:
                    del _cache[old_key]
            _cache[key] = (now, info)
            _pending.pop(key, None)
            future.set_result(info)
    return replace(info)
