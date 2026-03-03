"""ExcelManus API 客户端：所有渠道适配器共享的 HTTP 通信层。

封装对 ExcelManus REST API 的调用，包括 SSE 流式聊天、审批、问答、模型管理等。
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger("channels.api_client")

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
    ) -> None:
        self.api_url = (api_url or os.environ.get("EXCELMANUS_API_URL", "http://localhost:8000")).rstrip("/")
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout, connect=connect_timeout),
        )

    async def close(self) -> None:
        """关闭 HTTP 客户端。"""
        await self._client.aclose()

    # ── 聊天 ──

    async def stream_chat(
        self,
        message: str,
        session_id: str | None = None,
        chat_mode: str = "write",
        on_progress: ProgressCallback | None = None,
    ) -> ChatResult:
        """调用 SSE 流式聊天接口，返回结构化结果。"""
        payload: dict[str, Any] = {"message": message, "chat_mode": chat_mode}
        if session_id:
            payload["session_id"] = session_id

        result = ChatResult(session_id=session_id or "")
        reply_parts: list[str] = []

        async with self._client.stream(
            "POST",
            f"{self.api_url}/api/v1/chat/stream",
            json=payload,
            headers={"Accept": "text/event-stream"},
        ) as resp:
            resp.raise_for_status()
            event_type = ""

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

                event_type = ""

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

    # ── 审批 ──

    async def approve(
        self,
        session_id: str,
        approval_id: str,
        decision: str = "approve",
    ) -> dict[str, Any]:
        """提交审批决策。

        Args:
            decision: "approve" 或 "reject"。
        """
        resp = await self._client.post(
            f"{self.api_url}/api/v1/chat/{session_id}/approve",
            json={"approval_id": approval_id, "decision": decision},
        )
        resp.raise_for_status()
        return resp.json()

    # ── 问答 ──

    async def answer_question(
        self,
        session_id: str,
        question_id: str,
        answer: str,
    ) -> dict[str, Any]:
        """提交问答回答。"""
        resp = await self._client.post(
            f"{self.api_url}/api/v1/chat/{session_id}/answer",
            json={"question_id": question_id, "answer": answer},
        )
        resp.raise_for_status()
        return resp.json()

    # ── 终止 ──

    async def abort(self, session_id: str) -> dict[str, Any]:
        """终止活跃聊天任务。"""
        resp = await self._client.post(
            f"{self.api_url}/api/v1/chat/abort",
            json={"session_id": session_id},
        )
        resp.raise_for_status()
        return resp.json()

    # ── 模型管理 ──

    async def list_models(self) -> list[dict[str, Any]]:
        """获取可用模型列表。"""
        resp = await self._client.get(f"{self.api_url}/api/v1/models")
        resp.raise_for_status()
        return resp.json().get("models", [])

    async def switch_model(self, name: str) -> dict[str, Any]:
        """切换活跃模型。"""
        resp = await self._client.put(
            f"{self.api_url}/api/v1/models/active",
            json={"name": name},
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
    ) -> dict[str, Any]:
        """添加模型配置。"""
        resp = await self._client.post(
            f"{self.api_url}/api/v1/config/models/profiles",
            json={
                "name": name,
                "model": model,
                "base_url": base_url,
                "api_key": api_key,
                "description": description,
            },
        )
        resp.raise_for_status()
        return resp.json()

    async def delete_model(self, name: str) -> dict[str, Any]:
        """删除模型配置。"""
        resp = await self._client.delete(
            f"{self.api_url}/api/v1/config/models/profiles/{name}",
        )
        resp.raise_for_status()
        return resp.json()

    # ── 文件 ──

    async def download_file(self, file_path: str) -> tuple[bytes, str]:
        """下载工作区文件。返回 (content_bytes, filename)。"""
        resp = await self._client.get(
            f"{self.api_url}/api/v1/files/excel",
            params={"path": file_path},
        )
        resp.raise_for_status()
        filename = Path(file_path).name
        return resp.content, filename

    def get_workspace_path(self) -> str:
        """获取工作区路径。"""
        return os.environ.get(
            "EXCELMANUS_WORKSPACE",
            "/root/.openclaw/workspace/excelmanus/workspace",
        )

    async def upload_to_workspace(self, filename: str, data: bytes) -> str:
        """将文件写入工作区目录。返回完整路径。"""
        workspace = self.get_workspace_path()
        os.makedirs(workspace, exist_ok=True)
        dest = os.path.join(workspace, filename)
        with open(dest, "wb") as f:
            f.write(data)
        return dest
