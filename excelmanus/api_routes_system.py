"""上传、@ 提及列表、斜杠命令、健康检查与公网 IP。

从 api.py 抽出的独立路由模块。运行时状态只从 api_app_state 读取，
禁止 ``from excelmanus.api import _config`` 反向导入。
由 api.py 在 create_app 中 include_router 注册。
"""

from __future__ import annotations

import os
import uuid
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

import excelmanus
from excelmanus.api_app_state import (
    error_json_response as _error_json_response,
    get_channel_launcher,
    get_config,
    get_config_incomplete,
    get_database,
    get_draining,
    get_file_registry as _get_file_registry,
    get_restart_reason,
    get_session_manager,
    get_skillpack_loader,
    get_tool_registry,
    is_external_safe_mode as _is_external_safe_mode,
    resolve_workspace as _resolve_workspace,
    resolve_workspace_root as _resolve_workspace_root,
    safe_uploads_path as _safe_uploads_path,
)
from excelmanus.logger import get_logger

logger = get_logger("api.system")

router = APIRouter()

_UPLOAD_MAX_PART_SIZE = 100 * 1024 * 1024  # 100 MB – 覆盖 Starlette 默认的 1 MB 限制


@router.post("/api/v1/upload")
async def upload_file(raw_request: Request) -> JSONResponse:
    """上传文件到 workspace uploads 目录（支持可选 folder 参数指定子目录）。

    注意: 不使用 FastAPI 的 UploadFile 依赖注入，改为手动调用
    ``request.form(max_part_size=...)`` 以突破 Starlette 0.50+ 默认的
    1 MB multipart 大小限制。
    """
    assert get_config() is not None, "服务未初始化"

    form = await raw_request.form(max_part_size=_UPLOAD_MAX_PART_SIZE)
    file = form.get("file")
    logger.info("upload_file: form keys=%s, file type=%s, file=%r", list(form.keys()), type(file).__name__, file)
    if file is None or not hasattr(file, "read"):
        return _error_json_response(400, f"缺少 file 字段 (got {type(file).__name__})")

    filename = file.filename or "unnamed"

    ws = _resolve_workspace(raw_request)

    content = await file.read()

    upload_dir = ws.get_upload_dir()

    # 支持可选的 folder= 表单字段或查询参数
    folder = raw_request.query_params.get("folder", "")
    if not folder:
        folder = str(form.get("folder", ""))

    if folder:
        target_dir = _safe_uploads_path(upload_dir, folder)
        if target_dir is None:
            return _error_json_response(400, "非法目标路径")
        target_dir.mkdir(parents=True, exist_ok=True)
    else:
        target_dir = upload_dir

    safe_name = f"{uuid.uuid4().hex[:8]}_{filename}"
    dest_path = target_dir / safe_name

    with open(dest_path, "wb") as f:
        f.write(content)

    # .xls/.xlsb → 自动转换为 .xlsx，转换成功后删除原始文件节省空间
    converted = False
    original_filename = filename
    _original_dest = dest_path  # 保留引用用于清理
    from excelmanus.xls_converter import needs_conversion, convert_to_xlsx, ConversionError
    if needs_conversion(dest_path):
        try:
            xlsx_path = convert_to_xlsx(dest_path, overwrite=True)
            dest_path = xlsx_path
            filename = xlsx_path.name
            converted = True
            # 清理原始 .xls/.xlsb 文件，避免双倍磁盘占用
            try:
                _original_dest.unlink(missing_ok=True)
            except OSError:
                pass
            logger.info("上传文件自动转换: %s → %s", original_filename, filename)
        except (ConversionError, Exception) as exc:
            logger.warning("上传文件转换失败，保留原始格式: %s (%s)", original_filename, exc)

    rel_path = f"./{dest_path.relative_to(ws.root_dir)}"

    # 注册到 FileRegistry
    registry = _get_file_registry(str(ws.root_dir))
    if registry is not None:
        try:
            entry = registry.register_upload(
                canonical_path=str(dest_path.relative_to(ws.root_dir)),
                original_name=original_filename,
                size_bytes=dest_path.stat().st_size,
            )
            # 转换后添加原始扩展名别名，方便用户用原名引用
            if converted:
                registry.add_alias(entry.id, "original_path", f"./{(target_dir / safe_name).relative_to(ws.root_dir)}")
        except Exception:
            logger.debug("FileRegistry register_upload 失败", exc_info=True)

    resp: dict[str, Any] = {
        "filename": original_filename,
        "path": rel_path,
        "size": dest_path.stat().st_size,
    }
    if converted:
        resp["converted_from"] = original_filename
        resp["converted_to"] = filename
    return JSONResponse(content=resp)


