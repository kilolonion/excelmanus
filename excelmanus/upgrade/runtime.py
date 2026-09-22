"""start 脚本与升级 helper 共享的 runtime / upgrade-request 文件。"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from excelmanus.data_home import get_excelmanus_home

RUNTIME_NAME = "runtime.json"
REQUEST_NAME = "upgrade-request.json"
STATUS_NAME = "upgrade-status.json"


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def reserve_request(data: dict[str, Any]) -> Path:
    """Exclusive reservation: two browsers/processes cannot schedule two helpers."""
    path = request_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump({**data, "requested_at": datetime.now(timezone.utc).isoformat(), "pid": os.getpid()}, stream)
    return path


def runtime_path() -> Path:
    return get_excelmanus_home() / RUNTIME_NAME


def request_path() -> Path:
    return get_excelmanus_home() / REQUEST_NAME


def status_path() -> Path:
    return get_excelmanus_home() / STATUS_NAME


def write_runtime(data: dict[str, Any]) -> Path:
    path = runtime_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        **data,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def read_runtime() -> dict[str, Any] | None:
    path = runtime_path()
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def clear_runtime() -> None:
    path = runtime_path()
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def write_request(data: dict[str, Any]) -> Path:
    path = request_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        **data,
        "requested_at": datetime.now(timezone.utc).isoformat(),
        "pid": os.getpid(),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def read_request() -> dict[str, Any] | None:
    path = request_path()
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def clear_request() -> None:
    path = request_path()
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def write_upgrade_status(data: dict[str, Any]) -> Path:
    path = status_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        **data,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    if isinstance(data.get("ok"), bool):
        payload["finished_at"] = payload["updated_at"]
    _atomic_json(path, payload)
    return path


def read_upgrade_status() -> dict[str, Any] | None:
    path = status_path()
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError):
        return None
