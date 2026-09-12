"""python -m excelmanus.upgrade

无参数：helper 模式（读取 upgrade-request.json，停机更新再拉起）。
--offline：CLI 更新，服务在跑则拒绝。
--check：只检查是否有更新。
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("excelmanus.upgrade")


def _project_root(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit).expanduser().resolve()
    return Path(__file__).resolve().parents[2]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="excelmanus.upgrade")
    parser.add_argument("--project-root", default="")
    parser.add_argument("--offline", action="store_true", help="CLI 停机更新（服务在跑则退出）")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--skip-backup", action="store_true")
    parser.add_argument("--skip-deps", action="store_true")
    parser.add_argument("--mirror", action="store_true")
    parser.add_argument("--yes", "-y", action="store_true")
    parser.add_argument("--restore", default="", help="从备份名恢复（需服务已停止）")
    parser.add_argument("--list-backups", action="store_true")
    args = parser.parse_args(argv)

    root = _project_root(args.project_root or None)

    if args.list_backups:
        from excelmanus.updater import list_backups

        backups = list_backups(root)
        if not backups:
            print("暂无备份")
            return 0
        for item in backups:
            print(f"{item['name']}\t{item.get('version', '')}\t{item.get('size_mb', 0)}MB")
        return 0

    if args.check:
        from excelmanus.updater import check_for_updates, get_current_version

        info = check_for_updates(root, force=True)
        current = get_current_version(root)
        if info.has_update:
            print(f"可更新: {current} → {info.latest}（落后 {info.commits_behind} 个提交）")
            return 0
        if info.check_failed:
            print("检查更新失败")
            return 1
        print(f"已是最新版本 ({current})")
        return 0

    from excelmanus.upgrade.helper import api_is_running, run_helper, stop_supervised
    from excelmanus.upgrade.runtime import read_runtime, write_request

    if args.restore:
        if api_is_running():
            print("服务仍在运行。请先停止，或在设置页执行恢复（会自动停机）。", file=sys.stderr)
            return 2
        write_request({"action": "restore", "backup_name": args.restore})
        return run_helper(root, skip_stop=True, skip_start=True)

    if args.offline:
        if api_is_running():
            print(
                "检测到 API 仍在监听。请先停止服务，或在设置页「执行更新」。",
                file=sys.stderr,
            )
            return 2
        if not args.yes:
            ans = input("开始停机更新？[Y/n] ").strip()
            if ans.lower().startswith("n"):
                print("已取消")
                return 0
        runtime = read_runtime()
        if runtime:
            stop_supervised(runtime)
        write_request({
            "action": "upgrade",
            "skip_backup": args.skip_backup,
            "skip_deps": args.skip_deps,
            "use_mirror": args.mirror,
        })
        return run_helper(root, skip_stop=True, skip_start=True)

    return run_helper(root)


if __name__ == "__main__":
    raise SystemExit(main())