@router.post("/api/v1/upload-from-url")
async def upload_file_from_url(raw_request: Request) -> JSONResponse:
    """从 URL 下载文件并保存到 workspace uploads 目录。

    请求体 JSON::

        {"url": "https://example.com/data.xlsx"}
    """
    assert get_config() is not None, "服务未初始化"

    try:
        body = await raw_request.json()
    except Exception:
        return _error_json_response(400, "请求体必须是 JSON")

    url: str = (body.get("url") or "").strip()
    if not url:
        return _error_json_response(400, "缺少 url 字段")

    # 仅允许 http/https
    if not url.lower().startswith(("http://", "https://")):
        return _error_json_response(400, "仅支持 http/https 链接")

    import httpx
    from urllib.parse import urlparse, unquote

    # 从 URL 路径推断文件名
    parsed = urlparse(url)
    url_path = unquote(parsed.path.rstrip("/"))
    raw_filename = url_path.split("/")[-1] if "/" in url_path else ""
    if not raw_filename or "." not in raw_filename:
        return _error_json_response(400, "无法从 URL 推断文件名（需带扩展名，如 .xlsx/.csv/.png）")

    # 下载文件（限制大小）
    max_download = _UPLOAD_MAX_PART_SIZE  # 复用上传限制
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=60.0) as client:
            resp = await client.get(url)
            resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        return _error_json_response(502, f"远程服务器返回 {exc.response.status_code}")
    except Exception as exc:
        return _error_json_response(502, f"下载失败: {exc}")

    content = resp.content
    if len(content) > max_download:
        return _error_json_response(413, f"文件过大 (>{max_download // (1024*1024)} MB)")
    if len(content) == 0:
        return _error_json_response(400, "下载到空文件")

    ws = _resolve_workspace(raw_request)

    upload_dir = ws.get_upload_dir()
    safe_name = f"{uuid.uuid4().hex[:8]}_{raw_filename}"
    dest_path = upload_dir / safe_name

    with open(dest_path, "wb") as f:
        f.write(content)

    # .xls/.xlsb → 自动转换为 .xlsx，转换成功后删除原始文件节省空间
    converted = False
    original_filename = raw_filename
    _original_dest_url = dest_path
    from excelmanus.xls_converter import needs_conversion as _nc_url, convert_to_xlsx as _conv_url, ConversionError as _CE_url
    if _nc_url(dest_path):
        try:
            xlsx_path = _conv_url(dest_path, overwrite=True)
            dest_path = xlsx_path
            raw_filename = xlsx_path.name
            converted = True
            try:
                _original_dest_url.unlink(missing_ok=True)
            except OSError:
                pass
            logger.info("URL 上传文件自动转换: %s → %s", original_filename, raw_filename)
        except (_CE_url, Exception) as exc:
            logger.warning("URL 上传文件转换失败，保留原始格式: %s (%s)", original_filename, exc)

    rel_path = f"./{dest_path.relative_to(ws.root_dir)}"

    # 注册到 FileRegistry
    registry = _get_file_registry(str(ws.root_dir))
    if registry is not None:
        try:
            entry = registry.register_upload(
                canonical_path=str(dest_path.relative_to(ws.root_dir)),
                original_name=original_filename,
                size_bytes=dest_path.stat().st_size,
            )
            if converted:
                registry.add_alias(entry.id, "original_path", f"./{(upload_dir / safe_name).relative_to(ws.root_dir)}")
        except Exception:
            logger.debug("FileRegistry register_upload 失败 (from-url)", exc_info=True)

    resp: dict[str, Any] = {
        "filename": original_filename,
        "path": rel_path,
        "size": dest_path.stat().st_size,
    }
    if converted:
        resp["converted_from"] = original_filename
        resp["converted_to"] = raw_filename
    return JSONResponse(content=resp)


