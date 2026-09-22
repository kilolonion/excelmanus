"""在已停机的工作树上执行 git ff-only + 依赖 + 前端构建。

禁止在仍在服务请求的进程里调用。
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

from excelmanus.updater import (
    UpdateResult,
    UpgradeOutcome,
    _ensure_github_remote,
    _has_uv,
    _invalidate_version_cache,
    _is_domestic_network,
    _is_git_repo,
    _read_version_from_disk,
    _run_cmd,
    check_for_updates,
    get_current_version,
    verify_database_migration,
)

logger = logging.getLogger(__name__)

ProgressCb = Callable[[str, int], None]


def _fail(result: UpdateResult, outcome: UpgradeOutcome, error: str) -> UpdateResult:
    result.outcome = outcome
    result.error = error
    return result


def _rollback_code_to(project_root: Path, commit: str) -> tuple[bool, str]:
    """Return code to the previous commit without deleting local/user files."""
    if not commit:
        return False, "无更新前 commit"
    rc, _, err = _run_cmd(["git", "reset", "--keep", commit], cwd=project_root)
    return rc == 0, err or ""


def _rollback_error(prefix: str, pre_update_commit: str, rb_ok: bool, rb_err: str) -> str:
    rolled = (
        f"代码已回滚到更新前版本 ({pre_update_commit[:8]})。"
        if rb_ok else f"自动回滚也失败 ({rb_err})"
    )
    return f"{prefix}\n{rolled}"


def apply_on_stopped_tree(
    project_root: str | Path,
    *,
    skip_deps: bool = False,
    use_mirror: bool = False,
    progress_cb: ProgressCb | None = None,
) -> UpdateResult:
    """拉取并安装更新。调用方必须已经停掉 API / 前端进程。"""
    project_root = Path(project_root)
    result = UpdateResult(
        outcome=UpgradeOutcome.FAILED,
        old_version=get_current_version(project_root),
    )

    def _p(msg: str, pct: int) -> None:
        logger.info("[%d%%] %s", pct, msg)
        if progress_cb:
            progress_cb(msg, pct)

    _p("正在检查更新...", 5)
    vi = check_for_updates(project_root, force=True)
    if vi.check_failed:
        err = vi.error or "检查更新失败：无法获取远程版本"
        _p(err, 100)
        return _fail(result, UpgradeOutcome.CHECK_FAILED, err)
    if not vi.has_update:
        result.outcome = UpgradeOutcome.ALREADY_LATEST
        result.new_version = vi.current
        _p("已是最新版本", 100)
        return result
    _p(f"发现新版本: {vi.current} → {vi.latest} ({vi.commits_behind} 个新提交)", 10)
    result.steps_completed.append("version_check")

    if not _is_git_repo(project_root):
        return _fail(result, UpgradeOutcome.NOT_GIT_REPO, "项目不是 Git 仓库，无法更新")

    _, pre_update_commit, _ = _run_cmd(["git", "rev-parse", "HEAD"], cwd=project_root)
    status_rc, status_out, status_err = _run_cmd(["git", "status", "--porcelain", "--untracked-files=no"], cwd=project_root)
    if status_rc != 0:
        return _fail(result, UpgradeOutcome.PRECHECK_FAILED, f"无法检查本地代码: {status_err}")
    if status_out.strip():
        return _fail(result, UpgradeOutcome.PRECHECK_FAILED, "ExcelManus 源码存在未提交修改，请先处理后再更新。未移动或暂存任何工作区和用户文件。")

    _, branch, _ = _run_cmd(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=project_root)
    branch = branch or "main"

    git_remote = "origin"
    if vi.check_method == "git":
        # check_for_updates 已 fetch；若 origin 当时失败会改用 github
        rc_origin, _, _ = _run_cmd(
            ["git", "rev-parse", "--verify", f"origin/{branch}"], cwd=project_root,
        )
        if rc_origin != 0:
            git_remote = "github"
            _ensure_github_remote(project_root)

    _p("正在拉取最新代码...", 30)
    rc, _, err = _run_cmd(
        ["git", "merge", f"{git_remote}/{branch}", "--ff-only", "--no-overwrite-ignore"], cwd=project_root,
    )
    if rc != 0:
        return _fail(
            result,
            UpgradeOutcome.FF_CONFLICT,
            f"fast-forward 合并失败: {err}\n"
            "本地代码与远程存在冲突，无法自动更新。\n"
            "请手动执行: git pull --rebase 或 git merge 解决冲突后重试。",
        )
    result.steps_completed.append("git_pull")
    _p("代码已更新", 45)

    domestic = use_mirror or _is_domestic_network()
    use_uv = _has_uv()
    if not skip_deps:
        from concurrent.futures import ThreadPoolExecutor

        installer_label = "uv" if use_uv else "pip"
        _p(f"正在并行安装依赖 (安装器: {installer_label})...", 50)

        def _install_backend() -> tuple[bool, str]:
            from excelmanus.updater import _build_pip_cmd

            rc_be, _, err_be = _run_cmd(
                _build_pip_cmd(project_root, domestic, use_uv),
                cwd=project_root, timeout=300,
            )
            if rc_be != 0 and not domestic:
                rc_be, _, err_be = _run_cmd(
                    _build_pip_cmd(project_root, True, use_uv),
                    cwd=project_root, timeout=300,
                )
            return rc_be == 0, err_be

        def _install_frontend() -> tuple[bool, str]:
            web_dir = project_root / "web"
            if not (web_dir.is_dir() and (web_dir / "package.json").exists()):
                return True, ""
            lockfile = web_dir / "package-lock.json"
            npm_cmd = ["npm", "ci"] if lockfile.is_file() else ["npm", "install"]
            npm_args = list(npm_cmd)
            if domestic:
                npm_args.append("--registry=https://registry.npmmirror.com")
            rc_fe, _, err_fe = _run_cmd(npm_args, cwd=web_dir, timeout=300)
            if rc_fe != 0 and not domestic:
                fallback = list(npm_cmd) + ["--registry=https://registry.npmmirror.com"]
                rc_fe, _, err_fe = _run_cmd(fallback, cwd=web_dir, timeout=300)
            return rc_fe == 0, err_fe

        with ThreadPoolExecutor(max_workers=2) as pool:
            fut_be = pool.submit(_install_backend)
            fut_fe = pool.submit(_install_frontend)
            be_ok, be_err = fut_be.result(timeout=600)
            fe_ok, fe_err = fut_fe.result(timeout=600)

        if not be_ok:
            _p("后端依赖安装失败，正在回滚代码...", 55)
            rb_ok, rb_err = _rollback_code_to(project_root, pre_update_commit)
            return _fail(
                result,
                UpgradeOutcome.DEPS_FAILED,
                _rollback_error(f"后端依赖更新失败: {be_err[-200:]}", pre_update_commit, rb_ok, rb_err),
            )
        result.steps_completed.append("pip_install")
        _p("后端依赖已更新", 65)
        if not fe_ok:
            _p("前端依赖安装失败，正在回滚代码...", 70)
            rb_ok, rb_err = _rollback_code_to(project_root, pre_update_commit)
            fe_msg = fe_err[-200:] if fe_err else "未知错误"
            return _fail(
                result,
                UpgradeOutcome.DEPS_FAILED,
                _rollback_error(f"前端依赖更新失败: {fe_msg}", pre_update_commit, rb_ok, rb_err),
            )
        result.steps_completed.append("npm_install")
        _p("前端依赖已更新", 70)

    web_dir = project_root / "web"
    if web_dir.is_dir() and (web_dir / "package.json").exists():
        _p("正在重新构建前端...", 75)
        rc_build, _, build_err = _run_cmd(
            ["npm", "run", "build"], cwd=web_dir, timeout=600,
        )
        if rc_build != 0:
            _p("默认构建失败，尝试 webpack 兜底...", 78)
            rc_build, _, build_err = _run_cmd(
                ["npm", "run", "build:webpack"], cwd=web_dir, timeout=600,
            )
        if rc_build == 0:
            result.steps_completed.append("frontend_build")
            _p("前端构建完成", 82)
        else:
            _p("前端构建失败，正在回滚代码...", 80)
            rb_ok, rb_err = _rollback_code_to(project_root, pre_update_commit)
            build_msg = build_err[-200:] if build_err else "未知错误"
            return _fail(
                result,
                UpgradeOutcome.BUILD_FAILED,
                _rollback_error(f"前端构建失败: {build_msg}", pre_update_commit, rb_ok, rb_err),
            )

    _p("正在预验证数据库迁移...", 85)
    db_ok, db_msg = verify_database_migration(project_root)
    if not db_ok:
        _p(f"数据库预检失败，正在回滚代码... ({db_msg})", 90)
        rb_ok, rb_err = _rollback_code_to(project_root, pre_update_commit)
        return _fail(
            result,
            UpgradeOutcome.PRECHECK_FAILED,
            _rollback_error(f"数据库预检失败: {db_msg}", pre_update_commit, rb_ok, rb_err),
        )
    result.steps_completed.append("db_migration_verified")
    _p(f"数据库迁移验证通过: {db_msg}", 90)

    result.new_version = _read_version_from_disk(project_root)
    result.outcome = UpgradeOutcome.SUCCESS
    result.needs_restart = True
    result.steps_completed.append("verified")
    _invalidate_version_cache()
    _p(f"更新成功！{result.old_version} → {result.new_version}", 100)
    return result
