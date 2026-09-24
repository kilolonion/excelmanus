"""Release discovery must be fast, read-only, stable-only and proxy tolerant."""
import json
import threading
import urllib.error
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock

import pytest

from excelmanus import release_check as releases


def response(data=None, url=None):
    result = MagicMock()
    result.__enter__.return_value = result
    result.read.return_value = json.dumps(data).encode()
    result.geturl.return_value = url
    return result


def published(tag="v1.9.0", **extra):
    return {"tag_name": tag, "html_url": f"{releases.RELEASES_URL}/tag/{tag}", "body": "Changes", **extra}


@pytest.fixture
def setup_release(tmp_path, monkeypatch):
    root = tmp_path / "source"
    root.mkdir()
    (root / "pyproject.toml").write_text('[project]\nversion = "1.8.1"\n')
    (root / ".git").mkdir()
    with releases._lock:
        releases._cache.clear()
    opener = MagicMock()
    monkeypatch.setattr(releases.urllib.request, "build_opener", lambda *args: opener)
    monkeypatch.setattr("excelmanus.updater._run_cmd", MagicMock(side_effect=AssertionError("Release checks must not run Git")))
    return root, opener


def test_git_checkout_uses_release_api_without_fetching(setup_release):
    root, opener = setup_release
    opener.open.return_value = response(published())
    info = releases.check_release_updates(root)
    assert info.has_update and info.latest == "1.9.0"
    assert info.release_notes == "Changes"
    assert info.release_url.endswith("/tag/v1.9.0")
    assert info.check_method == "github_release_api"
    assert opener.open.call_args.args[0].full_url == releases.RELEASE_API
    assert opener.open.call_args.kwargs["timeout"] == 4
    info.latest = "mutated"
    assert releases.check_release_updates(root).latest == "1.9.0"
    assert opener.open.call_count == 1
    releases.check_release_updates(root, force=True)
    assert opener.open.call_count == 2


@pytest.mark.parametrize("failure", [TimeoutError(), urllib.error.URLError("offline"),
    urllib.error.HTTPError(releases.RELEASE_API, 429, "limited", {}, None),
    urllib.error.HTTPError(releases.RELEASE_API, 503, "unavailable", {}, None)])
def test_transport_failures_fall_back_to_public_release(setup_release, failure):
    root, opener = setup_release
    opener.open.side_effect = [failure, response(url=f"{releases.RELEASES_URL}/tag/v1.9.0")]
    info = releases.check_release_updates(root)
    assert not info.check_failed and info.has_update
    assert info.check_method == "github_release_page"
    assert opener.open.call_count == 2


@pytest.mark.parametrize("tag", ["v1.7.1", "v1.8.1", "v1.10.0"])
def test_numeric_release_comparison_without_downgrades(setup_release, tag):
    root, opener = setup_release
    opener.open.return_value = response(published(tag))
    assert releases.check_release_updates(root).has_update is (tag == "v1.10.0")


@pytest.mark.parametrize("data", [published("v1.9.0-beta.1"), published(draft=True),
    published(prerelease=True), published(html_url="https://evil.test/tag/v1.9.0"),
    published(html_url=f"{releases.RELEASES_URL}/tag/v2.0.0"), []])
def test_invalid_api_and_page_never_report_latest(setup_release, data):
    root, opener = setup_release
    opener.open.side_effect = [response(data), response(url=f"{releases.RELEASES_URL}/tag/v1.9.0-rc.1")]
    info = releases.check_release_updates(root)
    assert info.check_failed and not info.has_update
    assert info.error


def test_failed_check_cache_and_force_retry(setup_release, monkeypatch):
    root, opener = setup_release
    now = [100.0]
    monkeypatch.setattr(releases.time, "monotonic", lambda: now[0])
    opener.open.side_effect = TimeoutError()
    info = releases.check_release_updates(root)
    assert info.check_failed and "连接超时" in info.error
    releases.check_release_updates(root)
    assert opener.open.call_count == 2
    now[0] += 31
    releases.check_release_updates(root)
    assert opener.open.call_count == 4
    opener.open.side_effect = None
    opener.open.return_value = response(published())
    assert releases.check_release_updates(root, force=True).has_update


def test_simultaneous_force_checks_share_network_and_cache_is_per_version(setup_release, monkeypatch):
    root, opener = setup_release
    started, release, joined = threading.Event(), threading.Event(), threading.Event()
    original_result = releases.Future.result

    def waiting_result(self, *args, **kwargs):
        joined.set()
        return original_result(self, *args, **kwargs)

    monkeypatch.setattr(releases.Future, "result", waiting_result)

    def fetch(*args, **kwargs):
        started.set()
        assert release.wait(3)
        return response(published())

    opener.open.side_effect = fetch
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(releases.check_release_updates, root, force=True)
        assert started.wait(3)
        second = pool.submit(releases.check_release_updates, root, force=True)
        try:
            assert joined.wait(3)
        finally:
            release.set()
        assert first.result().latest == second.result().latest == "1.9.0"
    assert opener.open.call_count == 1
    (root / "pyproject.toml").write_text('[project]\nversion = "2.0.0"\n')
    assert not releases.check_release_updates(root).has_update
    assert opener.open.call_count == 2


def test_redirect_rejects_non_project_hosts_before_request():
    handler = releases._ReleaseRedirect()
    with pytest.raises(ValueError):
        handler.redirect_request(None, None, 302, "Found", {}, "https://evil.test/release")


@pytest.mark.asyncio
async def test_version_endpoint_returns_release_link_and_force(setup_release, monkeypatch):
    from starlette.requests import Request
    from excelmanus import api_routes_version as routes
    root, opener = setup_release
    monkeypatch.setattr(routes, "_get_project_root", lambda: root)
    opener.open.return_value = response(published())
    request = Request({"type": "http", "query_string": b"force=1"})
    payload = json.loads((await routes.version_check(request)).body)
    assert payload["check_method"] == "github_release_api"
    assert payload["release_url"].endswith("/tag/v1.9.0")
    await routes.version_check(request)
    assert opener.open.call_count == 2