@router.get("/api/v1/mentions")
async def list_mentions(request: Request, path: str = "") -> JSONResponse:
    """返回 @ 提及可选项。path 参数支持子目录扫描。"""
    tools: list[str] = []
    skills: list[dict] = []
    files: list[str] = []

    _tool_registry = get_tool_registry()
    _skillpack_loader = get_skillpack_loader()
    _config = get_config()

    if _tool_registry is not None:
        tools = sorted(_tool_registry.get_tool_names())
    if _skillpack_loader is not None:
        for name, sp in _skillpack_loader.get_skillpacks().items():
            skills.append({
                "name": name,
                "description": sp.get("description", "") if isinstance(sp, dict) else "",
            })
    safe_path = path.replace("..", "").strip("/")
    if _config is not None:
        ws = _resolve_workspace_root(request)
        scan_dir = os.path.join(ws, safe_path) if safe_path else ws
        if os.path.isdir(scan_dir):
            try:
                for entry in os.scandir(scan_dir):
                    if entry.name.startswith(".") or entry.name in {"node_modules", "__pycache__", ".venv"}:
                        continue
                    rel = f"{safe_path}/{entry.name}" if safe_path else entry.name
                    if entry.is_dir():
                        files.append(rel + "/")
                    elif entry.is_file():
                        files.append(rel)
            except OSError:
                pass

    return JSONResponse(content={
        "tools": tools,
        "skills": [{"name": s["name"], "description": s.get("description", "")} for s in skills],
        "files": sorted(files),
        "path": safe_path if _config is not None else "",
    })


