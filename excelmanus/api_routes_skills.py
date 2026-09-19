"""Skillpack API。

从 api.py 抽出的独立路由模块。运行时状态只从 api_app_state 读取，
禁止 ``from excelmanus.api import _skillpack_manager`` 反向导入。
由 api.py 在 create_app 中 include_router 注册。
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import AliasChoices, BaseModel, ConfigDict, Field, StringConstraints

from excelmanus.api_app_state import (
    error_json_response as _error_json_response,
    get_skillpack_manager,
)
from excelmanus.skillpacks import (
    SkillpackConflictError,
    SkillpackInputError,
    SkillpackNotFoundError,
)
from excelmanus.skillpacks.importer import SkillImportError

router = APIRouter()

# OpenAPI 错误响应（与 api.py _error_responses 对应项保持一致）
_error_responses: dict = {
    403: {"description": "无权限执行"},
    404: {"description": "会话不存在"},
    409: {"description": "会话正在处理中"},
    422: {"description": "请求参数错误"},
    500: {"description": "服务内部错误"},
}


class SkillpackSummaryResponse(BaseModel):
    """Skillpack 摘要响应。"""

    model_config = ConfigDict(populate_by_name=True)

    name: str
    description: str
    source: str
    writable: bool
    argument_hint: str = Field(
        default="",
        validation_alias=AliasChoices("argument_hint", "argument-hint"),
        serialization_alias="argument-hint",
    )


class SkillpackDetailResponse(SkillpackSummaryResponse):
    """Skillpack 详情响应。"""

    file_patterns: list[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("file_patterns", "file-patterns"),
        serialization_alias="file-patterns",
    )
    resources: list[str]
    version: str
    disable_model_invocation: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "disable_model_invocation",
            "disable-model-invocation",
        ),
        serialization_alias="disable-model-invocation",
    )
    user_invocable: bool = Field(
        default=True,
        validation_alias=AliasChoices("user_invocable", "user-invocable"),
        serialization_alias="user-invocable",
    )
    instructions: str
    resource_contents: dict[str, str]
    hooks: dict[str, Any] = Field(default_factory=dict)
    model: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    required_mcp_servers: list[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices(
            "required_mcp_servers",
            "required-mcp-servers",
        ),
        serialization_alias="required-mcp-servers",
    )
    required_mcp_tools: list[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices(
            "required_mcp_tools",
            "required-mcp-tools",
        ),
        serialization_alias="required-mcp-tools",
    )
    extensions: dict[str, Any] = Field(default_factory=dict)


class SkillpackImportRequest(BaseModel):
    """导入 skillpack 请求体。"""

    model_config = ConfigDict(extra="forbid")
    source: Literal["local_path", "github_url"]
    value: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2048)
    ]
    overwrite: bool = False


class SkillpackCreateRequest(BaseModel):
    """创建 skillpack 请求体。"""

    model_config = ConfigDict(extra="forbid")
    name: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)
    ]
    payload: dict[str, Any]


class SkillpackPatchRequest(BaseModel):
    """更新 skillpack 请求体。"""

    model_config = ConfigDict(extra="forbid")
    payload: dict[str, Any]


class SkillpackMutationResponse(BaseModel):
    """写操作响应。"""

    status: str
    name: str
    detail: dict[str, Any] | None = None


def _require_skillpack_manager():
    """获取模块级 SkillpackManager 实例（无需创建 AgentEngine）。"""
    manager = get_skillpack_manager()
    if manager is None:
        raise HTTPException(status_code=503, detail="SkillpackManager 未初始化")
    return manager


def _to_skill_summary(detail: dict[str, Any]) -> SkillpackSummaryResponse:
    """从详情字典提取摘要响应。"""
    return SkillpackSummaryResponse(
        name=str(detail.get("name", "")),
        description=str(detail.get("description", "")),
        source=str(detail.get("source", "")),
        writable=bool(detail.get("writable", False)),
        argument_hint=str(
            detail.get("argument-hint", detail.get("argument_hint", "")) or ""
        ),
    )


def _to_skill_detail(detail: dict[str, Any]) -> SkillpackDetailResponse:
    """详情字典转换为响应模型。"""
    return SkillpackDetailResponse(
        name=str(detail.get("name", "")),
        description=str(detail.get("description", "")),
        source=str(detail.get("source", "")),
        writable=bool(detail.get("writable", False)),
        argument_hint=str(
            detail.get("argument-hint", detail.get("argument_hint", "")) or ""
        ),
        file_patterns=list(
            detail.get("file-patterns", detail.get("file_patterns", [])) or []
        ),
        resources=list(detail.get("resources", []) or []),
        version=str(detail.get("version", "1.0.0")),
        disable_model_invocation=bool(
            detail.get(
                "disable-model-invocation",
                detail.get("disable_model_invocation", False),
            )
        ),
        user_invocable=bool(
            detail.get("user-invocable", detail.get("user_invocable", True))
        ),
        instructions=str(detail.get("instructions", "") or ""),
        resource_contents=dict(detail.get("resource_contents", {}) or {}),
        hooks=dict(detail.get("hooks", {}) or {}),
        model=(
            str(detail.get("model")).strip()
            if detail.get("model") is not None and str(detail.get("model")).strip()
            else None
        ),
        metadata=dict(detail.get("metadata", {}) or {}),
        required_mcp_servers=list(
            detail.get(
                "required-mcp-servers",
                detail.get("required_mcp_servers", []),
            )
            or []
        ),
        required_mcp_tools=list(
            detail.get(
                "required-mcp-tools",
                detail.get("required_mcp_tools", []),
            )
            or []
        ),
        extensions=dict(detail.get("extensions", {}) or {}),
    )


def _to_standard_skill_detail_dict(detail: dict[str, Any]) -> dict[str, Any]:
    """将技能详情标准化为 API 输出字段。"""
    return _to_skill_detail(detail).model_dump(by_alias=True, exclude_none=False)


@router.get(
    "/api/v1/skills",
    response_model=list[SkillpackSummaryResponse],
    responses={
        500: _error_responses[500],
    },
)
async def list_skills(raw_request: Request) -> list[SkillpackSummaryResponse] | JSONResponse:
    """列出全部已加载 skillpack 摘要。"""
    manager = _require_skillpack_manager()
    details = manager.list_skillpacks()
    return [_to_skill_summary(detail) for detail in details]


@router.get(
    "/api/v1/skills/{name}",
    response_model=SkillpackDetailResponse | SkillpackSummaryResponse,
    responses={
        404: _error_responses[404],
        422: _error_responses[422],
        500: _error_responses[500],
    },
)
async def get_skill(name: str, raw_request: Request) -> SkillpackDetailResponse | SkillpackSummaryResponse | JSONResponse:
    """查询单个 skillpack。"""
    manager = _require_skillpack_manager()
    try:
        detail = manager.get_skillpack(name)
    except SkillpackInputError as exc:
        return _error_json_response(422, str(exc))
    except SkillpackNotFoundError as exc:
        return _error_json_response(404, str(exc))

    return _to_skill_detail(detail)


@router.post(
    "/api/v1/skills",
    status_code=201,
    response_model=SkillpackMutationResponse,
    responses={
        403: _error_responses[403],
        409: _error_responses[409],
        422: _error_responses[422],
        500: _error_responses[500],
    },
)
async def create_skill(
    request: SkillpackCreateRequest,
    raw_request: Request,
) -> SkillpackMutationResponse | JSONResponse:
    """创建 skillpack。"""
    manager = _require_skillpack_manager()
    try:
        detail = manager.create_skillpack(
            name=request.name,
            payload=request.payload,
            actor="api",
        )
    except SkillpackInputError as exc:
        return _error_json_response(422, str(exc))
    except SkillpackConflictError as exc:
        return _error_json_response(409, str(exc))

    return SkillpackMutationResponse(
        status="created",
        name=str(detail.get("name", request.name)),
        detail=_to_standard_skill_detail_dict(detail),
    )


@router.patch(
    "/api/v1/skills/{name}",
    response_model=SkillpackMutationResponse,
    responses={
        403: _error_responses[403],
        404: _error_responses[404],
        409: _error_responses[409],
        422: _error_responses[422],
        500: _error_responses[500],
    },
)
async def patch_skill(
    name: str,
    request: SkillpackPatchRequest,
    raw_request: Request,
) -> SkillpackMutationResponse | JSONResponse:
    """更新 skillpack。"""
    manager = _require_skillpack_manager()
    try:
        detail = manager.patch_skillpack(
            name=name,
            payload=request.payload,
            actor="api",
        )
    except SkillpackInputError as exc:
        return _error_json_response(422, str(exc))
    except SkillpackNotFoundError as exc:
        return _error_json_response(404, str(exc))
    except SkillpackConflictError as exc:
        return _error_json_response(409, str(exc))

    return SkillpackMutationResponse(
        status="updated",
        name=str(detail.get("name", name)),
        detail=_to_standard_skill_detail_dict(detail),
    )


@router.delete(
    "/api/v1/skills/{name}",
    response_model=SkillpackMutationResponse,
    responses={
        403: _error_responses[403],
        404: _error_responses[404],
        409: _error_responses[409],
        422: _error_responses[422],
        500: _error_responses[500],
    },
)
async def delete_skill(
    name: str,
    raw_request: Request,
    reason: str = "",
) -> SkillpackMutationResponse | JSONResponse:
    """软删除 skillpack。"""
    manager = _require_skillpack_manager()
    try:
        detail = manager.delete_skillpack(
            name=name,
            actor="api",
            reason=reason,
        )
    except SkillpackInputError as exc:
        return _error_json_response(422, str(exc))
    except SkillpackNotFoundError as exc:
        return _error_json_response(404, str(exc))
    except SkillpackConflictError as exc:
        return _error_json_response(409, str(exc))

    return SkillpackMutationResponse(
        status="deleted",
        name=str(detail.get("name", name)),
        detail=detail,
    )


@router.post(
    "/api/v1/skills/import",
    status_code=201,
    response_model=SkillpackMutationResponse,
    responses={
        403: _error_responses[403],
        409: _error_responses[409],
        422: _error_responses[422],
        500: _error_responses[500],
    },
)
async def import_skill(
    request: SkillpackImportRequest,
    raw_request: Request,
) -> SkillpackMutationResponse | JSONResponse:
    """从本地路径或 GitHub URL 导入 SKILL.md 及附属资源。"""
    manager = _require_skillpack_manager()
    try:
        result = await manager.import_skillpack_async(
            source=request.source,
            value=request.value,
            actor="api",
            overwrite=request.overwrite,
        )
    except SkillpackInputError as exc:
        return _error_json_response(422, str(exc))
    except SkillpackConflictError as exc:
        return _error_json_response(409, str(exc))
    except SkillImportError as exc:
        return _error_json_response(422, str(exc))

    return SkillpackMutationResponse(
        status="imported",
        name=str(result.get("name", "")),
        detail=result,
    )
