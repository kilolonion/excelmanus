"""Capability probe jobs: async orchestration + progress snapshots + SSE feed."""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

from excelmanus.logger import get_logger
from excelmanus.model_probe import ModelCapabilities, run_full_probe
from excelmanus.providers import create_client

logger = get_logger("capability_probe_jobs")

_TERMINAL_JOB_STATES = {"succeeded", "partial", "failed", "cancelled"}
_TERMINAL_TARGET_STATES = {"succeeded", "partial", "failed", "cancelled"}
_DEFAULT_STAGES = ("health", "tool_calling", "vision", "thinking")


def _utc_now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _provider_key(base_url: str) -> str:
    try:
        host = (urlparse(base_url).hostname or "").lower()
    except Exception:
        host = ""
    if not host:
        return "generic"
    if "openrouter" in host:
        return "openrouter"
    if "openai" in host or "chatgpt.com" in host:
        return "openai"
    if "x.ai" in host:
        return "xai"
    if "deepseek" in host:
        return "deepseek"
    if "zhipu" in host or "bigmodel" in host:
        return "glm"
    if "mistral" in host:
        return "mistral"
    if "googleapis" in host or "generativelanguage" in host:
        return "gemini"
    return host


def _target_cache_key(model: str, base_url: str) -> str:
    return f"{model.strip().lower()}|{base_url.strip().rstrip('/')}"


def _target_state_from_caps(caps: ModelCapabilities | Any) -> str:
    # Test doubles may only expose to_dict(), so support both object attributes
    # and mapping-style payloads when deriving the final target state.
    if hasattr(caps, "to_dict"):
        try:
            payload = caps.to_dict()
        except Exception:
            payload = {}
    else:
        payload = {}

    healthy = getattr(caps, "healthy", payload.get("healthy"))
    if healthy is not True:
        return "failed"

    vals = [
        getattr(caps, "supports_tool_calling", payload.get("supports_tool_calling")),
        getattr(caps, "supports_vision", payload.get("supports_vision")),
        getattr(caps, "supports_thinking", payload.get("supports_thinking")),
    ]
    if all(v is not None for v in vals):
        return "succeeded"
    if any(v is not None for v in vals):
        return "partial"
    return "failed"


@dataclass(slots=True)
class ProbeTargetSpec:
    name: str
    cache_model: str
    api_model: str
    base_url: str
    api_key: str
    protocol: str
    thinking_mode: str = "auto"


@dataclass(slots=True)
class ProbeStageStatus:
    state: str = "pending"  # pending|running|completed|failed|skipped
    started_at: str = ""
    finished_at: str = ""
    value: Any = None
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "value": self.value,
            "error": self.error,
        }


@dataclass(slots=True)
class ProbeTargetSnapshot:
    name: str
    model: str
    base_url: str
    state: str = "queued"  # queued|waiting|running|succeeded|partial|failed|cancelled
    started_at: str = ""
    finished_at: str = ""
    error: str = ""
    deduplicated: bool = False
    stages: dict[str, ProbeStageStatus] = field(
        default_factory=lambda: {stage: ProbeStageStatus() for stage in _DEFAULT_STAGES}
    )
    capabilities: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "model": self.model,
            "base_url": self.base_url,
            "state": self.state,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "error": self.error,
            "deduplicated": self.deduplicated,
            "stages": {k: v.to_dict() for k, v in self.stages.items()},
            "capabilities": self.capabilities,
        }