@router.post("/api/v1/command")
async def execute_command(request: Request) -> JSONResponse:
    """执行展示型斜杠命令并返回结果（不走 chat 流）。"""
    body = await request.json()
    command = (body.get("command") or "").strip()
    if not command:
        return _error_json_response(400, "缺少 command 字段")

    _config = get_config()
    _session_manager = get_session_manager()
    _skillpack_loader = get_skillpack_loader()
    _database = get_database()

    # /help → 返回命令列表
    if command == "/help":
        from excelmanus.control_commands import CONTROL_COMMAND_SPECS
        lines = ["### ExcelManus 命令帮助\n"]
        lines.append("| 命令 | 说明 |")
        lines.append("|------|------|")
        base = [
            ("/help", "显示帮助"), ("/skills", "查看技能包"), ("/history", "对话历史摘要"),
            ("/clear", "清除对话历史"), ("/mcp", "MCP Server 状态"), ("/save", "保存对话记录"),
            ("/config", "环境变量配置"),
        ]
        for cmd, desc in base:
            lines.append(f"| `{cmd}` | {desc} |")
        for spec in CONTROL_COMMAND_SPECS:
            args = f" ({', '.join(spec.arguments)})" if spec.arguments else ""
            lines.append(f"| `{spec.command}{args}` | {spec.description} |")
        return JSONResponse(content={"result": "\n".join(lines), "format": "markdown"})

    # /skills → 返回技能包列表
    if command == "/skills":
        if _skillpack_loader is None:
            return JSONResponse(content={"result": "技能包未加载", "format": "text"})
        lines = ["### 已加载技能包\n"]
        for sp in _skillpack_loader.list_skillpacks():
            name = sp.name if hasattr(sp, "name") else str(sp.get("name", ""))
            desc = sp.description if hasattr(sp, "description") else str(sp.get("description", ""))
            source = sp.source if hasattr(sp, "source") else str(sp.get("source", ""))
            lines.append(f"- **{name}** ({source}) — {desc}")
        return JSONResponse(content={"result": "\n".join(lines), "format": "markdown"})

    # /history → 对话历史摘要
    if command == "/history":
        if _session_manager is None:
            return JSONResponse(content={"result": "服务未初始化", "format": "text"})
        try:
            sessions = await _session_manager.list_sessions()
            if not sessions:
                return JSONResponse(content={"result": "暂无活跃会话", "format": "text"})
            lines = ["### 活跃会话\n"]
            for s in sessions:
                status = "🔄" if s.get("in_flight") else "💬"
                lines.append(f"- {status} **{s['title']}** ({s['message_count']} 条消息) `{s['id'][:8]}...`")
            return JSONResponse(content={"result": "\n".join(lines), "format": "markdown"})
        except Exception:
            return JSONResponse(content={"result": "获取会话历史失败", "format": "text"})

    # /mcp → 返回 MCP 状态
    if command == "/mcp":
        if _session_manager is None:
            return JSONResponse(content={"result": "服务未初始化", "format": "text"})
        return JSONResponse(content={"result": "MCP 状态请通过设置面板查看", "format": "text"})

    # /config → 返回配置摘要
    if command in {"/config", "/config list", "/config get"}:
        if _config is None:
            return JSONResponse(content={"result": "配置未加载", "format": "text"})
        lines = ["### 当前配置\n"]
        lines.append(f"- **模型**: `{_config.model}`")
        lines.append(f"- **Base URL**: `{_config.base_url}`")
        lines.append(f"- **工作区**: `{_config.workspace_root}`")
        lines.append(f"- **最大迭代**: {_config.max_iterations}")
        lines.append(f"- **辅助模型**: `{_config.aux_model or '未配置'}`")
        lines.append(f"- **AUX Base URL**: `{_config.aux_base_url or '继承主配置'}`")
        lines.append(f"- **子代理**: {'开启' if _config.subagent_enabled else '关闭'}")
        lines.append(f"- **备份模式**: {'开启' if _config.backup_enabled else '关闭'}")
        lines.append(f"- **安全模式**: {'开启' if _config.external_safe_mode else '关闭'}")
        lines.append(f"- **多模型配置**: {len(_config.models)} 个")
        return JSONResponse(content={"result": "\n".join(lines), "format": "markdown"})

    # /model, /model list → 返回模型列表
    if command in {"/model", "/model list"}:
        try:
            assert _config is not None
            lines = ["### 可用模型\n"]
            lines.append(f"- **default** → `{_config.model}` — 默认模型（主配置） ✦")
            for profile in _config.models:
                desc = f" — {profile.description}" if profile.description else ""
                lines.append(f"- **{profile.name}** → `{profile.model}`{desc}")
            return JSONResponse(content={"result": "\n".join(lines), "format": "markdown"})
        except Exception:
            return JSONResponse(content={"result": "模型列表获取失败", "format": "text"})

    # /subagent list, /subagent status
    if command in {"/subagent list", "/subagent status"}:
        assert _config is not None
        status = "开启" if _config.subagent_enabled else "关闭"
        return JSONResponse(content={"result": f"子代理状态: **{status}**\n\n最大迭代: {_config.subagent_max_iterations}", "format": "markdown"})

    # /backup list, /backup status
    if command in {"/backup list", "/backup status"}:
        assert _config is not None
        status = "开启" if _config.backup_enabled else "关闭"
        return JSONResponse(content={"result": f"备份沙盒: **{status}**", "format": "markdown"})

    # /compact status
    if command == "/compact status":
        assert _config is not None
        status = "开启" if _config.compaction_enabled else "关闭"
        return JSONResponse(content={"result": f"上下文压缩: **{status}**\n\n阈值: {_config.compaction_threshold_ratio}", "format": "markdown"})

    # /fullaccess status
    if command == "/fullaccess status":
        # fullaccess 已改为跨会话持久化设置
        hint = "全权限模式: **关闭**\n\n使用 `/fullaccess on` 开启，开启后工具调用将跳过审批确认（跨会话生效）"
        if _database is not None:
            try:
                from excelmanus.stores.config_store import UserConfigStore
                _uc = UserConfigStore(_database.conn)
                if _uc.get_full_access():
                    hint = "全权限模式: **开启**（跨会话生效）\n\n使用 `/fullaccess off` 关闭"
            except Exception:
                pass
        elif _session_manager is not None:
            try:
                sessions = await _session_manager.list_sessions()
                for s in sessions:
                    detail = await _session_manager.get_session_detail(s["id"])
                    if detail.get("full_access_enabled"):
                        hint = f"全权限模式: **开启**\n\n使用 `/fullaccess off` 关闭"
                        break
            except Exception:
                pass
        return JSONResponse(content={"result": hint, "format": "markdown"})

    # /plan status
    if command == "/plan status":
        hint = "计划模式: **关闭**（默认）\n\n使用 `/plan on` 开启，开启后 Agent 会先输出计划再执行"
        return JSONResponse(content={"result": hint, "format": "markdown"})

    # /registry status
    if command == "/registry status":
        return JSONResponse(content={"result": "文件注册表: 请通过 `/registry scan` 触发扫描\n\n注册表会在会话首轮自动扫描构建", "format": "markdown"})

    # /save
    if command == "/save":
        if _session_manager is None:
            return JSONResponse(content={"result": "服务未初始化", "format": "text"})
        try:
            sessions = await _session_manager.list_sessions()
            count = len(sessions)
            return JSONResponse(content={"result": f"当前有 **{count}** 个活跃会话\n\n网页端对话自动保存，无需手动操作", "format": "markdown"})
        except Exception:
            return JSONResponse(content={"result": "会话状态获取失败", "format": "text"})

    # ── 会话级命令：需要 engine 实例 ──
    # 当前端传入 session_id 时，委托给 command_handler 处理会话级控制命令
    # （如 /fullaccess on, /subagent off, /compact, /rules, /memory, /playbook 等）
    session_id = body.get("session_id") or ""
    if session_id and _session_manager is not None:
        engine = _session_manager.get_engine(session_id)
        if engine is not None:
            try:
                result = await engine._command_handler.handle(command)
                if result is not None:
                    # 判断是否包含 markdown 格式
                    fmt = "markdown" if any(c in result for c in ("**", "##", "`", "- ")) else "text"
                    return JSONResponse(content={"result": result, "format": fmt})
            except Exception as exc:
                logger.warning("command_handler 执行 '%s' 异常: %s", command, exc, exc_info=True)
                return JSONResponse(content={"result": f"命令执行失败: {exc}", "format": "text"})

    return JSONResponse(content={"result": f"未知命令: {command}", "format": "text"})


