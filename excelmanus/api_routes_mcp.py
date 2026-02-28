"""MCP Server 管理 API 路由。

从 api.py 提取的独立路由模块，通过延迟导入访问 api.py 的全局状态。
由 api.py 在 lifespan 完成后通过 include_router 注册。
"""

from __future__ import annotations

import json
import os
from typing import Any, Literal

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from excelmanus.logger import get_logger

logger = get_logger("api.mcp")

router = APIRouter()


# ── 辅助函数 ──────────────────────────────────────────

def _get_config():
    """延迟导入获取全局 config。"""
    from excelmanus.api import _config
    return _config


def _get_error_response(status_code: int, message: str) -> JSONResponse:
    """延迟导入获取统一错误响应构造器。"""
    from excelmanus.api import _error_json_response
    return _error_json_response(status_code, message)


def _find_mcp_config_path() -> str:
    """定位 mcp.json 配置文件路径（写操作目标）。"""
    env_path = os.environ.get("EXCELMANUS_MCP_CONFIG")
    if env_path and os.path.isfile(env_path):
        return env_path
    config = _get_config()
    ws = config.workspace_root if config else os.getcwd()
    ws_path = os.path.join(ws, "mcp.json")
    if os.path.isfile(ws_path):
        return ws_path
    home_path = os.path.join(os.path.expanduser("~"), ".excelmanus", "mcp.json")
    if os.path.isfile(home_path):
        return home_path
    return ws_path


def _read_mcp_json(path: str) -> dict:
    """读取 mcp.json 文件内容。"""
    if not os.path.isfile(path):
        return {"mcpServers": {}}
    with open(path, "r", encoding="utf-8") as f:
        try:
            data = json.load(f)
        except json.JSONDecodeError:
            return {"mcpServers": {}}
    if not isinstance(data, dict):
        return {"mcpServers": {}}
    if "mcpServers" not in data:
        data["mcpServers"] = {}
    return data


def _write_mcp_json(path: str, data: dict) -> None:
    """写回 mcp.json 文件。"""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")


def _get_shared_mcp_manager():
    """获取共享 MCP 管理器实例。"""
    from excelmanus.api import _session_manager
    if _session_manager is None:
        return None
    return getattr(_session_manager, "_shared_mcp_manager", None)


# ── 请求模型 ──────────────────────────────────────────

class MCPServerCreateRequest(BaseModel):
    """创建/更新 MCP Server 请求体。"""
    model_config = ConfigDict(extra="forbid")
    name: str = ""
    transport: Literal["stdio", "sse", "streamable_http"]
    command: str | None = None
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    url: str | None = None
    headers: dict[str, str] = Field(default_factory=dict)
    timeout: int = 30
    autoApprove: list[str] = Field(default_factory=list)


def _server_request_to_entry(req: MCPServerCreateRequest) -> dict:
    """将请求体转为 mcp.json 条目格式。"""
    entry: dict[str, Any] = {"transport": req.transport}
    if req.transport == "stdio":
        if req.command:
            entry["command"] = req.command
        if req.args:
            entry["args"] = req.args
        if req.env:
            entry["env"] = req.env
    else:
        if req.url:
            entry["url"] = req.url
        if req.headers:
            entry["headers"] = req.headers
    if req.timeout != 30:
        entry["timeout"] = req.timeout
    if req.autoApprove:
        entry["autoApprove"] = req.autoApprove
    return entry


# ── 路由 ──────────────────────────────────────────────

@router.get("/api/v1/mcp/servers")
async def list_mcp_servers() -> JSONResponse:
    """列出所有 MCP Server 配置 + 运行时状态。"""
    config_path = _find_mcp_config_path()
    config_data = _read_mcp_json(config_path)
    servers_dict = config_data.get("mcpServers", {})

    runtime_info: dict[str, dict] = {}
    manager = _get_shared_mcp_manager()
    if manager is not None:
        for info in manager.get_server_info():
            runtime_info[info["name"]] = info

    result = []
    for name, entry in servers_dict.items():
        rt = runtime_info.get(name, {})
        result.append({
            "name": name,
            "config": entry,
            "status": rt.get("status", "not_connected"),
            "transport": entry.get("transport", "unknown"),
            "tool_count": rt.get("tool_count", 0),
            "tools": rt.get("tools", []),
            "last_error": rt.get("last_error"),
            "auto_approve": entry.get("autoApprove", []),
        })

    return JSONResponse(content={"servers": result, "config_path": config_path})


