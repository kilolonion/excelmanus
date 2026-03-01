"""ClawHub lockfile 管理：兼容 clawhub CLI 的 .clawhub/lock.json 格式。

格式：
{
  "version": 1,
  "skills": {
    "<slug>": {
      "version": "1.0.0",
      "installedAt": 1709000000
    }
  }
}
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from excelmanus.logger import get_logger

logger = get_logger("skillpacks.clawhub_lockfile")

_LOCKFILE_VERSION = 1


def _default_lockfile() -> dict[str, Any]:
    return {"version": _LOCKFILE_VERSION, "skills": {}}


class ClawHubLockfile:
    """管理 .clawhub/lock.json。"""

    def __init__(self, workspace_root: str | Path) -> None:
        self._workspace_root = Path(workspace_root).expanduser().resolve()
        self._lockfile_path = self._workspace_root / ".clawhub" / "lock.json"

    @property
    def path(self) -> Path:
        return self._lockfile_path

    def read(self) -> dict[str, Any]:
        """读取 lockfile，不存在则返回空结构。"""
        if not self._lockfile_path.exists():
            return _default_lockfile()
        try:
            text = self._lockfile_path.read_text(encoding="utf-8")
            data = json.loads(text)
            if not isinstance(data, dict) or data.get("version") != _LOCKFILE_VERSION:
                logger.warning("lockfile 版本不匹配，重置")
                return _default_lockfile()
            if "skills" not in data or not isinstance(data["skills"], dict):
                data["skills"] = {}
            return data
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("读取 lockfile 失败：%s", exc)
            return _default_lockfile()

    def write(self, data: dict[str, Any]) -> None:
        """写入 lockfile。"""
        self._lockfile_path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
        self._lockfile_path.write_text(text, encoding="utf-8")

    def get_installed(self) -> dict[str, str | None]:
        """返回已安装技能映射 {slug: version_or_None}。"""
        data = self.read()
        result: dict[str, str | None] = {}
        for slug, info in data.get("skills", {}).items():
            if isinstance(info, dict):
                result[slug] = info.get("version")
            else:
                result[slug] = None
        return result

    def add(self, slug: str, version: str | None) -> None:
        """记录安装。"""
        data = self.read()
        data["skills"][slug] = {
            "version": version,
            "installedAt": int(time.time()),
        }
        self.write(data)

    def remove(self, slug: str) -> bool:
        """移除记录，返回是否存在。"""
        data = self.read()
        if slug in data.get("skills", {}):
            del data["skills"][slug]
            self.write(data)
            return True
        return False

    def update_version(self, slug: str, version: str) -> None:
        """更新已安装技能的版本。"""
        data = self.read()
        skills = data.get("skills", {})
        if slug in skills:
            skills[slug]["version"] = version
            skills[slug]["installedAt"] = int(time.time())
        else:
            skills[slug] = {
                "version": version,
                "installedAt": int(time.time()),
            }
        self.write(data)

    def has(self, slug: str) -> bool:
        """检查是否已安装。"""
        data = self.read()
        return slug in data.get("skills", {})