@router.get("/api/v1/health")
async def health(request: Request) -> dict:
    """健康检查：返回版本号和已加载的工具/技能包。"""
    if get_draining():
        return {
            "status": "draining",
            "version": excelmanus.__version__,
            "restart_reason": get_restart_reason(),
        }

    if _is_external_safe_mode():
        return {
            "status": "ok",
            "version": excelmanus.__version__,
            "configured": not get_config_incomplete(),
            "model": "hidden",
            "tools": [],
            "skillpacks": [],
        }

    tools: list[str] = []
    skillpacks: list[str] = []
    _tool_registry = get_tool_registry()
    _skillpack_loader = get_skillpack_loader()
    _session_manager = get_session_manager()
    _config = get_config()
    if _tool_registry is not None:
        tools = sorted(_tool_registry.get_tool_names())
    if _skillpack_loader is not None:
        skillpacks = sorted(_skillpack_loader.get_skillpacks().keys())

    active_sessions = 0
    if _session_manager is not None:
        active_sessions = await _session_manager.get_active_count()

    # 发布清单摘要（供前端版本轮询使用）
    from excelmanus.api_routes_version import get_manifest_data, _API_SCHEMA_VERSION
    _manifest = get_manifest_data()
    _launcher = get_channel_launcher()

    return {
        "status": "ok",
        "version": excelmanus.__version__,
        "configured": not get_config_incomplete(),
        "model": _config.model if _config is not None else "",
        "tools": tools,
        "skillpacks": skillpacks,
        "active_sessions": active_sessions,
        "docker_sandbox_enabled": getattr(request.app.state, "docker_sandbox_enabled", False),
        "build_id": _manifest.get("frontend_build_id"),
        "version_fingerprint": _manifest.get("version_fingerprint"),
        "api_schema_version": _API_SCHEMA_VERSION,
        "git_commit": _manifest.get("git_commit"),
        "min_frontend_build_id": _manifest.get("min_frontend_build_id"),
        "min_backend_version": _manifest.get("min_backend_version"),
        "deploy_mode": _config.deploy_mode if _config is not None else "standalone",
        "channels": _launcher.active_channels if _launcher is not None else [],
    }


@router.get("/api/v1/server/public-ip")
async def server_public_ip() -> JSONResponse:
    """检测服务器的公网 IP 地址。"""
    import httpx

    _IP_SERVICES = [
        "https://api.ipify.org",
        "https://icanhazip.com",
        "https://checkip.amazonaws.com",
    ]
    async with httpx.AsyncClient(timeout=5) as client:
        for url in _IP_SERVICES:
            try:
                resp = await client.get(url)
                if resp.status_code == 200:
                    ip = resp.text.strip()
                    if ip:
                        return JSONResponse({"ip": ip})
            except Exception:
                continue
    return JSONResponse({"ip": None, "error": "无法检测公网 IP"}, status_code=503)