@router.post("/api/v1/mcp/servers", status_code=201)
async def create_mcp_server(request: MCPServerCreateRequest) -> JSONResponse:
    """新增 MCP Server 条目到 mcp.json。"""
    if not request.name.strip():
        return _get_error_response(400, "Server 名称不能为空")

    config_path = _find_mcp_config_path()
    data = _read_mcp_json(config_path)

    if request.name in data["mcpServers"]:
        return _get_error_response(409, f"Server '{request.name}' 已存在")

    entry = _server_request_to_entry(request)
    data["mcpServers"][request.name] = entry
    _write_mcp_json(config_path, data)

    return JSONResponse(
        status_code=201,
        content={"status": "created", "name": request.name},
    )


@router.put("/api/v1/mcp/servers/{name}")
async def update_mcp_server(name: str, request: MCPServerCreateRequest) -> JSONResponse:
    """更新 MCP Server 配置。"""
    config_path = _find_mcp_config_path()
    data = _read_mcp_json(config_path)

    if name not in data["mcpServers"]:
        return _get_error_response(404, f"Server '{name}' 不存在")

    entry = _server_request_to_entry(request)

    new_name = request.name.strip() or name
    if new_name != name:
        del data["mcpServers"][name]
    data["mcpServers"][new_name] = entry
    _write_mcp_json(config_path, data)

    return JSONResponse(content={"status": "updated", "name": new_name})


@router.delete("/api/v1/mcp/servers/{name}")
async def delete_mcp_server(name: str) -> JSONResponse:
    """删除 MCP Server 条目。"""
    config_path = _find_mcp_config_path()
    data = _read_mcp_json(config_path)

    if name not in data["mcpServers"]:
        return _get_error_response(404, f"Server '{name}' 不存在")

    del data["mcpServers"][name]
    _write_mcp_json(config_path, data)

    return JSONResponse(content={"status": "deleted", "name": name})


@router.post("/api/v1/mcp/reload")
async def reload_mcp() -> JSONResponse:
    """热重载所有 MCP 连接：关闭现有连接 → 重新初始化。"""
    from excelmanus.api import _session_manager, _tool_registry

    manager = _get_shared_mcp_manager()
    if manager is None:
        return _get_error_response(400, "未启用共享 MCP 管理器")

    try:
        await manager.shutdown()
        manager._initialized = False
        if _session_manager is not None:
            _session_manager.reset_mcp_initialized()
        assert _tool_registry is not None
        await manager.initialize(_tool_registry)
    except Exception as exc:
        logger.error("MCP 热重载失败: %s", exc, exc_info=True)
        return _get_error_response(500, f"MCP 热重载失败: {exc}")

    info = manager.get_server_info()
    ready = sum(1 for s in info if s["status"] == "ready")
    return JSONResponse(content={
        "status": "ok",
        "servers_total": len(info),
        "servers_ready": ready,
    })


@router.post("/api/v1/mcp/servers/{name}/test")
async def test_mcp_server(name: str) -> JSONResponse:
    """测试单个 MCP Server 连接。"""
    config_path = _find_mcp_config_path()
    data = _read_mcp_json(config_path)

    if name not in data["mcpServers"]:
        return _get_error_response(404, f"Server '{name}' 不存在")

    from excelmanus.mcp.client import MCPClientWrapper
    from excelmanus.mcp.config import MCPConfigLoader

    single_data = {"mcpServers": {name: data["mcpServers"][name]}}
    configs = MCPConfigLoader._parse_config(single_data)
    if not configs:
        return _get_error_response(400, f"Server '{name}' 配置无效")

    cfg = configs[0]
    client = MCPClientWrapper(cfg)
    try:
        await client.connect()
        tools = await client.discover_tools()
        tool_names = [getattr(t, "name", str(t)) for t in tools]
        await client.close()
        return JSONResponse(content={
            "status": "ok",
            "name": name,
            "tool_count": len(tool_names),
            "tools": tool_names,
        })
    except Exception as exc:
        try:
            await client.close()
        except Exception:
            pass
        return JSONResponse(
            status_code=502,
            content={
                "status": "error",
                "name": name,
                "error": str(exc)[:300],
            },
        )