@dataclass(slots=True)
class ProbeJob:
    job_id: str
    state: str = "queued"  # queued|running|succeeded|partial|failed|cancelled|cancelling
    created_at: str = field(default_factory=_utc_now_iso)
    started_at: str = ""
    finished_at: str = ""
    cancel_requested: bool = False
    targets: list[ProbeTargetSnapshot] = field(default_factory=list)
    subscribers: list[asyncio.Queue[dict[str, Any]]] = field(default_factory=list)
    task: asyncio.Task[Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        done = 0
        succeeded = 0
        partial = 0
        failed = 0
        cancelled = 0
        for target in self.targets:
            if target.state in _TERMINAL_TARGET_STATES:
                done += 1
            if target.state == "succeeded":
                succeeded += 1
            elif target.state == "partial":
                partial += 1
            elif target.state == "failed":
                failed += 1
            elif target.state == "cancelled":
                cancelled += 1

        return {
            "job_id": self.job_id,
            "state": self.state,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "targets_total": len(self.targets),
            "targets_done": done,
            "targets_succeeded": succeeded,
            "targets_partial": partial,
            "targets_failed": failed,
            "targets_cancelled": cancelled,
            "targets": [t.to_dict() for t in self.targets],
        }


class CapabilityProbeJobManager:
    def __init__(
        self,
        *,
        job_concurrency: int = 2,
        provider_concurrency: int = 1,
        health_timeout: float = 8.0,
        tool_timeout: float = 20.0,
        vision_timeout: float = 20.0,
        thinking_total_timeout: float = 30.0,
        thinking_strategy_timeout: float = 8.0,
    ) -> None:
        self._jobs: dict[str, ProbeJob] = {}
        self._jobs_lock = asyncio.Lock()
        self._target_inflight: dict[str, asyncio.Future[ModelCapabilities]] = {}
        self._target_inflight_lock = asyncio.Lock()
        self._global_sem = asyncio.Semaphore(max(1, int(job_concurrency)))
        self._provider_concurrency = max(1, int(provider_concurrency))
        self._provider_sems: dict[str, asyncio.Semaphore] = {}

        self._health_timeout = float(health_timeout)
        self._tool_timeout = float(tool_timeout)
        self._vision_timeout = float(vision_timeout)
        self._thinking_total_timeout = float(thinking_total_timeout)
        self._thinking_strategy_timeout = float(thinking_strategy_timeout)

    async def shutdown(self) -> None:
        async with self._jobs_lock:
            jobs = list(self._jobs.values())
        for job in jobs:
            job.cancel_requested = True
            if job.task is not None and not job.task.done():
                job.task.cancel()
        for job in jobs:
            task = job.task
            if task is None:
                continue
            try:
                await task
            except Exception:
                pass

    async def create_job(
        self,
        *,
        targets: list[ProbeTargetSpec],
        db: Any = None,
        session_manager: Any = None,
        source: str = "manual_probe",
    ) -> dict[str, Any]:
        job_id = uuid.uuid4().hex
        job = ProbeJob(
            job_id=job_id,
            targets=[
                ProbeTargetSnapshot(
                    name=t.name,
                    model=t.cache_model,
                    base_url=t.base_url,
                )
                for t in targets
            ],
        )
        async with self._jobs_lock:
            self._jobs[job_id] = job
        job.task = asyncio.create_task(
            self._run_job(job, targets=targets, db=db, session_manager=session_manager, source=source)
        )
        return {
            "job_id": job_id,
            "state": job.state,
            "created_at": job.created_at,
            "targets_total": len(job.targets),
        }

    async def get_job_snapshot(self, job_id: str) -> dict[str, Any] | None:
        async with self._jobs_lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            return job.to_dict()

    async def cancel_job(self, job_id: str) -> str | None:
        async with self._jobs_lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
        if job.state in _TERMINAL_JOB_STATES:
            return job.state
        job.cancel_requested = True
        job.state = "cancelling"
        await self._emit(job)
        if job.task is not None and not job.task.done():
            job.task.cancel()
        return "cancelling"

    async def subscribe(self, job_id: str) -> asyncio.Queue[dict[str, Any]] | None:
        async with self._jobs_lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            q: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
            job.subscribers.append(q)
            return q

    async def unsubscribe(self, job_id: str, queue: asyncio.Queue[dict[str, Any]]) -> None:
        async with self._jobs_lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            if queue in job.subscribers:
                job.subscribers.remove(queue)

    async def _emit(self, job: ProbeJob) -> None:
        payload = {"event": "job_update", "data": job.to_dict()}
        subscribers = list(job.subscribers)
        for q in subscribers:
            try:
                q.put_nowait(payload)
            except Exception:
                logger.debug("probe job subscriber queue push failed", exc_info=True)

    async def _run_job(
        self,
        job: ProbeJob,
        *,
        targets: list[ProbeTargetSpec],
        db: Any = None,
        session_manager: Any = None,
        source: str = "manual_probe",
    ) -> None:
        job.state = "running"
        job.started_at = _utc_now_iso()
        await self._emit(job)

        tasks = [
            asyncio.create_task(
                self._run_target(job, targets[i], job.targets[i], db=db, session_manager=session_manager, source=source)
            )
            for i in range(len(targets))
        ]
        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            job.cancel_requested = True
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        except Exception:
            logger.debug("probe job failed unexpectedly", exc_info=True)

        states = [target.state for target in job.targets]
        if job.cancel_requested and all(s in {"cancelled", "queued"} for s in states):
            job.state = "cancelled"
        elif all(s == "succeeded" for s in states):
            job.state = "succeeded"
        elif any(s in {"succeeded", "partial"} for s in states):
            job.state = "partial"
        elif all(s == "cancelled" for s in states):
            job.state = "cancelled"
        else:
            job.state = "failed"

        job.finished_at = _utc_now_iso()
        await self._emit(job)

    async def _run_target(
        self,
        job: ProbeJob,
        spec: ProbeTargetSpec,
        snapshot: ProbeTargetSnapshot,
        *,
        db: Any = None,
        session_manager: Any = None,
        source: str = "manual_probe",
    ) -> None:
        if job.cancel_requested:
            snapshot.state = "cancelled"
            snapshot.finished_at = _utc_now_iso()
            await self._emit(job)
            return

        snapshot.state = "running"
        snapshot.started_at = _utc_now_iso()
        await self._emit(job)

        key = _target_cache_key(spec.cache_model, spec.base_url)
        owner = False
        async with self._target_inflight_lock:
            inflight = self._target_inflight.get(key)
            if inflight is None:
                inflight = asyncio.get_running_loop().create_future()
                self._target_inflight[key] = inflight
                owner = True
            else:
                snapshot.deduplicated = True
                snapshot.state = "waiting"
                await self._emit(job)

        if not owner:
            try:
                caps = await inflight
                snapshot.capabilities = caps.to_dict()
                snapshot.state = _target_state_from_caps(caps)
            except asyncio.CancelledError:
                snapshot.state = "cancelled"
                snapshot.error = "cancelled"
            except Exception as exc:
                snapshot.state = "failed"
                snapshot.error = str(exc)[:200]
            snapshot.finished_at = _utc_now_iso()
            await self._emit(job)
            return

        provider = _provider_key(spec.base_url)
        provider_sem = self._provider_sems.get(provider)
        if provider_sem is None:
            provider_sem = asyncio.Semaphore(self._provider_concurrency)
            self._provider_sems[provider] = provider_sem

        try:
            async with self._global_sem:
                async with provider_sem:
                    if job.cancel_requested:
                        raise asyncio.CancelledError()
                    client = create_client(
                        api_key=spec.api_key,
                        base_url=spec.base_url,
                        protocol=spec.protocol,
                    )

                    async def _stage_callback(stage: str, state: str, payload: dict[str, Any] | None = None) -> None:
                        stage_state = snapshot.stages.setdefault(stage, ProbeStageStatus())
                        payload = payload or {}
                        if state == "running":
                            stage_state.state = "running"
                            if not stage_state.started_at:
                                stage_state.started_at = _utc_now_iso()
                        elif state == "completed":
                            stage_state.state = "completed"
                            stage_state.finished_at = _utc_now_iso()
                            stage_state.value = payload.get("result")
                            err = payload.get("error")
                            if err:
                                stage_state.error = str(err)[:200]
                        elif state == "failed":
                            stage_state.state = "failed"
                            stage_state.finished_at = _utc_now_iso()
                            err = payload.get("error")
                            if err:
                                stage_state.error = str(err)[:200]
                        await self._emit(job)

                    caps = await run_full_probe(
                        client=client,
                        model=spec.api_model,
                        base_url=spec.base_url,
                        skip_if_cached=False,
                        db=db,
                        thinking_mode=spec.thinking_mode,
                        health_timeout=self._health_timeout,
                        tool_timeout=self._tool_timeout,
                        vision_timeout=self._vision_timeout,
                        thinking_total_timeout=self._thinking_total_timeout,
                        thinking_strategy_timeout=self._thinking_strategy_timeout,
                        stage_callback=_stage_callback,
                        source=source,
                    )

                    if session_manager is not None:
                        try:
                            await session_manager.broadcast_model_capabilities(spec.api_model, caps)
                        except Exception:
                            logger.debug("broadcast model capabilities failed", exc_info=True)

                    snapshot.capabilities = caps.to_dict()
                    snapshot.state = _target_state_from_caps(caps)
                    snapshot.finished_at = _utc_now_iso()
                    self._mark_skipped_stages(snapshot)
                    if not inflight.done():
                        inflight.set_result(caps)
                    await self._emit(job)
        except asyncio.CancelledError:
            snapshot.state = "cancelled"
            snapshot.error = "cancelled"
            snapshot.finished_at = _utc_now_iso()
            self._mark_skipped_stages(snapshot)
            if not inflight.done():
                inflight.set_exception(asyncio.CancelledError())
            await self._emit(job)
        except Exception as exc:
            snapshot.state = "failed"
            snapshot.error = str(exc)[:200]
            snapshot.finished_at = _utc_now_iso()
            self._mark_skipped_stages(snapshot)
            if not inflight.done():
                inflight.set_exception(exc)
            await self._emit(job)
        finally:
            async with self._target_inflight_lock:
                current = self._target_inflight.get(key)
                if current is inflight:
                    self._target_inflight.pop(key, None)

    @staticmethod
    def _mark_skipped_stages(snapshot: ProbeTargetSnapshot) -> None:
        for stage in _DEFAULT_STAGES:
            stage_state = snapshot.stages.get(stage)
            if stage_state is None:
                continue
            if stage_state.state == "pending":
                stage_state.state = "skipped"
                stage_state.finished_at = _utc_now_iso()
