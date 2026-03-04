"""ExcelManus API 客户端：所有渠道适配器共享的 HTTP 通信层。

封装对 ExcelManus REST API 的调用，包括 SSE 流式聊天、审批、问答、模型管理等。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger("excelmanus.channels.api_client")

# 可重试的 HTTP 状态码
_RETRYABLE_STATUS_CODES = {429, 502, 503, 504}

# on_progress 回调签名: async def callback(stage: str, message: str) -> None
ProgressCallback = Callable[[str, str], Awaitable[None]]


@dataclass
class ChatResult:
    """流式聊天的结构化结果。"""

    reply: str = ""
    session_id: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    approval: dict[str, Any] | None = None
    question: dict[str, Any] | None = None
    file_downloads: list[dict[str, Any]] = field(default_factory=list)
    progress_events: list[dict[str, Any]] = field(default_factory=list)
    staging_event: dict[str, Any] | None = None
    error: str | None = None


class ExcelManusAPIClient:
    """统一的 ExcelManus API 客户端。

    所有渠道适配器通过此客户端与 ExcelManus 后端交互，
    避免各渠道重复实现 HTTP / SSE 逻辑。
    """

    def __init__(
        self,
        api_url: str | None = None,
        timeout: float = 300.0,
        connect_timeout: float = 10.0,
        max_retries: int = 3,
        retry_base_delay: float = 1.0,
        service_token: str | None = None,
    ) -> None:
        self.api_url = (api_url or os.environ.get("EXCELMANUS_API_URL", "http://localhost:8000")).rstrip("/")
        self._max_retries = max_retries
        self._retry_base_delay = retry_base_delay
        self._service_token = service_token
        self._on_behalf_of: str | None = None
        transport = httpx.AsyncHTTPTransport(retries=2)
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout, connect=connect_timeout),
            transport=transport,
        )

    def set_service_token(self, token: str) -> None:
        """设置服务令牌（可延迟注入）。"""
        self._service_token = token

    def set_on_behalf_of(self, user_id: str | None) -> None:
        """设置代理用户 ID（每次请求前调用）。"""
        self._on_behalf_of = user_id

    def _auth_headers(self, on_behalf_of: str | None = None) -> dict[str, str]:
        """构造认证请求头。

        包含 Authorization（service token）和可选的 X-On-Behalf-Of（代理用户）。
        """
        headers: dict[str, str] = {}
        if self._service_token:
            headers["Authorization"] = f"Bearer {self._service_token}"
        uid = on_behalf_of or self._on_behalf_of
        if uid:
            headers["X-On-Behalf-Of"] = uid
        return headers

    async def close(self) -> None:
        """关闭 HTTP 客户端。"""
        await self._client.aclose()

    # ── 重试基础设施 ──

    async def _request(
        self,
        method: str,
        url: str,
        **kwargs,
    ) -> httpx.Response:
        """带指数退避重试的 HTTP 请求。

        重试条件：
        - httpx.TransportError（连接重置、DNS 等）
        - httpx.TimeoutException
        - HTTP 429 / 502 / 503 / 504
        """
        # 自动注入认证请求头
        req_headers = kwargs.pop("headers", {}) or {}
        req_headers.update(self._auth_headers())
        kwargs["headers"] = req_headers

        last_exc: Exception | None = None
        for attempt in range(self._max_retries):
            try:
                resp = await self._client.request(method, url, **kwargs)
                if resp.status_code not in _RETRYABLE_STATUS_CODES:
                    return resp
                # 可重试状态码
                last_exc = httpx.HTTPStatusError(
                    f"{resp.status_code}",
                    request=resp.request,
                    response=resp,
                )
                if resp.status_code == 429:
                    retry_after = resp.headers.get("Retry-After")
                    if retry_after:
                        try:
                            delay = float(retry_after)
                        except ValueError:
                            delay = self._retry_base_delay * (2 ** attempt)
                    else:
                        delay = self._retry_base_delay * (2 ** attempt)
                else:
                    delay = self._retry_base_delay * (2 ** attempt)
            except (httpx.TransportError, httpx.TimeoutException) as exc:
                last_exc = exc
                delay = self._retry_base_delay * (2 ** attempt)

            # 指数退避 + jitter，上限 30 秒
            delay = min(delay, 30.0) + random.uniform(0, 0.5)
            if attempt < self._max_retries - 1:
                logger.warning(
                    "请求 %s %s 失败 (attempt %d/%d): %s — %.1fs 后重试",
                    method, url, attempt + 1, self._max_retries,
                    last_exc, delay,
                )
                await asyncio.sleep(delay)

        # 所有重试用尽
        if last_exc is not None:
            raise last_exc
        raise RuntimeError("Unexpected: no response and no exception after retries")

    # ── 聊天 ──

    async def stream_chat(
        self,
        message: str,
        session_id: str | None = None,
        chat_mode: str = "write",
        images: list[dict[str, str]] | None = None,
        on_progress: ProgressCallback | None = None,
        channel: str | None = None,
    ) -> ChatResult:
        """调用 SSE 流式聊天接口，返回结构化结果。"""
        payload: dict[str, Any] = {"message": message, "chat_mode": chat_mode}
        if session_id:
            payload["session_id"] = session_id
        if images:
            payload["images"] = images
        if channel:
            payload["channel"] = channel

        result = ChatResult(session_id=session_id or "")
        reply_parts: list[str] = []

        # SSE 连接阶段带重试；流读取阶段捕获异常保留部分结果
        stream_headers = {"Accept": "text/event-stream"}
        stream_headers.update(self._auth_headers())
        last_connect_exc: Exception | None = None
        for attempt in range(self._max_retries):
            try:
                async with self._client.stream(
                    "POST",
                    f"{self.api_url}/api/v1/chat/stream",
                    json=payload,
                    headers=stream_headers,
                ) as resp:
                    resp.raise_for_status()
                    last_connect_exc = None  # 连接成功
                    event_type = ""

                    try:
                        async for line in resp.aiter_lines():
                            if line.startswith("event:"):
                                event_type = line[6:].strip()
                                continue
                            if not line.startswith("data:"):
                                continue
                            try:
                                data = json.loads(line[5:].strip())
                            except json.JSONDecodeError:
                                continue

                            if event_type == "session_init":
                                result.session_id = data.get("session_id", result.session_id)

                            elif event_type in ("text", "text_delta"):
                                chunk = data.get("content", "")
                                if chunk:
                                    reply_parts.append(chunk)

                            elif event_type == "tool_call_start":
                                result.tool_calls.append({
                                    "name": data.get("tool_name", "unknown"),
                                    "status": "running",
                                })

                            elif event_type == "tool_call_end":
                                name = data.get("tool_name", "")
                                success = data.get("success", True)
                                for tc in reversed(result.tool_calls):
                                    if tc["name"] == name and tc["status"] == "running":
                                        tc["status"] = "done" if success else "error"
                                        break

                            elif event_type == "pending_approval":
                                result.approval = data

                            elif event_type == "user_question":
                                result.question = data

                            elif event_type == "file_download":
                                result.file_downloads.append(data)

                            elif event_type == "pipeline_progress":
                                result.progress_events.append(data)
                                if on_progress:
                                    stage = data.get("stage", "")
                                    progress_msg = data.get("message", "")
                                    try:
                                        await on_progress(stage, progress_msg)
                                    except Exception:
                                        logger.debug("on_progress callback error", exc_info=True)

                            elif event_type == "reply":
                                content = data.get("content", "")
                                if content and not reply_parts:
                                    reply_parts.append(content)

                            elif event_type == "error":
                                result.error = data.get("error", "未知错误")

                            elif event_type == "failure_guidance":
                                title = data.get("title", "")
                                message_text = data.get("message", "")
                                result.error = f"{title}: {message_text}" if title else message_text

                            elif event_type == "staging_updated":
                                result.staging_event = data

                            event_type = ""
                    except (httpx.ReadError, httpx.RemoteProtocolError) as stream_exc:
                        logger.warning("SSE 流读取中断: %s（已收集部分结果）", stream_exc)
                        if not result.error:
                            result.error = "连接中断，以下为部分结果"
                break  # 连接成功且流读完（或中断但已保留部分结果），不再重试
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code in _RETRYABLE_STATUS_CODES:
                    last_connect_exc = exc
                    if exc.response.status_code == 429:
                        retry_after = exc.response.headers.get("Retry-After")
                        try:
                            delay = float(retry_after) if retry_after else self._retry_base_delay * (2 ** attempt)
                        except ValueError:
                            delay = self._retry_base_delay * (2 ** attempt)
                    else:
                        delay = self._retry_base_delay * (2 ** attempt)
                    delay = min(delay, 30.0) + random.uniform(0, 0.5)
                    if attempt < self._max_retries - 1:
                        logger.warning(
                            "SSE 连接 HTTP %d (attempt %d/%d) — %.1fs 后重试",
                            exc.response.status_code, attempt + 1, self._max_retries, delay,
                        )
                        await asyncio.sleep(delay)
                else:
                    raise
            except (httpx.TransportError, httpx.TimeoutException) as exc:
                last_connect_exc = exc
                if attempt < self._max_retries - 1:
                    delay = min(self._retry_base_delay * (2 ** attempt), 30.0) + random.uniform(0, 0.5)
                    logger.warning(
                        "SSE 连接失败 (attempt %d/%d): %s — %.1fs 后重试",
                        attempt + 1, self._max_retries, exc, delay,
                    )
                    await asyncio.sleep(delay)

        if last_connect_exc is not None:
            raise last_connect_exc

        # 组装回复文本
        sections: list[str] = []
        if result.tool_calls:
            icons = {"done": "✅", "error": "❌", "running": "🔧"}
            chain = " → ".join(
                f"{icons.get(tc['status'], '🔧')} {tc['name']}" for tc in result.tool_calls
            )
            sections.append(f"⚙️ {chain}")
        text = "".join(reply_parts).strip()
        if text:
            sections.append(text)
        if result.error:
            sections.append(f"❌ {result.error}")
        result.reply = "\n\n".join(sections) if sections else ""
        return result

    async def stream_chat_events(
        self,
        message: str,
        session_id: str | None = None,
        chat_mode: str = "write",
        images: list[dict[str, str]] | None = None,
        on_behalf_of: str | None = None,
        channel: str | None = None,
    ) -> AsyncIterator[tuple[str, dict[str, Any]]]:
        """SSE 事件的异步生成器，供 ChunkedOutputManager 消费。

        与 stream_chat() 并存（向后兼容），但不累积结果，
        而是逐事件 yield，由调用方自行处理。

        Yields:
            (event_type, data) 元组。
        """
        payload: dict[str, Any] = {"message": message, "chat_mode": chat_mode}
        if session_id:
            payload["session_id"] = session_id
        if images:
            payload["images"] = images
        if channel:
            payload["channel"] = channel

        stream_headers2 = {"Accept": "text/event-stream"}
        stream_headers2.update(self._auth_headers(on_behalf_of=on_behalf_of))
        last_connect_exc: Exception | None = None
        for attempt in range(self._max_retries):
            try:
                async with self._client.stream(
                    "POST",
                    f"{self.api_url}/api/v1/chat/stream",
                    json=payload,
                    headers=stream_headers2,
                ) as resp:
                    resp.raise_for_status()
                    last_connect_exc = None
                    event_type = ""

                    try:
                        async for line in resp.aiter_lines():
                            if line.startswith("event:"):
                                event_type = line[6:].strip()
                                continue
                            if not line.startswith("data:"):
                                continue
                            try:
                                data = json.loads(line[5:].strip())
                            except json.JSONDecodeError:
                                continue

                            yield (event_type, data)
                            event_type = ""
                    except (httpx.ReadError, httpx.RemoteProtocolError) as stream_exc:
                        logger.warning("SSE 流读取中断: %s", stream_exc)
                        yield ("error", {"error": "连接中断，以下为部分结果"})
                break
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code in _RETRYABLE_STATUS_CODES:
                    last_connect_exc = exc
                    if exc.response.status_code == 429:
                        retry_after = exc.response.headers.get("Retry-After")
                        try:
                            delay = float(retry_after) if retry_after else self._retry_base_delay * (2 ** attempt)
                        except ValueError:
                            delay = self._retry_base_delay * (2 ** attempt)
                    else:
                        delay = self._retry_base_delay * (2 ** attempt)
                    delay = min(delay, 30.0) + random.uniform(0, 0.5)
                    if attempt < self._max_retries - 1:
                        logger.warning(
                            "SSE 连接 HTTP %d (attempt %d/%d) — %.1fs 后重试",
                            exc.response.status_code, attempt + 1, self._max_retries, delay,
                        )
                        await asyncio.sleep(delay)
                else:
                    raise
            except (httpx.TransportError, httpx.TimeoutException) as exc:
                last_connect_exc = exc
                if attempt < self._max_retries - 1:
                    delay = min(self._retry_base_delay * (2 ** attempt), 30.0) + random.uniform(0, 0.5)
                    logger.warning(
                        "SSE 连接失败 (attempt %d/%d): %s — %.1fs 后重试",
                        attempt + 1, self._max_retries, exc, delay,
                    )
                    await asyncio.sleep(delay)

        if last_connect_exc is not None:
            raise last_connect_exc

    # ── 审批 ──

    async def approve(
        self,
        session_id: str,
        approval_id: str,
        decision: str = "approve",
        *,
        on_behalf_of: str | None = None,
    ) -> dict[str, Any]:
        """提交审批决策。

        Args:
            decision: "approve" 或 "reject"。
        """
        headers = {}
        if on_behalf_of:
            headers.update(self._auth_headers(on_behalf_of=on_behalf_of))
        resp = await self._request(
            "POST",
            f"{self.api_url}/api/v1/chat/{session_id}/approve",
            json={"approval_id": approval_id, "decision": decision},
            headers=headers or None,
        )
        resp.raise_for_status()
        return resp.json()

    # ── 问答 ──

    async def answer_question(
        self,
        session_id: str,
        question_id: str,
        answer: str,
        *,
        on_behalf_of: str | None = None,
    ) -> dict[str, Any]:
        """提交问答回答。"""
        headers = {}
        if on_behalf_of:
            headers.update(self._auth_headers(on_behalf_of=on_behalf_of))
        resp = await self._request(
            "POST",
            f"{self.api_url}/api/v1/chat/{session_id}/answer",
            json={"question_id": question_id, "answer": answer},
            headers=headers or None,
        )
        resp.raise_for_status()
        return resp.json()

    # ── 终止 ──

    async def abort(
        self, session_id: str, *, on_behalf_of: str | None = None,
    ) -> dict[str, Any]:
        """终止活跃聊天任务。"""
        headers = {}
        if on_behalf_of:
            headers.update(self._auth_headers(on_behalf_of=on_behalf_of))
        resp = await self._request(
            "POST",
            f"{self.api_url}/api/v1/chat/abort",
            json={"session_id": session_id},
            headers=headers or None,
        )
        resp.raise_for_status()
        return resp.json()

    # ── 引导消息 ──

    async def guide_message(
        self, session_id: str, message: str,
        *, on_behalf_of: str | None = None,
    ) -> dict[str, Any]:
        """向运行中的会话注入引导消息（不启动新 chat）。"""
        headers = {}
        if on_behalf_of:
            headers.update(self._auth_headers(on_behalf_of=on_behalf_of))
        resp = await self._request(
            "POST",
            f"{self.api_url}/api/v1/chat/{session_id}/guide",
            json={"message": message},
            headers=headers or None,
        )
        resp.raise_for_status()
        return resp.json()

    # ── 模型管理 ──

    async def list_models(
        self, *, on_behalf_of: str | None = None,
    ) -> list[dict[str, Any]]:
        """获取可用模型列表。携带 on_behalf_of 时按用户 allowed_models 过滤。"""
        headers = {}
        if on_behalf_of:
            headers.update(self._auth_headers(on_behalf_of=on_behalf_of))
        resp = await self._request(
            "GET", f"{self.api_url}/api/v1/models", headers=headers or None,
        )
        resp.raise_for_status()
        return resp.json().get("models", [])

    async def switch_model(
        self, name: str, *, on_behalf_of: str | None = None,
    ) -> dict[str, Any]:
        """切换活跃模型。携带 on_behalf_of 时持久化到用户级配置。"""
        headers = {}
        if on_behalf_of:
            headers.update(self._auth_headers(on_behalf_of=on_behalf_of))
        resp = await self._request(
            "PUT",
            f"{self.api_url}/api/v1/models/active",
            json={"name": name},
            headers=headers or None,
        )
        resp.raise_for_status()
        return resp.json()

    async def add_model(
        self,
        name: str,
        model: str,
        base_url: str,
        api_key: str,
        description: str = "",
        *,
        on_behalf_of: str | None = None,
    ) -> dict[str, Any]:
        """添加模型配置。需要管理员权限。"""
        headers = {}
        if on_behalf_of:
            headers.update(self._auth_headers(on_behalf_of=on_behalf_of))
        resp = await self._request(
            "POST",
            f"{self.api_url}/api/v1/config/models/profiles",
            json={
                "name": name,
                "model": model,
                "base_url": base_url,
                "api_key": api_key,
                "description": description,
            },
            headers=headers or None,
        )
        resp.raise_for_status()
        return resp.json()

    async def delete_model(
        self, name: str, *, on_behalf_of: str | None = None,
    ) -> dict[str, Any]:
        """删除模型配置。需要管理员权限。"""
        headers = {}
        if on_behalf_of:
            headers.update(self._auth_headers(on_behalf_of=on_behalf_of))
        resp = await self._request(
            "DELETE",
            f"{self.api_url}/api/v1/config/models/profiles/{name}",
            headers=headers or None,
        )
        resp.raise_for_status()
        return resp.json()

    async def get_usage(
        self, *, on_behalf_of: str | None = None,
    ) -> dict[str, Any]:
        """获取当前用户的 token 用量和配额信息。"""
        headers = {}
        if on_behalf_of:
            headers.update(self._auth_headers(on_behalf_of=on_behalf_of))
        resp = await self._request(
            "GET", f"{self.api_url}/api/v1/auth/me/usage",
            headers=headers or None,
        )
        resp.raise_for_status()
        return resp.json()

    # ── 会话管理 ──

    async def list_sessions(
        self, *, on_behalf_of: str | None = None,
    ) -> list[dict[str, Any]]:
        """列出会话。携带 on_behalf_of 时按用户过滤。"""
        headers = {}
        if on_behalf_of:
            headers.update(self._auth_headers(on_behalf_of=on_behalf_of))
        resp = await self._request(
            "GET", f"{self.api_url}/api/v1/sessions", headers=headers or None,
        )
        resp.raise_for_status()
        return resp.json().get("sessions", [])

    async def list_turns(
        self, session_id: str, *, on_behalf_of: str | None = None,
    ) -> list[dict[str, Any]]:
        """列出指定会话的用户轮次摘要。"""
        headers = {}
        if on_behalf_of:
            headers.update(self._auth_headers(on_behalf_of=on_behalf_of))
        resp = await self._request(
            "GET",
            f"{self.api_url}/api/v1/chat/turns",
            params={"session_id": session_id},
            headers=headers or None,
        )
        resp.raise_for_status()
        return resp.json().get("turns", [])

    async def rollback(
        self,
        session_id: str,
        turn_index: int,
        rollback_files: bool = True,
        *,
        on_behalf_of: str | None = None,
    ) -> dict[str, Any]:
        """回退对话到指定用户轮次。"""
        headers = {}
        if on_behalf_of:
            headers.update(self._auth_headers(on_behalf_of=on_behalf_of))
        resp = await self._request(
            "POST",
            f"{self.api_url}/api/v1/chat/rollback",
            json={
                "session_id": session_id,
                "turn_index": turn_index,
                "rollback_files": rollback_files,
            },
            headers=headers or None,
        )
        resp.raise_for_status()
        return resp.json()

    async def list_operations(
        self, session_id: str, limit: int = 10,
        *, on_behalf_of: str | None = None,
    ) -> list[dict[str, Any]]:
        """列出指定会话的操作历史。"""
        headers = {}
        if on_behalf_of:
            headers.update(self._auth_headers(on_behalf_of=on_behalf_of))
        resp = await self._request(
            "GET",
            f"{self.api_url}/api/v1/sessions/{session_id}/operations",
            params={"limit": limit},
            headers=headers or None,
        )
        resp.raise_for_status()
        return resp.json().get("operations", [])

    async def undo_operation(
        self, session_id: str, approval_id: str,
        *, on_behalf_of: str | None = None,
    ) -> dict[str, Any]:
        """回滚指定操作。"""
        headers = {}
        if on_behalf_of:
            headers.update(self._auth_headers(on_behalf_of=on_behalf_of))
        resp = await self._request(
            "POST",
            f"{self.api_url}/api/v1/sessions/{session_id}/operations/{approval_id}/undo",
            headers=headers or None,
        )
        resp.raise_for_status()
        return resp.json()

    # ── Staged 文件管理 ──

    async def list_staged(
        self, session_id: str,
        *, on_behalf_of: str | None = None,
    ) -> dict[str, Any]:
        """列出指定会话的待应用 staged 文件。"""
        headers = {}
        if on_behalf_of:
            headers.update(self._auth_headers(on_behalf_of=on_behalf_of))
        resp = await self._request(
            "GET",
            f"{self.api_url}/api/v1/backup/list",
            params={"session_id": session_id},
            headers=headers or None,
        )
        resp.raise_for_status()
        return resp.json()

    async def apply_staged(
        self,
        session_id: str,
        files: list[str] | None = None,
        *,
        on_behalf_of: str | None = None,
    ) -> dict[str, Any]:
        """将 staged 文件应用回原始位置。files 为空时应用全部。"""
        headers = {}
        if on_behalf_of:
            headers.update(self._auth_headers(on_behalf_of=on_behalf_of))
        resp = await self._request(
            "POST",
            f"{self.api_url}/api/v1/backup/apply",
            json={"session_id": session_id, "files": files},
            headers=headers or None,
        )
        resp.raise_for_status()
        return resp.json()

    async def discard_staged(
        self,
        session_id: str,
        files: list[str] | None = None,
        *,
        on_behalf_of: str | None = None,
    ) -> dict[str, Any]:
        """丢弃 staged 文件。files 为空时丢弃全部。"""
        headers = {}
        if on_behalf_of:
            headers.update(self._auth_headers(on_behalf_of=on_behalf_of))
        resp = await self._request(
            "POST",
            f"{self.api_url}/api/v1/backup/discard",
            json={"session_id": session_id, "files": files},
            headers=headers or None,
        )
        resp.raise_for_status()
        return resp.json()

    async def undo_backup(
        self,
        session_id: str,
        original_path: str,
        undo_path: str,
        *,
        on_behalf_of: str | None = None,
    ) -> dict[str, Any]:
        """撤销已应用的备份，恢复到 apply 前状态。"""
        headers = {}
        if on_behalf_of:
            headers.update(self._auth_headers(on_behalf_of=on_behalf_of))
        resp = await self._request(
            "POST",
            f"{self.api_url}/api/v1/backup/undo",
            json={
                "session_id": session_id,
                "original_path": original_path,
                "undo_path": undo_path,
            },
            headers=headers or None,
        )
        resp.raise_for_status()
        return resp.json()

    # ── 文件 ──

    async def download_file(
        self, file_path: str,
        *, on_behalf_of: str | None = None,
    ) -> tuple[bytes, str]:
        """下载工作区文件。返回 (content_bytes, filename)。"""
        headers = {}
        if on_behalf_of:
            headers.update(self._auth_headers(on_behalf_of=on_behalf_of))
        resp = await self._request(
            "GET",
            f"{self.api_url}/api/v1/files/excel",
            params={"path": file_path},
            headers=headers or None,
        )
        resp.raise_for_status()
        filename = Path(file_path).name
        return resp.content, filename

    async def generate_download_link(
        self, file_path: str,
        *, user_id: str = "",
        on_behalf_of: str | None = None,
    ) -> str | None:
        """生成文件的短效公开下载链接。返回 URL 或 None（失败时）。"""
        headers = {}
        if on_behalf_of:
            headers.update(self._auth_headers(on_behalf_of=on_behalf_of))
        try:
            resp = await self._request(
                "POST",
                f"{self.api_url}/api/v1/files/download/link",
                json={"file_path": file_path, "user_id": user_id},
                headers=headers or None,
            )
            resp.raise_for_status()
            return resp.json().get("url")
        except Exception:
            return None

    def get_workspace_path(self) -> str:
        """获取工作区路径。"""
        return os.environ.get(
            "EXCELMANUS_WORKSPACE_ROOT",
            os.environ.get("EXCELMANUS_WORKSPACE", "."),
        )

    async def upload_to_workspace(
        self, filename: str, data: bytes,
        *, on_behalf_of: str | None = None,
    ) -> str:
        """上传文件到工作区。优先通过 HTTP API 上传；不可用时回退到本地写入。"""
        # 优先尝试 HTTP 上传
        try:
            import io
            headers = {}
            if on_behalf_of:
                headers.update(self._auth_headers(on_behalf_of=on_behalf_of))
            resp = await self._request(
                "POST",
                f"{self.api_url}/api/v1/upload",
                files={"file": (filename, io.BytesIO(data))},
                headers=headers or None,
            )
            if resp.status_code < 400:
                result = resp.json()
                return result.get("path", filename)
        except Exception:
            logger.debug("HTTP 上传不可用，回退到本地写入", exc_info=True)

        # 回退：本地写入（仅限 Bot 与 API 同机部署）
        import uuid as _uuid
        workspace = self.get_workspace_path()
        upload_dir = os.path.join(workspace, "uploads")
        os.makedirs(upload_dir, exist_ok=True)
        safe_name = f"{_uuid.uuid4().hex[:8]}_{filename}"
        dest = os.path.join(upload_dir, safe_name)
        with open(dest, "wb") as f:
            f.write(data)
        return f"./uploads/{safe_name}"
