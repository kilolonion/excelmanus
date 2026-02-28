"""Agent 核心引擎：Skillpack 路由 + Tool Calling 循环。"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Sequence
from pathlib import Path
import json
import random
import re as _re
from dataclasses import dataclass, field, replace
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Literal

import openai

from excelmanus.approval import AppliedApprovalRecord, ApprovalManager, PendingApproval
from excelmanus.compaction import CompactionManager
from excelmanus.workspace import IsolatedWorkspace, SandboxEnv, WorkspaceTransaction
from excelmanus.providers import create_client
from excelmanus.config import ExcelManusConfig, ModelProfile
from excelmanus.events import EventCallback, EventType, ToolCallEvent
from excelmanus.hooks import (
    HookAgentAction,
    HookCallContext,
    HookDecision,
    HookEvent,
    HookResult,
    SkillHookRunner,
)
from excelmanus.logger import get_logger, log_tool_call
from excelmanus.memory import ConversationMemory, TokenCounter
from excelmanus.plan_mode import (
    parse_plan_markdown,
    utc_now_iso,
)
from excelmanus.interaction import InteractionRegistry, DEFAULT_INTERACTION_TIMEOUT
from excelmanus.question_flow import PendingQuestion, QuestionFlowManager
from excelmanus.skillpacks import (
    SkillMatchResult,
    SkillRouter,
    Skillpack,
    SkillpackManager,
)
from excelmanus.skillpacks.context_builder import build_contexts_with_budget
from excelmanus.subagent import SubagentExecutor, SubagentRegistry, SubagentResult
from excelmanus.task_list import TaskStatus, TaskStore
from excelmanus.tools import focus_tools, task_tools
from excelmanus.tools.introspection_tools import register_introspection_tools
from excelmanus.engine_core.command_handler import CommandHandler
from excelmanus.engine_core.context_builder import ContextBuilder
from excelmanus.engine_core.session_state import SessionState
from excelmanus.engine_core.subagent_orchestrator import SubagentOrchestrator
from excelmanus.engine_core.llm_caller import LLMCaller
from excelmanus.engine_core.interaction_handler import InteractionHandler
from excelmanus.engine_core.meta_tools import MetaToolBuilder
from excelmanus.engine_core.skill_resolver import SkillResolver
from excelmanus.engine_core.tool_dispatcher import ToolDispatcher
from excelmanus.mentions.parser import MentionParser, ResolvedMention
from excelmanus.mcp.manager import MCPManager, parse_tool_prefix
from excelmanus.tools.registry import ToolNotAllowedError
from excelmanus.window_perception import (
    AdvisorContext,
    LifecyclePlan,
    PerceptionBudget,
    WindowPerceptionManager,
)
from excelmanus.window_perception.domain import Window
from excelmanus.window_perception.small_model import build_advisor_messages, parse_small_model_plan
from excelmanus.engine_types import (  # noqa: F401 — re-export for backwards compat
    ThinkingConfig,
    ToolCallResult,
    TurnDiagnostic,
    ChatResult,
    DelegateSubagentOutcome,
    _AuditedExecutionError,
    _ToolCallBatch,
    ApprovalResolver,
    QuestionResolver,
    _EFFORT_RATIOS,
    _EFFORT_TO_GEMINI_LEVEL,
    _EFFORT_TO_OPENAI,
)
from excelmanus.engine_utils import (  # noqa: F401 — re-export for backwards compat
    _ALWAYS_AVAILABLE_TOOLS_READONLY,
    _ALWAYS_AVAILABLE_TOOLS_WRITE_ONLY,
    _ALWAYS_AVAILABLE_TOOLS_SET,
    _ALWAYS_AVAILABLE_TOOLS_READONLY_SET,
    _SYSTEM_Q_SUBAGENT_APPROVAL,
    _SUBAGENT_APPROVAL_OPTION_ACCEPT,
    _SUBAGENT_APPROVAL_OPTION_FULLACCESS_RETRY,
    _SUBAGENT_APPROVAL_OPTION_REJECT,
    _WINDOW_ADVISOR_RETRY_DELAY_MIN_SECONDS,
    _WINDOW_ADVISOR_RETRY_DELAY_MAX_SECONDS,
    _WINDOW_ADVISOR_RETRY_AFTER_CAP_SECONDS,
    _WINDOW_ADVISOR_RETRY_TIMEOUT_CAP_SECONDS,
    _VALID_WRITE_HINTS,
    _MID_DISCUSSION_MAX_LEN,
    _SKILL_AGENT_ALIASES,
    _WRITE_EFFECT_VALUES,
    _MENTION_XML_TAG_MAP,
    _normalize_write_hint,
    _merge_write_hint,
    _merge_write_hint_with_override,
    build_mention_context_block,
    _message_content_to_text,
    _normalize_tool_calls,
    _coerce_completion_message,
    _extract_completion_message,
    _usage_token,
    _extract_cached_tokens,
    _extract_anthropic_cache_tokens,
    _extract_ttft_ms,
    _looks_like_html_document,
    _CLARIFICATION_PATTERNS,
    _MIN_QUESTION_MARKS_FOR_CLARIFICATION,
    _looks_like_clarification,
    _WAITING_FOR_USER_ACTION_PATTERNS,
    _looks_like_waiting_for_user_action,
    _FORMULA_ADVICE_PATTERN,
    _FORMULA_ADVICE_FALLBACK_PATTERN,
    _VBA_MACRO_ADVICE_PATTERN,
    _USER_VBA_REQUEST_PATTERN,
    _user_requests_vba,
    _contains_formula_advice,
    _WRITE_ACTION_VERBS,
    _FILE_REFERENCE_PATTERN,
    _detect_write_intent,
    _summarize_text,
    _split_tool_call_batches,
)

if TYPE_CHECKING:
    from excelmanus.database import Database
    from excelmanus.persistent_memory import PersistentMemory
    from excelmanus.memory_extractor import MemoryExtractor

logger = get_logger("engine")

from excelmanus.message_serialization import to_plain as _to_plain, assistant_message_to_dict as _assistant_message_to_dict  # noqa: E402


class AgentEngine:
    """核心代理引擎，驱动 LLM 与工具之间的 Tool Calling 循环。"""

    # auto 模式系统消息兼容性探测结果（key-based 缓存，按 model+base_url 隔离）
    _system_mode_fallback_cache: dict[tuple[str, str], str] = {}

    def __init__(
        self,
        config: ExcelManusConfig,
        registry: Any,
        skill_router: SkillRouter | None = None,
        persistent_memory: PersistentMemory | None = None,
        memory_extractor: MemoryExtractor | None = None,
        mcp_manager: MCPManager | None = None,
        own_mcp_manager: bool = True,
        database: "Database | None" = None,
        shared_backup_path_map: dict[str, str] | None = None,
        workspace: IsolatedWorkspace | None = None,
        user_id: str | None = None,
    ) -> None:
        # ── 核心组件初始化（必须在所有 property 代理字段赋值之前）──
        self._user_id = user_id
        self._session_id: str | None = None
        self._history_snapshot_index: int = 0
        self._state = SessionState()
        self._client = create_client(
            api_key=config.api_key,
            base_url=config.base_url,
            protocol=config.protocol,
        )
        # AUX：统一用于路由小模型 + 窗口感知顾问（未配置 aux_model 或 aux_enabled=False 时回退主模型）
        _aux_effective = config.aux_enabled and bool(config.aux_model)
        _aux_api_key = config.aux_api_key or config.api_key
        _aux_base_url = config.aux_base_url or config.base_url
        _aux_protocol = config.aux_protocol if _aux_effective else config.protocol
        # 路由子代理：aux_model 已配置且启用则固定 aux；否则跟随主模型
        if _aux_effective:
            self._router_client = create_client(
                api_key=_aux_api_key,
                base_url=_aux_base_url,
                protocol=_aux_protocol,
            )
            self._router_model = config.aux_model
            self._router_follow_active_model = False
        else:
            self._router_client = self._client
            self._router_model = config.model
            self._router_follow_active_model = True
        # 窗口感知顾问小模型：aux 启用时用 aux_*，否则回退主模型
        _adv_api_key = _aux_api_key if _aux_effective else config.api_key
        _adv_base_url = _aux_base_url if _aux_effective else config.base_url
        _adv_model = (config.aux_model if _aux_effective else None) or config.model
        _adv_protocol = _aux_protocol if _aux_effective else config.protocol
        # 始终创建独立 client，避免与 _client 共享对象导致测试 mock 互相干扰
        self._advisor_client = create_client(
            api_key=_adv_api_key,
            base_url=_adv_base_url,
            protocol=_adv_protocol,
        )
        self._advisor_model = _adv_model
        # adviser 是否跟随主模型切换：仅当未配置辅助模型时
        self._advisor_follow_active_model = not _aux_effective
        # VLM 独立客户端：vlm_enabled=False 时回退到主模型
        _vlm_effective = config.vlm_enabled
        _vlm_api_key = (config.vlm_api_key if _vlm_effective else None) or config.api_key
        _vlm_base_url = (config.vlm_base_url if _vlm_effective else None) or config.base_url
        _vlm_model = (config.vlm_model if _vlm_effective else None) or config.model
        _vlm_protocol = config.vlm_protocol if _vlm_effective else config.protocol
        if _vlm_effective and config.vlm_base_url:
            self._vlm_client = create_client(
                api_key=_vlm_api_key,
                base_url=_vlm_base_url,
                protocol=_vlm_protocol,
            )
        else:
            self._vlm_client = self._client
        self._vlm_model = _vlm_model
        self._config = config
        # ── 视觉能力推断 ──
        self._is_vision_capable = self._infer_vision_capable(config, database)
        # B 通道可用条件：
        #   1. vlm_enhance 总开关开启
        #   2. 有独立 VLM 端点（vlm_base_url 且 vlm_enabled），或主模型本身有视觉能力可兼作 VLM
        _has_independent_vlm = bool(_vlm_effective and config.vlm_base_url)
        self._vlm_enhance_available = (
            config.vlm_enhance
            and (_has_independent_vlm or self._is_vision_capable)
        )
        if config.vlm_enhance and not self._vlm_enhance_available:
            logger.info("VLM 增强已开启但未配置独立 VLM 且主模型无视觉能力，B 通道不可用")
        if config.vlm_model and not config.vlm_base_url:
            logger.warning(
                "已设置 vlm_model=%s 但未设置 vlm_base_url，"
                "VLM 调用将回退到主模型端点（模型名可能不兼容）",
                config.vlm_model,
            )
        logger.info(
            "视觉模式: main_vision=%s, vlm_enhance=%s",
            self._is_vision_capable, self._vlm_enhance_available,
        )
        # ── 首次使用关键词推断时，自动触发后台 probe 以获取 ground truth ──
        if config.main_model_vision == "auto" and database is not None:
            try:
                from excelmanus.model_probe import load_capabilities
                _cached = load_capabilities(database, config.model, config.base_url)
                if _cached is None or _cached.supports_vision is None:
                    self._schedule_background_probe(config, database)
            except Exception:
                pass
        # fork 出 per-session registry，避免多会话共享同一实例时
        # 会话级工具（task_tools / skill_tools）重复注册抛出 ToolRegistryError
        self._registry = registry.fork() if hasattr(registry, "fork") else registry
        if hasattr(self._registry, "configure_schema_validation"):
            try:
                self._registry.configure_schema_validation(
                    mode=config.tool_schema_validation_mode,
                    canary_percent=config.tool_schema_validation_canary_percent,
                    strict_path=config.tool_schema_strict_path,
                )
            except Exception:
                logger.warning("工具 schema 校验配置注入失败，已回退默认策略", exc_info=True)
        self._skill_router = skill_router
        self._skillpack_manager = (
            SkillpackManager(config, skill_router._loader)
            if skill_router is not None
            else None
        )
        self._memory = ConversationMemory(config)
        # 运行时变量注入系统提示词
        resolved_root = str(Path(config.workspace_root).resolve())
        _runtime_vars = {
            "workspace_root": resolved_root,
        }
        for _var_key, _var_val in _runtime_vars.items():
            self._memory.system_prompt = self._memory.system_prompt.replace(
                f"{{{_var_key}}}", _var_val
            )
        # ── 动态能力图谱注入 ──────────────────────────────────
        try:
            from excelmanus.introspection.capability_map import CapabilityMapGenerator
            _cap_gen = CapabilityMapGenerator(registry=self._registry)
            self._capability_map_text = _cap_gen.generate()
            self._memory.system_prompt = self._memory.system_prompt.replace(
                "{auto_generated_capability_map}", self._capability_map_text
            )
        except Exception:
            logger.debug("能力图谱生成失败，使用占位符", exc_info=True)
            self._capability_map_text = ""
            # 移除未替换的占位符，避免 LLM 看到原始模板标记
            self._memory.system_prompt = self._memory.system_prompt.replace(
                "{auto_generated_capability_map}", ""
            )
        self._last_route_result = SkillMatchResult(
            skills_used=[],
            route_mode="all_tools",
            system_contexts=[],
        )
        # 任务清单存储：单会话内存级，闭包注入避免全局状态污染
        self._task_store = TaskStore()
        self._registry.register_tools(task_tools.get_tools(self._task_store))
        # 计划文档工具：绑定 TaskStore + workspace，write_plan 一次调用生成文档+TaskList
        from excelmanus.tools import plan_tools
        self._registry.register_tools(
            plan_tools.get_tools(self._task_store, config.workspace_root)
        )
        # U1 修复：注册 introspect_capability 工具
        register_introspection_tools(self._registry)
        # 会话级权限控制：默认限制代码 Skillpack，显式 /fullaccess 后解锁
        self._full_access_enabled: bool = False
        # 会话级子代理开关：初始化继承配置，可通过 /subagent 动态切换
        self._subagent_enabled: bool = config.subagent_enabled
        self._subagent_registry = SubagentRegistry(config)
        self._restricted_code_skillpacks: set[str] = {"excel_code_runner"}
        # 会话级 skill 累积：记录本会话已加载过的 skill 名称及其最后激活轮次
        self._loaded_skill_names: dict[str, int] = {}
        # 当前激活技能列表：末尾为主 skill，空列表表示未激活
        self._active_skills: list[Skillpack] = []
        # ── 工具 schema 缓存（同 turn 内 write_hint/skill 集合不变则复用）──
        self._tools_cache: list[dict[str, Any]] | None = None
        self._tools_cache_key: tuple[str, str, frozenset[str], bool] | None = None
        _cache_key = (config.model, config.base_url)
        self._system_mode_cache_key = _cache_key
        self._system_mode_fallback: str | None = type(self)._system_mode_fallback_cache.get(_cache_key)
        # ── 状态变量由 self._state 统一管理 ──
        # self._state 在 __init__ 顶部初始化，以下属性通过 @property 代理访问：
        # _session_turn, _last_iteration_count, _last_tool_call_count,
        # _last_success_count, _last_failure_count, _current_write_hint,
        # _has_write_tool_call, _turn_diagnostics, _session_diagnostics,
        # _execution_guard_fired, _vba_exempt
        self._subagent_orchestrator: SubagentOrchestrator | None = None  # 延迟初始化（需要 self）
        self._tool_dispatcher: ToolDispatcher | None = None  # 延迟初始化（需要 registry fork）
        self._approval = ApprovalManager(config.workspace_root, database=database)
        # ── IsolatedWorkspace + 事务层 ──────────────────────
        if workspace is not None:
            self._workspace = workspace
        else:
            self._workspace = IsolatedWorkspace(
                root_dir=config.workspace_root,
                transaction_enabled=config.backup_enabled,
            )
        # ── FileRegistry（元数据 + 版本管理统一接口）────
        self._file_registry: Any = None
        if database is not None:
            try:
                from excelmanus.file_registry import FileRegistry
                self._file_registry = FileRegistry(
                    database, self._config.workspace_root, enable_versions=True,
                )
            except Exception:
                logger.debug("FileRegistry 初始化失败", exc_info=True)
        self._transaction: WorkspaceTransaction | None = None
        if self._workspace.transaction_enabled:
            if self._file_registry is not None and self._file_registry.has_versions:
                self._transaction = self._workspace.create_transaction(
                    registry=self._file_registry,
                )
            else:
                logger.warning(
                    "备份沙盒已禁用：FileRegistry 不可用或未启用版本管理。",
                )
                self._workspace.transaction_enabled = False
        # 将 registry 共享给 ApprovalManager / SessionState
        self._approval._file_registry = self._file_registry
        self._state._file_registry = self._file_registry
        self._sandbox_env: SandboxEnv = self._workspace.create_sandbox_env(
            transaction=self._transaction,
        )
        # 会话级 FileAccessGuard，绑定到当前引擎的工作区根目录。
        from excelmanus.security import FileAccessGuard as _FAG
        self._file_access_guard = _FAG(str(self._workspace.root_dir))
        self._subagent_executor = SubagentExecutor(
            parent_config=config,
            parent_registry=registry,
            approval_manager=self._approval,
        )
        self._hook_runner = SkillHookRunner(config)
        self._transient_hook_contexts: list[str] = []
        self._hook_started_skills: set[str] = set()
        self._hook_agent_action_depth: int = 0
        self._question_flow = QuestionFlowManager(max_queue_size=8)
        self._system_question_actions: dict[str, dict[str, Any]] = {}
        self._batch_answers: dict[str, list[dict[str, Any]]] = {}
        self._pending_question_route_result: SkillMatchResult | None = None
        self._pending_approval_route_result: SkillMatchResult | None = None
        self._pending_approval_tool_call_id: str | None = None
        self._interaction_registry = InteractionRegistry()
        self._question_resolver: QuestionResolver | None = None
        self._checkpoint_enabled: bool = config.checkpoint_enabled
        self._turn_dirty_files: set[str] = set()  # 当前轮次被写的文件路径
        self._bench_mode: bool = False
        # PlanInterceptHandler 需要此属性（尽管 plan 模式已废弃，handler 仍可能被调用）
        self._plan_intercept_task_create: bool = False
        self._mention_contexts: list[ResolvedMention] | None = None
        self._current_chat_mode: str = "write"
        self._window_perception = WindowPerceptionManager(
            enabled=config.window_perception_enabled,
            budget=PerceptionBudget(
                system_budget_tokens=config.window_perception_system_budget_tokens,
                tool_append_tokens=config.window_perception_tool_append_tokens,
                max_windows=config.window_perception_max_windows,
                default_rows=config.window_perception_default_rows,
                default_cols=config.window_perception_default_cols,
                minimized_tokens=config.window_perception_minimized_tokens,
                background_after_idle=config.window_perception_background_after_idle,
                suspend_after_idle=config.window_perception_suspend_after_idle,
                terminate_after_idle=config.window_perception_terminate_after_idle,
                window_full_max_rows=config.window_full_max_rows,
                window_full_total_budget_tokens=config.window_full_total_budget_tokens,
                window_data_buffer_max_rows=config.window_data_buffer_max_rows,
            ),
            adaptive_model_mode_overrides=dict(config.adaptive_model_mode_overrides or {}),
            advisor_mode=(
                "rules"
                if config.window_perception_advisor_mode == "rules"
                else "hybrid"
            ),
            advisor_trigger_window_count=config.window_perception_advisor_trigger_window_count,
            advisor_trigger_turn=config.window_perception_advisor_trigger_turn,
            advisor_plan_ttl_turns=config.window_perception_advisor_plan_ttl_turns,
            intent_enabled=config.window_intent_enabled,
            intent_sticky_turns=config.window_intent_sticky_turns,
            intent_repeat_warn_threshold=config.window_intent_repeat_warn_threshold,
            intent_repeat_trip_threshold=config.window_intent_repeat_trip_threshold,
        )
        self._window_perception.bind_async_advisor_runner(
            lambda *a, **kw: self._llm_caller.run_window_perception_advisor_async(*a, **kw)
        )
        focus_tools.init_focus_manager(
            manager=self._window_perception,
            refill_reader=lambda **kw: self._context_builder._focus_window_refill_reader(**kw),
        )

        # ── 上下文自动压缩（Compaction）──────────────────────
        self._compaction_manager = CompactionManager(config)

        # ── PromptComposer 集成 ─────────────────────────────
        self._prompt_composer: Any = None
        try:
            from excelmanus.prompt_composer import PromptComposer as _PC
            _prompts_dir = Path(__file__).resolve().parent / "prompts"
            if _prompts_dir.is_dir():
                self._prompt_composer = _PC(_prompts_dir)
                self._prompt_composer.load_all()
        except Exception:
            logger.debug("PromptComposer 初始化失败，策略注入不可用", exc_info=True)

        # ── FileRegistry（工作区文件注册表） ─────────────
        self._database = database
        self._llm_call_store: Any = None  # 类型：LLMCallStore | None
        self._checkpoint_store: Any = None  # 类型：SessionStateStore | None
        if database is not None:
            try:
                from excelmanus.stores.llm_call_store import LLMCallStore as _LCS
                self._llm_call_store = _LCS(database, user_id=user_id)
            except Exception:
                logger.debug("LLM 调用日志初始化失败", exc_info=True)
            try:
                from excelmanus.stores.session_state_store import SessionStateStore as _SSS
                self._checkpoint_store = _SSS(database)
            except Exception:
                logger.debug("SessionStateStore 初始化失败", exc_info=True)
        # 仅保留 FileRegistry scan 相关状态
        self._registry_scan_task: asyncio.Task[Any] | None = None
        self._registry_scan_done: bool = False
        self._registry_scan_error: str | None = None
        self._registry_refresh_needed: bool = False

        # ── 持久记忆集成 ────────────────────────
        self._persistent_memory = persistent_memory
        self._memory_extractor = memory_extractor
        # 语义记忆增强层（延迟初始化，待首轮 chat 时异步同步索引）
        self._semantic_memory: Any = None  # 类型：SemanticMemory | None
        self._embedding_client: Any = None  # 类型：EmbeddingClient | None
        if persistent_memory is not None and config.embedding_enabled:
            try:
                from excelmanus.embedding.client import EmbeddingClient
                from excelmanus.embedding.semantic_memory import SemanticMemory
                _emb_openai_client = openai.AsyncOpenAI(
                    api_key=config.embedding_api_key or config.api_key,
                    base_url=config.embedding_base_url or config.base_url,
                )
                self._embedding_client = EmbeddingClient(
                    client=_emb_openai_client,
                    model=config.embedding_model,
                    dimensions=config.embedding_dimensions,
                    timeout_seconds=config.embedding_timeout_seconds,
                )
                self._semantic_memory = SemanticMemory(
                    persistent_memory=persistent_memory,
                    embedding_client=self._embedding_client,
                    top_k=config.memory_semantic_top_k,
                    threshold=config.memory_semantic_threshold,
                    fallback_recent=config.memory_semantic_fallback_recent,
                    database=database,
                )
            except Exception:
                logger.debug("语义记忆初始化失败，回退到传统加载", exc_info=True)
                self._semantic_memory = None
                self._embedding_client = None
        # 会话启动时加载核心记忆到 system prompt（同步回退，语义检索在首轮 chat 时异步执行）
        if persistent_memory is not None:
            core_memory = persistent_memory.load_core()
            if core_memory:
                original = self._memory.system_prompt
                self._memory.system_prompt = (
                    f"{original}\n\n## 持久记忆\n{core_memory}"
                )

        # ── 用户自定义规则 ─────────────────────────────────
        self._rules_manager: Any = None  # 类型：RulesManager | None
        try:
            from excelmanus.rules import RulesManager as _RM
            from excelmanus.stores.rules_store import RulesStore as _RS
            _rules_db_store = _RS(database) if database is not None else None
            self._rules_manager = _RM(db_store=_rules_db_store)
        except Exception:
            logger.debug("RulesManager 初始化失败", exc_info=True)

        # ── MCP Client 集成 ──────────────────────────────────
        self._mcp_manager = mcp_manager or MCPManager(config.workspace_root)
        self._own_mcp_manager = own_mcp_manager

        # ── 多模型切换 ──────────────────────────────────
        self._active_model: str = config.model
        self._active_api_key: str = config.api_key
        self._active_base_url: str = config.base_url
        self._active_protocol: str = config.protocol
        self._active_model_name: str | None = None  # 当前激活的 profile name

        # ── 模型能力探测结果（由 API 层或启动时注入） ──
        from excelmanus.model_probe import ModelCapabilities
        self._model_capabilities: ModelCapabilities | None = None
        self._thinking_config = ThinkingConfig(
            effort=config.thinking_effort,
            budget_tokens=config.thinking_budget,
        )

        # ── 解耦组件延迟初始化 ──────────────────────────────
        self._tool_dispatcher = ToolDispatcher(self)
        self._subagent_orchestrator = SubagentOrchestrator(self)
        self._pending_classify_task: asyncio.Task[Any] | None = None  # 后台 LLM 分类任务
        self._command_handler = CommandHandler(self)
        self._context_builder = ContextBuilder(self)
        self._llm_caller = LLMCaller(self)
        self._skill_resolver = SkillResolver(self)
        self._meta_tool_builder = MetaToolBuilder(self)
        self._interaction_handler = InteractionHandler(self)

    def _schedule_background_probe(self, config: "ExcelManusConfig", db: "Database | None") -> None:
        """后台触发 probe 检测主模型视觉能力，结果缓存到 DB 供下次使用。"""
        async def _do_probe() -> None:
            try:
                from excelmanus.model_probe import run_full_probe
                caps = await run_full_probe(
                    client=self._client,
                    model=config.model,
                    base_url=config.base_url,
                    skip_if_cached=True,
                    db=db,
                )
                if caps.supports_vision is not None and caps.supports_vision != self._is_vision_capable:
                    logger.warning(
                        "probe 检测视觉能力与关键词推断不一致: probe=%s, keyword=%s, model=%s。"
                        "已缓存 probe 结果，下次创建 engine 时将使用 probe 结果。",
                        caps.supports_vision, self._is_vision_capable, config.model,
                    )
                else:
                    logger.info("probe 视觉检测完成: model=%s, vision=%s", config.model, caps.supports_vision)
            except Exception:
                logger.debug("后台 probe 检测失败", exc_info=True)

        try:
            loop = asyncio.get_running_loop()
            loop.create_task(_do_probe())
            logger.info("已调度后台 probe 检测: model=%s", config.model)
        except RuntimeError:
            logger.debug("无事件循环，跳过后台 probe")

    @staticmethod
    def _infer_vision_capable(config: "ExcelManusConfig", db: "Database | None" = None) -> bool:
        """推断主模型是否支持视觉输入。

        优先级：手动覆盖 > probe 实际检测结果 > 关键词兜底推断。
        """
        mv = config.main_model_vision
        if mv == "true":
            return True
        if mv == "false":
            return False

        # ── 优先使用 probe 实际检测结果（ground truth）──
        if db is not None:
            try:
                from excelmanus.model_probe import load_capabilities
                caps = load_capabilities(db, config.model, config.base_url)
                if caps is not None and caps.supports_vision is not None:
                    logger.info(
                        "视觉能力来自 probe 检测结果: model=%s, vision=%s",
                        config.model, caps.supports_vision,
                    )
                    return caps.supports_vision
            except Exception:
                logger.debug("加载 probe 视觉检测结果失败，回退到关键词推断", exc_info=True)

        # ── 兜底：根据模型名关键词推断（仅在无 probe 结果时使用）──
        model_lower = config.model.lower()
        _NON_VISION_KEYWORDS = (
            "o1-mini", "o3-mini",
            "amazon.nova-micro", "amazon.nova-sonic",
            "gemini-embedding",
            "llama-3.2-1b", "llama-3.2-3b",
            "step-3.5-flash",
            "mistral-small-3.0", "mistral-small-3.1",
        )
        if any(kw in model_lower for kw in _NON_VISION_KEYWORDS):
            logger.info("视觉能力来自关键词推断 (NON_VISION): model=%s → False", config.model)
            return False

        _VISION_KEYWORDS = (
            "gpt-4o", "gpt-4-turbo", "gpt-4-vision", "gpt-4.1",
            "gpt-5",
            "gpt-image-1",
            "o1", "o3", "o4",
            "grok-2-vision", "grok-4",
            "claude-opus-", "claude-sonnet-", "claude-haiku-",
            "claude-opus-4", "claude-sonnet-4", "claude-haiku-4",
            "gemini",
            "amazon.nova", "nova-lite", "nova-pro", "nova-premier",
            "-vl", "-vision", "-multimodal",
            "qwen-vl", "qwen2-vl", "qwen2.5-vl", "qwen3-vl", "qwen3.5-vl",
            "qwen-omni", "qwen2.5-omni", "qwen3-omni",
            "deepseek-vl",
            "janus-pro",
            "llama-3.2-", "llama3.2-vision",
            "llama-4-", "llama4-",
            "pixtral",
            "ministral-3b", "ministral-8b", "ministral-14b",
            "mistral-small-3", "mistral-medium-3", "mistral-large-3",
            "phi-3-vision", "phi-3.5-vision", "phi-4-multimodal",
            "glm-4v", "glm-4.1v", "glm-4.5v", "glm-4.6v",
            "internvl",
            "minicpm-v",
            "minicpm-o",
            "ernie-4.5-vl", "ernie-vl",
            "command-a-vision",
            "aya-vision",
            "moonshot-v1-vision", "kimi-vl",
            "yi-vl",
            "doubao-1.5-vision", "doubao-1.6-vision", "doubao-vision", "seed1.5-vl", "seed-vl",
            "hunyuan-vision",
            "minimax-vl",
            "step-1v", "step-1.5v", "step-3",
            "step-r1-v-mini", "step-1o-vision", "step-1o-turbo-vision",
            "llava",
        )
        result = any(kw in model_lower for kw in _VISION_KEYWORDS)
        logger.info(
            "视觉能力来自关键词推断 (无 probe 缓存): model=%s → %s",
            config.model, result,
        )
        return result

    # ── Property 代理：所有循环/会话级状态委托给 self._state ──────

    @property
    def _session_turn(self) -> int:
        return self._state.session_turn

    @_session_turn.setter
    def _session_turn(self, value: int) -> None:
        self._state.session_turn = value

    @property
    def _last_iteration_count(self) -> int:
        return self._state.last_iteration_count

    @_last_iteration_count.setter
    def _last_iteration_count(self, value: int) -> None:
        self._state.last_iteration_count = value

    @property
    def _last_tool_call_count(self) -> int:
        return self._state.last_tool_call_count

    @_last_tool_call_count.setter
    def _last_tool_call_count(self, value: int) -> None:
        self._state.last_tool_call_count = value

    @property
    def _last_success_count(self) -> int:
        return self._state.last_success_count

    @_last_success_count.setter
    def _last_success_count(self, value: int) -> None:
        self._state.last_success_count = value

    @property
    def _last_failure_count(self) -> int:
        return self._state.last_failure_count

    @_last_failure_count.setter
    def _last_failure_count(self, value: int) -> None:
        self._state.last_failure_count = value

    @property
    def _current_write_hint(self) -> str:
        return self._state.current_write_hint

    @_current_write_hint.setter
    def _current_write_hint(self, value: str) -> None:
        self._state.current_write_hint = value

    @property
    def _has_write_tool_call(self) -> bool:
        return self._state.has_write_tool_call

    @_has_write_tool_call.setter
    def _has_write_tool_call(self, value: bool) -> None:
        self._state.has_write_tool_call = value

    @property
    def _turn_diagnostics(self) -> list:
        return self._state.turn_diagnostics

    @_turn_diagnostics.setter
    def _turn_diagnostics(self, value: list) -> None:
        self._state.turn_diagnostics = value

    @property
    def _session_diagnostics(self) -> list:
        return self._state.session_diagnostics

    @_session_diagnostics.setter
    def _session_diagnostics(self, value: list) -> None:
        self._state.session_diagnostics = value

    @property
    def _execution_guard_fired(self) -> bool:
        return self._state.execution_guard_fired

    @_execution_guard_fired.setter
    def _execution_guard_fired(self, value: bool) -> None:
        self._state.execution_guard_fired = value

    @property
    def _finish_task_warned(self) -> bool:
        return self._state.finish_task_warned

    @_finish_task_warned.setter
    def _finish_task_warned(self, value: bool) -> None:
        self._state.finish_task_warned = value

    @property
    def _verification_attempt_count(self) -> int:
        return self._state.verification_attempt_count

    @_verification_attempt_count.setter
    def _verification_attempt_count(self, value: int) -> None:
        self._state.verification_attempt_count = value

    @property
    def _vba_exempt(self) -> bool:
        return self._state.vba_exempt

    @_vba_exempt.setter
    def _vba_exempt(self, value: bool) -> None:
        self._state.vba_exempt = value

    def _get_tool_write_effect(self, tool_name: str) -> str:
        """读取工具声明的写入语义；缺失时回退 unknown。"""
        tool = self._registry.get_tool(tool_name)
        effect = getattr(tool, "write_effect", "unknown") if tool is not None else "unknown"
        if not isinstance(effect, str):
            return "unknown"
        normalized = effect.strip().lower()
        if normalized in _WRITE_EFFECT_VALUES:
            return normalized
        return "unknown"

    def _record_workspace_write_action(self) -> None:
        """记录工作区写入：写入态 + registry 刷新标记。"""
        self._state.record_write_action()
        self._registry_refresh_needed = True

    def _record_external_write_action(self) -> None:
        """记录工作区外写入：仅写入态，不触发 registry 刷新。"""
        self._state.record_write_action()

    def _record_write_action(self) -> None:
        """兼容入口：等价于工作区写入记录。"""
        self._record_workspace_write_action()

    def rollback_preview(self, turn_index: int) -> dict:
        """预览回滚到第 turn_index 个用户轮次后会影响的文件变更。

        Returns:
            {turn_index, removed_messages, file_changes: [{path, change_type, before_size, after_size, diff}]}
        """
        # 计算将被移除的消息数
        turns = self._memory.list_user_turns()
        removed_count = 0
        for turn in turns:
            if turn["index"] > turn_index:
                removed_count += 1
        # 还需加上助手消息
        msgs = self._memory.messages
        target_msg_index = None
        for turn in turns:
            if turn["index"] == turn_index:
                target_msg_index = turn["msg_index"]
                break
        if target_msg_index is not None:
            removed_count = len(msgs) - target_msg_index - 1
        else:
            removed_count = 0

        # 收集该轮次之后的 approval 文件变更（仅限当前会话）
        applied = self._approval.list_applied(
            limit=100, session_id=self._session_id,
        )
        file_changes: list[dict] = []
        seen_paths: set[str] = set()
        for record in applied:
            if not record.undoable:
                continue
            if record.session_turn is not None and record.session_turn <= turn_index:
                continue
            for change in record.changes:
                if change.path in seen_paths:
                    continue
                seen_paths.add(change.path)
                # 确定变更类型
                if not change.before_exists and change.after_exists:
                    change_type = "added"
                elif change.before_exists and not change.after_exists:
                    change_type = "deleted"
                else:
                    change_type = "modified"

                diff_text: str | None = None
                if not change.is_binary and change.text_diff_file:
                    diff_path = Path(self._approval.workspace_root) / change.text_diff_file
                    if diff_path.exists():
                        try:
                            raw = diff_path.read_text(encoding="utf-8", errors="replace")
                            # 提取与此文件相关的 diff hunk
                            diff_text = self._extract_file_diff(raw, change.path)
                            if diff_text and len(diff_text) > 3000:
                                diff_text = diff_text[:3000] + "\n... (truncated)"
                        except OSError:
                            pass

                file_changes.append({
                    "path": change.path,
                    "change_type": change_type,
                    "before_size": change.before_size,
                    "after_size": change.after_size,
                    "is_binary": change.is_binary,
                    "diff": diff_text,
                    "tool_name": record.tool_name,
                })

        return {
            "turn_index": turn_index,
            "removed_messages": removed_count,
            "file_changes": file_changes,
        }

    @staticmethod
    def _extract_file_diff(patch_text: str, file_path: str) -> str | None:
        """从 unified diff patch 中提取指定文件的 diff 段落。"""
        lines = patch_text.split("\n")
        result_lines: list[str] = []
        in_target = False
        for line in lines:
            if line.startswith("--- ") or line.startswith("+++ "):
                if file_path in line:
                    in_target = True
                    result_lines.append(line)
                elif in_target and line.startswith("--- "):
                    break
                else:
                    in_target = False
            elif in_target:
                result_lines.append(line)
        return "\n".join(result_lines) if result_lines else patch_text if len(patch_text) < 2000 else None

    def rollback_conversation(
        self,
        turn_index: int,
        *,
        rollback_files: bool = False,
        keep_target: bool = True,
    ) -> dict:
        """回退对话到第 turn_index 个用户轮次。

        Args:
            turn_index: 目标用户轮次索引（0-indexed）。
            rollback_files: 是否同时回滚该轮之后产生的文件变更。
            keep_target: 是否保留目标用户消息。为 False 时连同目标消息
                一起移除（用于编辑重发场景，避免后续 chat() 重复添加）。

        Returns:
            {removed_messages, file_rollback_results, turn_index}
        """
        removed = self._memory.rollback_to_user_turn(turn_index, keep_target=keep_target)

        file_results: list[str] = []
        if rollback_files:
            # 逆序回滚该轮次之后产生的审批记录（newest-first，仅限当前会话）
            applied = self._approval.list_applied(
                limit=100, session_id=self._session_id,
            )
            for record in applied:
                if not record.undoable:
                    continue
                # 仅回滚目标轮次之后的记录；session_turn 未知时保守纳入
                if record.session_turn is not None and record.session_turn <= turn_index:
                    continue
                result = self._approval.undo(record.approval_id)
                file_results.append(result)

        # 重置 session turn 到目标轮次
        self._state.session_turn = turn_index
        self._state.has_write_tool_call = False
        self._state.current_write_hint = "unknown"

        # 清理所有 pending 状态，避免 rollback 后 chat() 误入旧的
        # pending question/approval/plan 处理路径，导致孤立 tool_call_id 400 错误
        self._question_flow.clear()
        self._system_question_actions.clear()
        self._batch_answers.clear()
        self._pending_question_route_result = None
        self._approval.clear_pending()
        self._pending_approval_route_result = None
        self._pending_approval_tool_call_id = None

        return {
            "removed_messages": removed,
            "file_rollback_results": file_results,
            "turn_index": turn_index,
        }

    async def _run_registry_scan(self) -> None:
        """后台执行 FileRegistry 全量扫描 + 自动数据探索。"""
        if self._file_registry is None:
            return
        try:
            await asyncio.to_thread(self._file_registry.scan_workspace)
            self._registry_scan_done = True
            self._registry_scan_error = None
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            self._registry_scan_done = False
            self._registry_scan_error = str(exc) or exc.__class__.__name__
            logger.debug("FileRegistry 后台扫描失败", exc_info=True)
            return

        # R8: 扫描成功后自动执行 Level 0 数据探索
        try:
            await self._auto_explore_after_scan()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.debug("自动数据探索失败，不影响正常使用", exc_info=True)

    async def _auto_explore_after_scan(self) -> None:
        """Registry 扫描完成后，自动调用 inspect_excel_files 生成数据概览。

        条件：FileRegistry 中存在 Excel 文件且尚无 explorer_reports 缓存。
        结果以 EXPLORER_REPORT 格式缓存到 session_state，供 context_builder 注入。
        """
        # 已有缓存则跳过
        if getattr(self._state, "explorer_reports", None):
            return

        # 检查是否有 Excel 文件
        if self._file_registry is None:
            return
        try:
            all_files = self._file_registry.list_all()
        except Exception:
            return
        excel_files = [
            f for f in all_files
            if any(str(getattr(f, "path", f)).lower().endswith(ext)
                   for ext in (".xlsx", ".xlsm", ".xls", ".csv"))
        ]
        if not excel_files:
            return

        # 调用 inspect_excel_files 工具（同步工具，在线程中执行）
        if not hasattr(self, "registry") or not hasattr(self.registry, "call_tool"):
            return
        tool_names = self.registry.get_tool_names()
        if "inspect_excel_files" not in tool_names:
            return

        try:
            raw_result = await asyncio.to_thread(
                self.registry.call_tool,
                "inspect_excel_files",
                {"directory": ".", "max_files": 10, "preview_rows": 0},
            )
            result_text = str(raw_result)
        except Exception:
            logger.debug("自动 inspect_excel_files 调用失败", exc_info=True)
            return

        # 将 inspect 结果转为 EXPLORER_REPORT 格式
        report = self._convert_inspect_to_explorer_report(result_text)
        if report is None:
            return

        if not hasattr(self._state, "explorer_reports") or self._state.explorer_reports is None:
            self._state.explorer_reports = []  # type: ignore[attr-defined]
        self._state.explorer_reports.append(report)  # type: ignore[attr-defined]
        logger.info(
            "R8 自动数据探索完成: %d 个文件, %d 个发现",
            len(report.get("files", [])),
            len(report.get("findings", [])),
        )

    @staticmethod
    def _convert_inspect_to_explorer_report(inspect_result: str) -> dict[str, Any] | None:
        """将 inspect_excel_files 的 JSON 输出转为 EXPLORER_REPORT 格式。"""
        try:
            data = json.loads(inspect_result)
        except (ValueError, json.JSONDecodeError):
            return None

        if not isinstance(data, dict):
            return None

        raw_files = data.get("files", [])
        if not raw_files:
            return None

        report_files: list[dict[str, Any]] = []
        total_rows = 0
        total_sheets = 0
        for f in raw_files:
            path = f.get("path", "")
            sheets_raw = f.get("sheets", [])
            sheets: list[dict[str, Any]] = []
            for s in sheets_raw:
                _rows = s.get("rows") or 0
                _cols = s.get("cols") or 0
                sheet_info: dict[str, Any] = {
                    "name": s.get("name", "?"),
                    "rows": _rows,
                    "cols": _cols,
                    "has_header": bool(s.get("header")),
                }
                sheets.append(sheet_info)
                total_rows += _rows
                total_sheets += 1
            report_files.append({"path": path, "sheets": sheets})

        return {
            "summary": f"工作区共 {len(report_files)} 个 Excel 文件，{total_sheets} 个工作表，约 {total_rows} 行数据",
            "files": report_files,
            "findings": [],
            "recommendation": "",
            "_source": "auto_explore",
        }

    def start_registry_scan(self, *, force: bool = False) -> bool:
        """启动 FileRegistry 后台扫描。"""
        if self._file_registry is None:
            return False
        task = self._registry_scan_task
        if task is not None and not task.done():
            return False
        if not force and self._registry_scan_done:
            return False

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            logger.debug("当前线程无运行中的事件循环，跳过 FileRegistry 扫描")
            return False

        self._registry_scan_error = None
        self._registry_scan_task = loop.create_task(self._run_registry_scan())
        return True

    async def await_registry_scan(self, timeout: float = 3.0) -> bool:
        """等待 FileRegistry 扫描完成。"""
        if self._registry_scan_done:
            return True
        task = self._registry_scan_task
        if task is None or task.done():
            return self._registry_scan_done
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            logger.debug("await FileRegistry scan 超时 (%.1fs)，继续对话", timeout)
            return False
        except Exception:  # noqa: BLE001
            logger.debug("await FileRegistry scan 异常", exc_info=True)
            return False
        return self._registry_scan_done

    @property
    def file_registry(self) -> Any:
        """FileRegistry 实例（只读）。"""
        return self._file_registry

    def registry_scan_status(self) -> dict[str, Any]:
        """返回 FileRegistry 扫描状态。"""
        if self._registry_scan_done:
            reg_count = len(self._file_registry.list_all()) if self._file_registry else 0
            return {
                "state": "ready",
                "total_files": reg_count,
                "scan_duration_ms": None,
                "error": None,
                "registry_files": reg_count,
            }
        task = self._registry_scan_task
        if task is not None and not task.done():
            return {
                "state": "building",
                "total_files": None,
                "scan_duration_ms": None,
                "error": None,
            }
        if self._registry_scan_error:
            return {
                "state": "error",
                "total_files": None,
                "scan_duration_ms": None,
                "error": self._registry_scan_error,
            }
        return {
            "state": "idle",
            "total_files": None,
            "scan_duration_ms": None,
            "error": None,
        }

    async def _cancel_registry_scan(self) -> None:
        """取消进行中的 FileRegistry 扫描任务。"""
        task = self._registry_scan_task
        if task is None or task.done():
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.debug("取消 FileRegistry 扫描任务时发生异常", exc_info=True)
        finally:
            if self._registry_scan_task is task:
                self._registry_scan_task = None

    async def extract_and_save_memory(
        self,
        *,
        trigger: str = "session_end",
        on_event: EventCallback | None = None,
    ) -> list:
        """从对话历史中提取记忆并持久化。

        trigger: "session_end" | "periodic" | "pre_compaction"
        若 MemoryExtractor 或 PersistentMemory 未配置则静默跳过。
        所有异常均被捕获并记录日志，不影响会话正常结束。
        返回提取到的 MemoryEntry 列表（可能为空）。
        """
        if self._memory_extractor is None or self._persistent_memory is None:
            return []
        try:
            messages = self._memory.get_messages()
            entries = await self._memory_extractor.extract(messages)
            if entries:
                self._persistent_memory.save_entries(entries)
                logger.info("持久记忆提取完成 (trigger=%s)，保存了 %d 条记忆条目", trigger, len(entries))
                if self._semantic_memory is not None:
                    try:
                        await self._semantic_memory.index_entries(entries)
                    except Exception:
                        logger.debug("增量向量索引失败", exc_info=True)
                self._emit(
                    on_event,
                    ToolCallEvent(
                        event_type=EventType.MEMORY_EXTRACTED,
                        memory_entries=[
                            {"id": e.id, "content": e.content, "category": e.category.value}
                            for e in entries
                        ],
                        memory_trigger=trigger,
                    ),
                )
            return entries
        except Exception:
            logger.exception("持久记忆提取或保存失败，已跳过")
            return []

    async def initialize_mcp(self) -> None:
        """异步初始化 MCP 连接（需在 event loop 中调用）。

        由 CLI 或 API 入口在启动时显式调用。

        注意：
        MCP 仅负责工具注册；Skill 仅负责策略与授权。
        """
        await self._mcp_manager.initialize(self._registry)
        self.sync_mcp_auto_approve()

    def sync_mcp_auto_approve(self) -> None:
        """将当前 MCP 白名单同步到审批管理器。"""
        auto_approved = self._mcp_manager.auto_approved_tools
        if auto_approved:
            self._approval.register_mcp_auto_approve(auto_approved)

    async def shutdown_mcp(self) -> None:
        """关闭所有 MCP Server 连接，释放资源。"""
        await self._cancel_registry_scan()

        if self._active_skills:
            _primary = self._active_skills[-1]
            self._skill_resolver.run_skill_hook(
                skill=_primary,
                event=HookEvent.STOP,
                payload={"reason": "shutdown_mcp"},
            )
            self._skill_resolver.run_skill_hook(
                skill=_primary,
                event=HookEvent.SESSION_END,
                payload={"reason": "shutdown_mcp"},
            )
        else:
            for skill_name in list(self._hook_started_skills):
                skill = self._skill_resolver.get_loaded_skill(skill_name)
                if skill is None:
                    continue
                self._skill_resolver.run_skill_hook(
                    skill=skill,
                    event=HookEvent.STOP,
                    payload={"reason": "shutdown_mcp"},
                )
                self._skill_resolver.run_skill_hook(
                    skill=skill,
                    event=HookEvent.SESSION_END,
                    payload={"reason": "shutdown_mcp"},
                )
        self._hook_started_skills.clear()
        if self._own_mcp_manager:
            await self._mcp_manager.shutdown()

    def mcp_server_info(self) -> list[dict[str, Any]]:
        """返回 MCP Server 连接状态摘要，供 CLI 展示。"""
        return self._mcp_manager.get_server_info()

    @property
    def mcp_connected_count(self) -> int:
        """已连接的 MCP Server 数量。"""
        return len(self._mcp_manager.connected_servers)

    @property
    def memory(self) -> ConversationMemory:
        """暴露 memory 供外部访问（如测试）。"""
        return self._memory

    @property
    def raw_messages(self) -> list[dict]:
        """内部消息列表引用（不含 system prompt）。

        返回 ConversationMemory 内部列表的直接引用，调用方不应直接修改。
        """
        return self._memory.messages

    def inject_history(self, messages: list[dict]) -> None:
        """注入历史消息（用于会话恢复），不触发截断。"""
        self._memory.inject_messages(messages)

    @property
    def message_snapshot_index(self) -> int:
        """已持久化的消息快照索引。"""
        return self._history_snapshot_index

    def set_message_snapshot_index(self, index: int) -> None:
        """设置已持久化的消息快照索引。"""
        self._history_snapshot_index = index

    # ── Checkpoint 持久化 ──────────────────────────────────────

    def save_checkpoint(self) -> None:
        """保存当前 SessionState + TaskStore 状态到数据库。"""
        if self._checkpoint_store is None or self._session_id is None:
            return
        try:
            self._checkpoint_store.save_checkpoint(
                session_id=self._session_id,
                state_dict=self._state.to_dict(),
                task_list_dict=self._task_store.to_dict(),
                turn_number=self._state.session_turn,
            )
        except Exception:
            logger.debug("save_checkpoint 失败", exc_info=True)

    def restore_checkpoint(self) -> bool:
        """从数据库恢复最新 checkpoint，返回是否成功恢复。"""
        if self._checkpoint_store is None or self._session_id is None:
            return False
        try:
            cp = self._checkpoint_store.load_latest_checkpoint(self._session_id)
            if cp is None:
                return False
            from excelmanus.engine_core.session_state import SessionState
            restored_state = SessionState.from_dict(cp["state_dict"])
            # 保留 _file_registry 引用（不序列化）
            restored_state._file_registry = self._state._file_registry
            self._state = restored_state

            from excelmanus.task_list import TaskStore
            restored_store = TaskStore.from_dict(cp["task_list_dict"])
            # 迁移任务清单到现有 _task_store（保持工具引用有效）
            self._task_store._task_list = restored_store._task_list
            self._task_store._plan_file_path = restored_store._plan_file_path
            logger.info(
                "checkpoint 恢复成功: session=%s turn=%s",
                self._session_id, cp["turn_number"],
            )
            return True
        except Exception:
            logger.debug("restore_checkpoint 失败", exc_info=True)
            return False

    def list_user_turns(self) -> list[dict]:
        """列出所有用户轮次摘要，返回 [{index, content_preview, msg_index}]。"""
        return self._memory.list_user_turns()

    def replace_user_message(self, msg_index: int, content: str) -> None:
        """替换指定位置的消息内容。"""
        self._memory.replace_message_content(msg_index, content)

    @property
    def session_turn(self) -> int:
        """当前会话轮次（公开只读）。"""
        return self._state.session_turn

    @property
    def active_base_url(self) -> str:
        """当前活跃模型的 base_url（只读）。"""
        return self._active_base_url

    def update_aux_config(
        self,
        *,
        aux_enabled: bool = True,
        aux_model: str | None = None,
        aux_api_key: str | None = None,
        aux_base_url: str | None = None,
    ) -> None:
        """热更新 AUX 配置（路由 + 子代理默认模型 + 窗口感知顾问）。

        当前端通过 API 修改 AUX 配置时，由 SessionManager 广播调用，
        确保已存活的引擎实例不会使用过时的 AUX 快照。
        """
        # 使用 replace 创建新 config 实例，保持 frozen 语义
        from dataclasses import replace as _dc_replace
        self._config = _dc_replace(
            self._config,
            aux_enabled=aux_enabled,
            aux_model=aux_model,
            aux_api_key=aux_api_key,
            aux_base_url=aux_base_url,
        )

        # 重建路由 / 窗口感知顾问的 client 与 model
        _aux_effective = aux_enabled and bool(aux_model)
        _aux_api_key = aux_api_key or self._config.api_key
        _aux_base_url = aux_base_url or self._config.base_url
        _aux_protocol = self._config.aux_protocol if _aux_effective else self._active_protocol
        if _aux_effective:
            self._router_client = create_client(
                api_key=_aux_api_key,
                base_url=_aux_base_url,
                protocol=_aux_protocol,
            )
            self._router_model = aux_model
            self._router_follow_active_model = False
        else:
            self._router_client = self._client
            self._router_model = self._active_model
            self._router_follow_active_model = True

        _adv_api_key = _aux_api_key if _aux_effective else self._config.api_key
        _adv_base_url = _aux_base_url if _aux_effective else self._config.base_url
        _adv_model = (aux_model if _aux_effective else None) or self._active_model
        _adv_protocol = _aux_protocol if _aux_effective else self._active_protocol
        self._advisor_client = create_client(
            api_key=_adv_api_key,
            base_url=_adv_base_url,
            protocol=_adv_protocol,
        )
        self._advisor_model = _adv_model
        self._advisor_follow_active_model = not _aux_effective
        logger.info(
            "AUX 配置热更新: enabled=%s, model=%s, base_url=%s",
            aux_enabled,
            aux_model or "(跟随主模型)",
            aux_base_url or "(跟随主模型)",
        )

    def get_compaction_status(self) -> dict[str, Any]:
        """返回上下文压缩状态，供 API 层查询。"""
        return self._compaction_manager.get_status(self._memory, None)

    @property
    def last_route_result(self) -> SkillMatchResult:
        """最近一轮 skill 路由结果。"""
        return self._last_route_result

    @property
    def session_diagnostics(self) -> list[dict[str, Any]]:
        """会话级诊断累积数据，供 /save 导出。"""
        return self._session_diagnostics

    @property
    def prompt_injection_snapshots(self) -> list[dict[str, Any]]:
        """提示词注入完整快照，供 /save 导出。"""
        return self._state.prompt_injection_snapshots

    @property
    def full_access_enabled(self) -> bool:
        """当前会话是否启用 fullaccess。"""
        return self._full_access_enabled

    @property
    def subagent_enabled(self) -> bool:
        """当前会话是否启用 subagent。"""
        return self._subagent_enabled

    @property
    def backup_enabled(self) -> bool:
        """当前会话是否启用备份沙盒模式（事务模式）。"""
        return self._workspace.transaction_enabled

    @backup_enabled.setter
    def backup_enabled(self, value: bool) -> None:
        self._workspace.transaction_enabled = value

    @property
    def checkpoint_enabled(self) -> bool:
        """当前会话是否启用轮次 checkpoint 模式。"""
        return self._checkpoint_enabled

    @checkpoint_enabled.setter
    def checkpoint_enabled(self, value: bool) -> None:
        self._checkpoint_enabled = value

    @property
    def workspace(self) -> IsolatedWorkspace:
        return self._workspace

    @property
    def file_version_manager(self) -> Any:
        """统一文件版本管理器（委托 FileRegistry）。"""
        if self._file_registry is not None and self._file_registry.has_versions:
            return self._file_registry.fvm
        return None

    @property
    def transaction(self) -> WorkspaceTransaction | None:
        return self._transaction

    @transaction.setter
    def transaction(self, value: WorkspaceTransaction | None) -> None:
        self._transaction = value

    @property
    def sandbox_env(self) -> SandboxEnv:
        return self._sandbox_env

    @sandbox_env.setter
    def sandbox_env(self, value: SandboxEnv) -> None:
        self._sandbox_env = value

    # ── Protocol 适配层：公共 property/方法，供 engine_core 子组件通过 Protocol 访问 ──

    @property
    def config(self) -> Any:
        """配置对象（Protocol: EngineConfig / ToolExecutionContext）。"""
        return self._config

    @property
    def registry(self) -> Any:
        """工具注册表（Protocol: ToolExecutionContext）。"""
        return self._registry

    @property
    def approval(self) -> Any:
        """审批管理器（Protocol: ToolExecutionContext）。"""
        return self._approval

    @property
    def state(self) -> Any:
        """会话状态（Protocol: ToolExecutionContext）。"""
        return self._state

    @property
    def file_access_guard(self) -> Any:
        """文件访问守卫（Protocol: ToolExecutionContext）。"""
        return self._file_access_guard

    @property
    def window_perception(self) -> Any:
        """窗口感知管理器（Protocol: ToolExecutionContext）。"""
        return self._window_perception

    @property
    def active_model(self) -> str:
        """当前活跃模型标识符（Protocol: EngineConfig）。"""
        return self._active_model

    @property
    def is_vision_capable(self) -> bool:
        """主模型是否支持视觉（Protocol: VLMContext）。"""
        return self._is_vision_capable

    @property
    def vlm_enhance_available(self) -> bool:
        """VLM 增强是否可用（Protocol: VLMContext）。"""
        return self._vlm_enhance_available

    @property
    def vlm_client(self) -> Any:
        """VLM 客户端（Protocol: VLMContext）。"""
        return self._vlm_client

    @property
    def vlm_model(self) -> str:
        """VLM 模型标识符（Protocol: VLMContext）。"""
        return self._vlm_model

    def emit(self, on_event: Any, event: Any) -> None:
        """发出事件（Protocol: ToolExecutionContext）。"""
        self._emit(on_event, event)

    def record_write_action(self) -> None:
        """记录写入操作（Protocol: ToolExecutionContext）。"""
        self._record_write_action()

    def record_workspace_write_action(self) -> None:
        """记录工作区写入操作（Protocol: ToolExecutionContext）。"""
        self._record_workspace_write_action()

    async def execute_tool_with_audit(self, **kwargs: Any) -> tuple:
        """执行工具并审计（Protocol: ToolExecutionContext）。"""
        return await self._execute_tool_with_audit(**kwargs)

    def format_pending_prompt(self, pending: Any) -> str:
        """格式化待审批提示（Protocol: ToolExecutionContext）。"""
        return self._format_pending_prompt(pending)

    def emit_pending_approval_event(self, **kwargs: Any) -> None:
        """发出待审批事件（Protocol: ToolExecutionContext）。"""
        self._interaction_handler.emit_pending_approval_event(**kwargs)

    def get_tool_write_effect(self, tool_name: str) -> str:
        """获取工具写入效果（Protocol: ToolExecutionContext）。"""
        return self._get_tool_write_effect(tool_name)

    def redirect_backup_paths(self, tool_name: str, arguments: dict) -> dict:
        """重定向备份路径（Protocol: ToolExecutionContext）。"""
        return self._context_builder._redirect_backup_paths(tool_name, arguments)

    def pick_route_skill(self, route_result: Any) -> Any:
        """选择路由技能（Protocol: ToolExecutionContext）。"""
        return self._skill_resolver.pick_route_skill(route_result)

    def run_skill_hook(self, **kwargs: Any) -> Any:
        """运行技能钩子（Protocol: ToolExecutionContext）。"""
        return self._skill_resolver.run_skill_hook(**kwargs)

    async def resolve_hook_result(self, **kwargs: Any) -> Any:
        """解析钩子结果（Protocol: ToolExecutionContext）。"""
        return await self._skill_resolver.resolve_hook_result(**kwargs)

    def render_task_brief(self, task_brief: Any) -> str:
        """渲染任务简报（Protocol: ToolExecutionContext）。"""
        return self._render_task_brief(task_brief)

    async def handle_activate_skill(self, name: str, reason: str = "") -> str:
        """激活技能（Protocol: DelegationContext）。"""
        return await self._handle_activate_skill(name, reason)

    async def delegate_to_subagent(self, *, task: str, agent_name: str | None = None, file_paths: list | None = None, on_event: Any = None) -> Any:
        """委派子代理（Protocol: DelegationContext）。"""
        return await self._delegate_to_subagent(task=task, agent_name=agent_name, file_paths=file_paths, on_event=on_event)

    async def parallel_delegate_to_subagents(self, *, tasks: list, on_event: Any = None) -> Any:
        """并行委派子代理（Protocol: DelegationContext）。"""
        return await self._parallel_delegate_to_subagents(tasks=tasks, on_event=on_event)

    def handle_list_subagents(self) -> str:
        """列出子代理（Protocol: DelegationContext）。"""
        return self._handle_list_subagents()

    def handle_ask_user(self, **kwargs: Any) -> tuple:
        """向用户提问（Protocol: DelegationContext）。"""
        return self._interaction_handler.handle_ask_user(**kwargs)

    def enqueue_subagent_approval_question(self, **kwargs: Any) -> Any:
        """入队子代理审批问题（Protocol: DelegationContext）。"""
        return self._interaction_handler.enqueue_subagent_approval_question(**kwargs)

    async def intercept_task_create_with_plan(self, **kwargs: Any) -> Any:
        """拦截 task_create 生成计划（Protocol: ToolExecutionContext）。

        注意：plan 模式已废弃，此方法返回错误提示。
        """
        # Plan 模式已废弃，直接返回错误
        error_msg = "Plan mode has been deprecated. Please use normal task creation."
        return None, None, error_msg

    def enable_bench_sandbox(self) -> None:
        """启用 benchmark 沙盒模式：解除所有交互式阻塞。

        - fullaccess = True：高风险工具直接执行，不弹确认
        - plan 拦截关闭：task_create 直接执行，不生成待审批计划
        - plan mode 关闭：普通对话不进入仅规划路径
        - subagent 启用：允许委派子代理
        - bench 模式标志：用于 activate_skill 短路非 Excel 类 skill
        """
        self._full_access_enabled = True
        self._subagent_enabled = True
        self._bench_mode = True


    def has_pending_question(self) -> bool:
        """当前会话是否存在待回答问题。"""
        return self._question_flow.has_pending()

    def current_pending_question(self) -> PendingQuestion | None:
        """返回当前待回答问题（队首）。"""
        return self._question_flow.current()

    def is_waiting_multiselect_answer(self) -> bool:
        """是否正在等待多选题回答。"""
        current = self._question_flow.current()
        return bool(current and current.multi_select)

    def has_pending_approval(self) -> bool:
        """当前会话是否存在待确认的高风险操作。"""
        return self._approval.has_pending()

    def current_pending_approval(self) -> "PendingApproval | None":
        """返回当前待确认操作。"""
        return self._approval.pending

    def list_loaded_skillpacks(self) -> list[str]:
        """返回当前已加载的 Skillpack 名称。"""
        if self._skillpack_manager is not None:
            rows = self._skillpack_manager.list_skillpacks()
            return sorted(str(item["name"]) for item in rows)
        if self._skill_router is None:
            return []
        skillpacks = self._skill_router._loader.get_skillpacks()
        if not skillpacks:
            skillpacks = self._skill_router._loader.load_all()
        return sorted(skillpacks.keys())

    def list_skillpack_commands(self) -> list[tuple[str, str]]:
        """返回可用于 CLI 展示的 Skillpack 斜杠命令与参数提示。"""
        if self._skillpack_manager is not None:
            rows = self._skillpack_manager.list_skillpacks()
            commands = [
                (str(item["name"]), str(item.get("argument_hint", "") or ""))
                for item in rows
                if bool(item.get("user_invocable", True))
            ]
            return sorted(commands, key=lambda item: item[0].lower())
        if self._skill_router is None:
            return []
        skillpacks = self._skill_router._loader.get_skillpacks()
        if not skillpacks:
            skillpacks = self._skill_router._loader.load_all()
        commands = [
            (skill.name, skill.argument_hint)
            for skill in skillpacks.values()
            if skill.user_invocable
        ]
        return sorted(commands, key=lambda item: item[0].lower())

    def get_skillpack_argument_hint(self, name: str) -> str:
        """按技能名返回 argument_hint。"""
        if self._skillpack_manager is not None:
            try:
                detail = self._skillpack_manager.get_skillpack(name)
            except Exception:
                return ""
            hint = detail.get("argument_hint")
            return hint if isinstance(hint, str) else ""
        if self._skill_router is None:
            return ""
        skillpacks = self._skill_router._loader.get_skillpacks()
        if not skillpacks:
            skillpacks = self._skill_router._loader.load_all()
        skill = skillpacks.get(name)
        if skill is not None:
            return skill.argument_hint

        lower_name = name.lower()
        for candidate in skillpacks.values():
            if candidate.name.lower() == lower_name:
                return candidate.argument_hint
        return ""

    def list_skillpacks_detail(self) -> list[dict[str, Any]]:
        """返回全部技能详情（按名称排序）。"""
        manager = self._require_skillpack_manager()
        return manager.list_skillpacks()

    def get_skillpack_detail(self, name: str) -> dict[str, Any]:
        """返回指定技能详情。"""
        manager = self._require_skillpack_manager()
        return manager.get_skillpack(name)

    def create_skillpack(
        self,
        name: str,
        payload: dict[str, Any],
        *,
        actor: str,
    ) -> dict[str, Any]:
        """创建 project 层技能。"""
        manager = self._require_skillpack_manager()
        return manager.create_skillpack(name=name, payload=payload, actor=actor)

    def patch_skillpack(
        self,
        name: str,
        payload: dict[str, Any],
        *,
        actor: str,
    ) -> dict[str, Any]:
        """更新 project 层技能。"""
        manager = self._require_skillpack_manager()
        return manager.patch_skillpack(name=name, payload=payload, actor=actor)

    def delete_skillpack(
        self,
        name: str,
        *,
        actor: str,
        reason: str = "",
    ) -> dict[str, Any]:
        """软删除 project 层技能。"""
        manager = self._require_skillpack_manager()
        return manager.delete_skillpack(
            name=name,
            actor=actor,
            reason=reason,
        )

    def import_skillpack(
        self,
        *,
        source: str,
        value: str,
        actor: str,
        overwrite: bool = False,
    ) -> dict[str, Any]:
        """从本地路径导入 SKILL.md 及附属资源（同步）。"""
        manager = self._require_skillpack_manager()
        return manager.import_skillpack(
            source=source, value=value, actor=actor, overwrite=overwrite,
        )

    async def import_skillpack_async(
        self,
        *,
        source: str,
        value: str,
        actor: str,
        overwrite: bool = False,
    ) -> dict[str, Any]:
        """从本地路径或 GitHub URL 导入 SKILL.md（异步）。"""
        manager = self._require_skillpack_manager()
        return await manager.import_skillpack_async(
            source=source, value=value, actor=actor, overwrite=overwrite,
        )

    def _require_skillpack_manager(self) -> SkillpackManager:
        if self._skillpack_manager is None:
            raise RuntimeError("skillpack 管理器不可用。")
        return self._skillpack_manager

    def _emit(self, on_event: EventCallback | None, event: ToolCallEvent) -> None:
        """安全地发出事件，捕获回调异常。"""
        if on_event is None:
            return
        try:
            on_event(event)
        except Exception as exc:
            logger.warning("事件回调异常: %s", exc)

    async def chat(
        self,
        user_message: str,
        on_event: EventCallback | None = None,
        slash_command: str | None = None,
        raw_args: str | None = None,
        mention_contexts: list[ResolvedMention] | None = None,
        images: list[dict[str, Any]] | None = None,
        approval_resolver: ApprovalResolver | None = None,
        question_resolver: QuestionResolver | None = None,
        chat_mode: str = "write",
    ) -> ChatResult:
        """编排层：路由 → 消息管理 → 调用循环 → 返回结果。"""
        self._question_resolver = question_resolver
        normalized_images: list[dict[str, str]] = []
        for item in images or []:
            if not isinstance(item, dict):
                continue
            data = str(item.get("data", "") or "").strip()
            if not data:
                continue
            media_type = str(item.get("media_type", "image/png") or "image/png").strip() or "image/png"
            detail_raw = str(item.get("detail", "auto") or "auto").strip().lower()
            detail = detail_raw if detail_raw in {"auto", "low", "high"} else "auto"
            normalized_images.append({
                "data": data,
                "media_type": media_type,
                "detail": detail,
            })

        if normalized_images:
            logger.info(
                "收到 %d 张图片附件 (media_types=%s, data_lens=%s)",
                len(normalized_images),
                [img["media_type"] for img in normalized_images],
                [len(img["data"]) for img in normalized_images],
            )
            # 前端附件图片 hash 注册到 dispatcher，
            # 后续 read_image 同一文件时可跳过 C 通道重复注入
            from excelmanus.engine_core.tool_dispatcher import _image_content_hash_b64
            for img in normalized_images:
                _h = _image_content_hash_b64(img["data"])
                self._tool_dispatcher._injected_image_hashes.add(_h)
                logger.debug("前端附件 hash 已注册: %s", _h)

        # ── 视觉能力前置检查：主模型不支持视觉且无 VLM 时直接拒绝 ──
        if normalized_images and not self._is_vision_capable and not self._vlm_enhance_available:
            reject_msg = (
                "当前主模型不支持图片识别，且未配置视觉模型（VLM），无法处理图片附件。\n\n"
                "请通过以下任一方式启用图片支持：\n"
                "1. 切换到支持视觉的主模型（如 GPT-4o、Claude Sonnet、Qwen-VL 等），"
                "或设置 `EXCELMANUS_MAIN_MODEL_VISION=true`\n"
                "2. 配置独立视觉模型：设置 `EXCELMANUS_VLM_BASE_URL` 和 `EXCELMANUS_VLM_MODEL`"
            )
            logger.warning(
                "拒绝图片请求: main_vision=%s, vlm_enhance=%s",
                self._is_vision_capable, self._vlm_enhance_available,
            )
            return ChatResult(reply=reject_msg)

        def _add_user_turn_to_memory(text: str) -> None:
            if not normalized_images:
                self._memory.add_user_message(text)
                return

            # 构建包含文本 + 图片的单条多模态用户消息。
            # 将所有内容放在一条消息中可避免连续的用户消息，
            # 否则 Claude 的 API 会拒绝。
            parts: list[dict[str, Any]] = []
            if text:
                parts.append({"type": "text", "text": text})

            if self._is_vision_capable:
                # 主模型支持视觉：直接注入 image_url
                for image in normalized_images:
                    parts.append({
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{image['media_type']};base64,{image['data']}",
                            "detail": image["detail"],
                        },
                    })
            else:
                # 主模型不支持视觉：用文本占位，VLM B 通道会单独处理图片描述
                parts.append({
                    "type": "text",
                    "text": f"[已上传 {len(normalized_images)} 张图片，将由视觉模型分析]",
                })

            self._memory.add_user_message(parts if parts else text)

        # 修复上一次中断（abort / CancelledError）可能遗留的悬空 tool_call
        _repaired = self._memory.repair_dangling_tool_calls()
        if _repaired:
            logger.info("修复了 %d 个中断遗留的悬空 tool_call", _repaired)

        if self._question_flow.has_pending():
            pending_chat_start = time.monotonic()
            pending_result = await self._interaction_handler.handle_pending_question_answer(
                user_message=user_message,
                on_event=on_event,
            )
            if pending_result is not None:
                if pending_result.iterations > 0:
                    elapsed = time.monotonic() - pending_chat_start
                    self._emit(
                        on_event,
                        ToolCallEvent(
                            event_type=EventType.CHAT_SUMMARY,
                            total_iterations=self._last_iteration_count,
                            total_tool_calls=self._last_tool_call_count,
                            success_count=self._last_success_count,
                            failure_count=self._last_failure_count,
                            elapsed_seconds=round(elapsed, 2),
                            prompt_tokens=pending_result.prompt_tokens,
                            completion_tokens=pending_result.completion_tokens,
                            total_tokens=pending_result.total_tokens,
                        ),
                    )
                return pending_result

        control_reply = await self._command_handler.handle(user_message, on_event=on_event)
        if control_reply is not None:
            logger.info("控制命令执行: %s", _summarize_text(user_message))
            return ChatResult(reply=control_reply)

        if self._approval.has_pending():
            self._last_route_result = SkillMatchResult(
                skills_used=[],
                route_mode="control_command",
                system_contexts=[],
            )
            block_msg = self._approval.pending_block_message()
            logger.info("存在待确认项，已阻塞普通请求")
            return ChatResult(reply=block_msg)

        chat_start = time.monotonic()
        # 每次真正的 chat 调用递增轮次计数器
        self._state.increment_turn()
        # 新任务默认重置 write_hint；续跑路径会在 _tool_calling_loop 中恢复。
        self._current_write_hint = "unknown"
        self._current_chat_mode = chat_mode
        self._tools_cache = None  # 新 turn → 失效工具 schema 缓存

        # 发出路由开始事件
        self._emit(
            on_event,
            ToolCallEvent(event_type=EventType.ROUTE_START),
        )
        self._emit(
            on_event,
            ToolCallEvent(
                event_type=EventType.PIPELINE_PROGRESS,
                pipeline_stage="routing",
                pipeline_message="正在分析任务意图...",
            ),
        )

        effective_slash_command = slash_command
        effective_raw_args = raw_args or ""

        # 兼容直接调用 engine.chat("/skill ...") 的旧路径：
        # 若调用方未显式传 slash_command，自动从用户输入中解析。
        if effective_slash_command is None:
            manual_skill_with_args = self._skill_resolver.resolve_skill_command_with_args(user_message)
            if manual_skill_with_args is not None:
                effective_slash_command, effective_raw_args = manual_skill_with_args

        # ── @skill:name 路由：当无斜杠命令时，检测 mention 中的 skill 引用 ──
        if effective_slash_command is None and mention_contexts:
            for rm in mention_contexts:
                if rm.mention.kind == "skill" and not rm.error:
                    effective_slash_command = rm.mention.value
                    # raw_args: 从 clean_text 中提取（即移除所有 @ 标记后的文本）
                    parse_result = MentionParser.parse(user_message)
                    effective_raw_args = parse_result.clean_text
                    break

        # ── 路由（斜杠命令 + chat_mode 映射） ──
        route_result = await self._route_skills(
            user_message,
            slash_command=effective_slash_command,
            raw_args=effective_raw_args if effective_slash_command else None,
            chat_mode=chat_mode,
            on_event=on_event,
            images=normalized_images if normalized_images else None,
        )

        route_result, user_message = await self._adapt_guidance_only_slash_route(
            route_result=route_result,
            user_message=user_message,
            slash_command=effective_slash_command,
            raw_args=effective_raw_args,
        )

        # 合并已激活 skill 的 system_contexts
        # 使用 instructions_only 渲染：完整 resource_contents 已在
        # activate_skill 的 tool result 中返回给 LLM，后续迭代仅需
        # instructions 提醒，避免 resource_contents 在每轮重复注入。
        final_skills_used = list(route_result.skills_used)
        final_system_contexts = list(route_result.system_contexts)
        if self._active_skills:
            for skill in self._active_skills:
                if skill.name not in final_skills_used:
                    final_skills_used.append(skill.name)
                skill_context = skill.render_context_instructions_only()
                if skill_context.strip() and skill_context not in final_system_contexts:
                    final_system_contexts.append(skill_context)

        route_result = SkillMatchResult(
            skills_used=final_skills_used,
            tool_scope=getattr(route_result, "tool_scope", []),
            route_mode=getattr(route_result, "route_mode", "all_tools"),
            system_contexts=final_system_contexts,
            parameterized=route_result.parameterized,
            write_hint=_merge_write_hint(
                getattr(route_result, "write_hint", None),
                self._current_write_hint,
            ),
            sheet_count=getattr(route_result, "sheet_count", 0),
            max_total_rows=getattr(route_result, "max_total_rows", 0),
            task_tags=tuple(getattr(route_result, "task_tags", ()) or ()),
        )
        self._last_route_result = route_result

        # 发出路由结束事件（含匹配结果）
        self._emit(
            on_event,
            ToolCallEvent(
                event_type=EventType.ROUTE_END,
                route_mode=route_result.route_mode,
                skills_used=list(route_result.skills_used),
                tool_scope=list(route_result.tool_scope) if route_result.tool_scope else [],
            ),
        )

        if effective_slash_command and route_result.route_mode == "slash_not_user_invocable":
            reply = f"技能 `{effective_slash_command}` 不允许手动调用。"
            _add_user_turn_to_memory(user_message)
            self._memory.add_assistant_message(reply)
            self._last_iteration_count = 1
            self._last_tool_call_count = 0
            self._last_success_count = 0
            self._last_failure_count = 1
            elapsed = time.monotonic() - chat_start
            self._emit(
                on_event,
                ToolCallEvent(
                    event_type=EventType.CHAT_SUMMARY,
                    total_iterations=self._last_iteration_count,
                    total_tool_calls=self._last_tool_call_count,
                    success_count=self._last_success_count,
                    failure_count=self._last_failure_count,
                    elapsed_seconds=round(elapsed, 2),
                    prompt_tokens=0,
                    completion_tokens=0,
                    total_tokens=0,
                ),
            )
            return ChatResult(
                reply=reply,
                tool_calls=[],
                iterations=1,
                truncated=False,
            )

        if effective_slash_command and route_result.route_mode == "slash_not_found":
            # 区分"技能被权限限制"与"技能真的不存在"，给出精确反馈
            normalized_cmd = SkillResolver.normalize_skill_command_name(effective_slash_command)
            blocked = self._skill_resolver.blocked_skillpacks()
            if blocked and normalized_cmd in blocked:
                reply = (
                    f"技能 `{effective_slash_command}` 当前受访问限制，"
                    f"请先执行 `/fullaccess on` 解除限制后再试。"
                )
            else:
                reply = f"未找到技能 `{effective_slash_command}`，请通过 `/skills` 查看可用技能列表。"
            _add_user_turn_to_memory(user_message)
            self._memory.add_assistant_message(reply)
            self._last_iteration_count = 1
            self._last_tool_call_count = 0
            self._last_success_count = 0
            self._last_failure_count = 1
            elapsed = time.monotonic() - chat_start
            self._emit(
                on_event,
                ToolCallEvent(
                    event_type=EventType.CHAT_SUMMARY,
                    total_iterations=self._last_iteration_count,
                    total_tool_calls=self._last_tool_call_count,
                    success_count=self._last_success_count,
                    failure_count=self._last_failure_count,
                    elapsed_seconds=round(elapsed, 2),
                    prompt_tokens=0,
                    completion_tokens=0,
                    total_tokens=0,
                ),
            )
            return ChatResult(
                reply=reply,
                tool_calls=[],
                iterations=1,
                truncated=False,
            )

        selected_skill = self._skill_resolver.pick_route_skill(route_result)
        if selected_skill is not None:
            user_prompt_hook_raw = self._skill_resolver.run_skill_hook(
                skill=selected_skill,
                event=HookEvent.USER_PROMPT_SUBMIT,
                payload={
                    "user_message": user_message,
                    "slash_command": effective_slash_command or "",
                    "raw_args": effective_raw_args,
                    "route_mode": route_result.route_mode,
                    "skills_used": list(route_result.skills_used),
                },
            )
            user_prompt_hook = await self._skill_resolver.resolve_hook_result(
                event=HookEvent.USER_PROMPT_SUBMIT,
                hook_result=user_prompt_hook_raw,
                on_event=on_event,
            )
            if (
                user_prompt_hook is not None
                and isinstance(user_prompt_hook.updated_input, dict)
            ):
                updated_message = user_prompt_hook.updated_input.get("user_message")
                if isinstance(updated_message, str) and updated_message.strip():
                    user_message = updated_message.strip()
            if user_prompt_hook is not None and user_prompt_hook.decision == HookDecision.DENY:
                reason = user_prompt_hook.reason or "Hook 拒绝了当前请求。"
                reply = f"请求已被 Hook 拦截：{reason}"
                _add_user_turn_to_memory(user_message)
                self._memory.add_assistant_message(reply)
                self._last_iteration_count = 1
                self._last_tool_call_count = 0
                self._last_success_count = 0
                self._last_failure_count = 1
                elapsed = time.monotonic() - chat_start
                self._emit(
                    on_event,
                    ToolCallEvent(
                        event_type=EventType.CHAT_SUMMARY,
                        total_iterations=self._last_iteration_count,
                        total_tool_calls=self._last_tool_call_count,
                        success_count=self._last_success_count,
                        failure_count=self._last_failure_count,
                        elapsed_seconds=round(elapsed, 2),
                        prompt_tokens=0,
                        completion_tokens=0,
                        total_tokens=0,
                    ),
                )
                return ChatResult(
                    reply=reply,
                    tool_calls=[],
                    iterations=1,
                    truncated=False,
                )

        if (
            effective_slash_command
            and route_result.route_mode == "slash_direct"
            and selected_skill is not None
            and selected_skill.command_dispatch == "tool"
            and selected_skill.command_tool
        ):
            _add_user_turn_to_memory(user_message)
            chat_result = await self._run_command_dispatch_skill(
                skill=selected_skill,
                raw_args=effective_raw_args,
                route_result=route_result,
                on_event=on_event,
            )
            elapsed = time.monotonic() - chat_start
            self._emit(
                on_event,
                ToolCallEvent(
                    event_type=EventType.CHAT_SUMMARY,
                    total_iterations=self._last_iteration_count,
                    total_tool_calls=self._last_tool_call_count,
                    success_count=self._last_success_count,
                    failure_count=self._last_failure_count,
                    elapsed_seconds=round(elapsed, 2),
                    prompt_tokens=chat_result.prompt_tokens,
                    completion_tokens=chat_result.completion_tokens,
                    total_tokens=chat_result.total_tokens,
                ),
            )
            return chat_result

        # 追加用户消息
        _add_user_turn_to_memory(user_message)
        logger.info(
            "用户指令摘要: %s | route_mode=%s | skills=%s",
            _summarize_text(user_message),
            route_result.route_mode,
            route_result.skills_used,
        )

        self._context_builder._set_window_perception_turn_hints(
            user_message=user_message,
            is_new_task=True,
            task_tags=route_result.task_tags,
        )
        # 仅新任务重置执行守卫；同任务续跑需保留状态，避免重复注入提示。
        self._execution_guard_fired = False
        self._vba_exempt = _user_requests_vba(user_message)
        # 存储 mention 上下文供 _tool_calling_loop 注入系统提示词
        self._mention_contexts = mention_contexts


        # ── 异步 LLM 分类：已内化到 router._classify_task 同步流程 ──
        self._pending_classify_task = None

        try:
            chat_result = await self._tool_calling_loop(
                route_result, on_event,
                approval_resolver=approval_resolver,
                question_resolver=question_resolver,
            )
        finally:
            pass

        # 注入路由诊断信息到 ChatResult
        chat_result.write_hint = self._current_write_hint
        chat_result.route_mode = route_result.route_mode
        chat_result.skills_used = list(route_result.skills_used)
        chat_result.task_tags = route_result.task_tags
        chat_result.turn_diagnostics = list(self._turn_diagnostics)

        # 累积到会话级诊断
        # 获取本轮提示词注入摘要
        _injection_summary_for_diag: list[dict[str, Any]] = []
        if self._state.prompt_injection_snapshots:
            _latest = self._state.prompt_injection_snapshots[-1]
            if _latest.get("session_turn") == self._session_turn:
                _injection_summary_for_diag = _latest.get("summary", [])
        self._session_diagnostics.append({
            "session_turn": self._session_turn,
            "write_hint": self._current_write_hint,
            "route_mode": route_result.route_mode,
            "skills_used": list(route_result.skills_used),
            "task_tags": list(route_result.task_tags),
            "iterations": chat_result.iterations,
            "prompt_tokens": chat_result.prompt_tokens,
            "completion_tokens": chat_result.completion_tokens,
            "total_tokens": chat_result.total_tokens,
            "write_guard_triggered": chat_result.write_guard_triggered,
            "turn_diagnostics": [d.to_dict() for d in self._turn_diagnostics],
            "prompt_injection_summary": _injection_summary_for_diag,
        })

        # 周期性后台记忆提取：每 N 轮静默提取一次
        _extract_interval = self._config.memory_auto_extract_interval
        if (
            _extract_interval > 0
            and self._session_turn > 0
            and self._session_turn % _extract_interval == 0
        ):
            try:
                await self.extract_and_save_memory(
                    trigger="periodic", on_event=on_event,
                )
            except Exception:
                logger.debug("周期性记忆提取失败，已跳过", exc_info=True)

        # 发出执行摘要事件
        elapsed = time.monotonic() - chat_start
        self._emit(
            on_event,
            ToolCallEvent(
                event_type=EventType.CHAT_SUMMARY,
                total_iterations=self._last_iteration_count,
                total_tool_calls=self._last_tool_call_count,
                success_count=self._last_success_count,
                failure_count=self._last_failure_count,
                elapsed_seconds=round(elapsed, 2),
                prompt_tokens=chat_result.prompt_tokens,
                completion_tokens=chat_result.completion_tokens,
                total_tokens=chat_result.total_tokens,
            ),
        )

        return chat_result

    # ── Skill 解析与 Hook 管理（委托到 SkillResolver）──────────

    def resolve_skill_command(self, user_message: str) -> str | None:
        """将消息中的 `/skill_name ...` 解析为 Skill 名称（公开 API）。"""
        return self._skill_resolver.resolve_skill_command(user_message)

    @property
    def _primary_skill(self) -> Skillpack | None:
        """当前主 skill（列表末尾），无激活时返回 None。"""
        return self._skill_resolver.primary_skill

    async def _handle_activate_skill(self, skill_name: str, reason: str = "") -> str:
        """处理 activate_skill 调用：激活技能并返回技能上下文。"""
        if self._skill_router is None:
            return f"未找到技能: {skill_name}"

        loader = self._skill_router._loader
        skillpacks = loader.get_skillpacks()
        if not skillpacks:
            skillpacks = loader.load_all()

        # 检查是否尝试激活被限制的技能
        # 注意：必须对输入名称做归一化后再比较，防止通过大小写/连字符变体绕过限制
        # 例如 "Excel-Code-Runner" 归一化后与 "excel_code_runner" 相同
        blocked = self._skill_resolver.blocked_skillpacks()
        if blocked:
            normalized_input = SkillResolver.normalize_skill_name(skill_name)
            normalized_blocked = {SkillResolver.normalize_skill_name(b) for b in blocked}
            if normalized_input in normalized_blocked:
                # 从全量技能包中获取描述（尝试精确名和归一化名）
                desc = ""
                skill_obj = skillpacks.get(skill_name)
                if skill_obj is None:
                    # 尝试通过归一化名找到实际技能对象
                    skill_obj = next(
                        (s for k, s in skillpacks.items() if SkillResolver.normalize_skill_name(k) == normalized_input),
                        None,
                    )
                if skill_obj is not None:
                    desc = f"\n该技能用于：{skill_obj.description}"
                return (
                    f"⚠️ 技能 '{skill_name}' 需要 fullaccess 权限才能使用。{desc}\n"
                    f"请告知用户使用 /fullaccess on 命令开启完全访问权限后重试。"
                )

        if not skillpacks:
            return f"未找到技能: {skill_name}"

        selected = self._skill_router._find_skill_by_name(
            skillpacks=skillpacks,
            name=skill_name,
        )
        if selected is None:
            return f"未找到技能: {skill_name}"
        mcp_requirements_error = self._validate_skill_mcp_requirements(selected)
        if mcp_requirements_error:
            return mcp_requirements_error

        self._active_skills = [
            s for s in self._active_skills if s.name != selected.name
        ] + [selected]
        self._loaded_skill_names[selected.name] = self._session_turn
        # 技能集合变化 → 失效工具 schema 缓存
        self._tools_cache = None

        context_text = selected.render_context()
        return f"OK\n{context_text}"

    @staticmethod
    def _normalize_mcp_identifier(name: str) -> str:
        return name.strip().replace("-", "_").lower()

    def _available_mcp_server_set(self) -> set[str]:
        return {
            self._normalize_mcp_identifier(name)
            for name in self._mcp_manager.connected_servers
            if isinstance(name, str) and name.strip()
        }

    def _available_mcp_tool_pairs(self) -> set[tuple[str, str]]:
        pairs: set[tuple[str, str]] = set()
        for tool_name in self._context_builder._all_tool_names():
            if not tool_name.startswith("mcp_"):
                continue
            try:
                server_name, original_tool = parse_tool_prefix(tool_name)
            except ValueError:
                continue
            pairs.add(
                (
                    self._normalize_mcp_identifier(server_name),
                    original_tool.strip().lower(),
                )
            )
        return pairs

    def _validate_skill_mcp_requirements(self, skill: Skillpack) -> str | None:
        required_servers = [
            item.strip()
            for item in skill.required_mcp_servers
            if isinstance(item, str) and item.strip()
        ]
        required_tools = [
            item.strip()
            for item in skill.required_mcp_tools
            if isinstance(item, str) and item.strip()
        ]
        if not required_servers and not required_tools:
            return None

        connected_servers = self._available_mcp_server_set()
        available_tool_pairs = self._available_mcp_tool_pairs()

        missing_servers: list[str] = []
        for server in required_servers:
            normalized = self._normalize_mcp_identifier(server)
            if normalized not in connected_servers:
                missing_servers.append(server)

        missing_tools: list[str] = []
        for token in required_tools:
            server_name, sep, tool_name = token.partition(":")
            if not sep:
                missing_tools.append(token)
                continue
            normalized_server = self._normalize_mcp_identifier(server_name)
            target_tool = tool_name.strip().lower()
            if target_tool == "*":
                matched = any(srv == normalized_server for srv, _ in available_tool_pairs)
            else:
                matched = (normalized_server, target_tool) in available_tool_pairs
            if not matched:
                missing_tools.append(token)

        if not missing_servers and not missing_tools:
            return None

        lines = [f"⚠️ 技能 '{skill.name}' 的 MCP 依赖未满足。"]
        if missing_servers:
            lines.append(f"- 缺少 MCP Server：{', '.join(missing_servers)}")
        if missing_tools:
            lines.append(f"- 缺少 MCP 工具：{', '.join(missing_tools)}")
        lines.append("请先配置并连接对应 MCP（mcp.json）后重试该技能。")
        return "\n".join(lines)

    @staticmethod
    def _normalize_subagent_file_paths(file_paths: list[Any] | None) -> list[str]:
        """规范化 subagent 输入文件路径。委托给 SubagentOrchestrator。"""
        from excelmanus.engine_core.subagent_orchestrator import SubagentOrchestrator
        return SubagentOrchestrator.normalize_file_paths(file_paths)

    def _build_parent_context_summary(self) -> str:
        """构建主会话上下文摘要。"""
        messages = self._memory.get_messages()
        lines: list[str] = []
        for msg in messages[-6:]:
            role = str(msg.get("role", "")).strip()
            content = str(msg.get("content", "")).strip()
            if not content:
                continue
            if role == "user":
                lines.append(f"用户: {content[:200]}")
            elif role == "assistant":
                lines.append(f"助手: {content[:200]}")
        return "\n".join(lines)

    async def _auto_select_subagent(
        self,
        *,
        task: str,
        file_paths: list[str],
    ) -> str:
        """基于关键词规则选择子代理。委托给 SubagentOrchestrator。"""
        return await self._subagent_orchestrator.auto_select_subagent(
            task=task, file_paths=file_paths,
        )

    async def run_subagent(
        self,
        *,
        agent_name: str,
        prompt: str,
        on_event: EventCallback | None = None,
    ) -> SubagentResult:
        """执行指定子代理。"""
        config = self._subagent_registry.get(agent_name)
        if config is None:
            return SubagentResult(
                success=False,
                summary=f"未找到子代理: {agent_name}",
                error=f"SubagentNotFound: {agent_name}",
                subagent_name=agent_name,
                permission_mode="default",
                conversation_id="",
            )

        runtime_api_key = config.api_key or self._active_api_key
        runtime_base_url = config.base_url or self._active_base_url

        # 运行时模型选择：子代理自身 > 全局 aux_model > 当前激活主模型。
        resolved_model = config.model or self._config.aux_model or self._active_model
        # 内置子代理场景：若 aux_model 明确绑定到 AUX 独立端点，
        # 而当前子代理运行端点不同，则优先回退到 active model，避免端点/模型错配。
        if (
            config.model is None
            and self._config.aux_model
            and self._config.aux_base_url
            and self._config.aux_base_url != runtime_base_url
        ):
            resolved_model = self._active_model

        runtime_config = config
        if (
            config.model != resolved_model
            or config.api_key != runtime_api_key
            or config.base_url != runtime_base_url
        ):
            runtime_config = replace(
                config,
                model=resolved_model,
                api_key=runtime_api_key,
                base_url=runtime_base_url,
            )
        parent_context_parts: list[str] = []
        parent_summary = self._build_parent_context_summary()
        if parent_summary:
            parent_context_parts.append(parent_summary)
        window_context = self._context_builder._build_window_perception_notice()
        if window_context:
            parent_context_parts.append(window_context)
        # full 模式：构建主代理级别的丰富上下文
        enriched_contexts: list[str] | None = None
        if runtime_config.capability_mode == "full":
            enriched_contexts = self._build_full_mode_contexts()

        # S1: 构建文件全景 + CoW 路径映射，让子代理知道工作区文件布局
        workspace_context = self._context_builder._build_file_registry_notice()
        # S2: 获取 CoW 映射供子代理工具调用时重定向
        cow_mappings: dict[str, str] = {}
        try:
            if hasattr(self._state, "get_cow_mappings"):
                _cow = self._state.get_cow_mappings()
                if isinstance(_cow, dict):
                    cow_mappings = _cow
        except Exception:
            pass

        _shared_run_kwargs = dict(
            parent_context="\n\n".join(parent_context_parts),
            on_event=on_event,
            full_access_enabled=self._full_access_enabled,
            tool_result_enricher=self._enrich_subagent_tool_result_with_window_perception,
            enriched_contexts=enriched_contexts,
            session_turn=self._state.session_turn,
            workspace_context=workspace_context,
            file_access_guard=self._file_access_guard,
            sandbox_env=self._sandbox_env,
            cow_mappings=cow_mappings,
            workspace_root=self._config.workspace_root,
        )

        result = await self._subagent_executor.run(
            config=runtime_config,
            prompt=prompt,
            **_shared_run_kwargs,
        )
        if self._should_retry_subagent_with_active_model(
            source_config=config,
            attempted_model=resolved_model,
            result=result,
        ):
            retry_config = replace(runtime_config, model=self._active_model)
            logger.warning(
                "%s 子代理模型 %r 不可用，回退 active model %r 重试一次。",
                agent_name,
                resolved_model,
                self._active_model,
            )
            return await self._subagent_executor.run(
                config=retry_config,
                prompt=prompt,
                **_shared_run_kwargs,
            )
        return result

    @staticmethod
    def _is_model_unavailable_error(error_text: str) -> bool:
        lowered = (error_text or "").lower()
        if not lowered:
            return False
        markers = (
            "未配置模型",
            "model not found",
            "model_not_found",
            "not found the model",
            "unknown model",
            "no such model",
            "does not exist",
            "invalid model",
            "unsupported model",
            "model is not available",
            "resource_not_found",
        )
        return any(marker in lowered for marker in markers)

    def _should_retry_subagent_with_active_model(
        self,
        *,
        source_config: Any,
        attempted_model: str,
        result: SubagentResult,
    ) -> bool:
        if source_config.model is not None:
            return False
        if attempted_model == self._active_model:
            return False
        error_text = " ".join(
            part
            for part in [str(result.error or "").strip(), str(result.summary or "").strip()]
            if part
        )
        return self._is_model_unavailable_error(error_text)

    async def _run_finish_verifier_advisory(
        self,
        *,
        report: dict[str, Any] | None,
        summary: str,
        on_event: EventCallback | None = None,
        blocking: bool = False,
    ) -> str | None:
        """任务完成前运行 verifier 子代理。

        blocking=False（advisory）：返回附加提示文本，不阻塞 finish_accepted。
        blocking=True：verdict=fail + confidence=high 时返回以 "BLOCK:" 开头的字符串，
        调用方据此翻转 finish_accepted；其余情况同 advisory。
        任何异常 / verifier 失败均 fail-open（返回 None）。
        """
        if not self._subagent_enabled:
            return None

        verifier_config = self._subagent_registry.get("verifier")
        if verifier_config is None:
            return None

        # 构建验证提示词：包含任务摘要 + 报告内容
        parts: list[str] = ["请验证以下任务是否真正完成："]
        if report and isinstance(report, dict):
            operations = (report.get("operations") or "").strip()
            if operations:
                parts.append(f"操作：{operations}")
            key_findings = (report.get("key_findings") or "").strip()
            if key_findings:
                parts.append(f"关键发现：{key_findings}")
            affected_files = report.get("affected_files")
            if isinstance(affected_files, list) and affected_files:
                parts.append(f"涉及文件：{', '.join(str(f) for f in affected_files)}")
        elif summary.strip():
            parts.append(f"完成摘要：{summary}")
        else:
            return None

        # 注入最近对话上下文帮助 verifier 理解任务
        recent_context = self._build_parent_context_summary()
        if recent_context:
            parts.append(f"会话上下文：{recent_context[:800]}")

        # 注入写入操作日志（供 verifier 精准验证变更而非盲目探索）
        _state = getattr(self, "_state", None)
        if _state is not None:
            write_log = _state.render_write_operations_log()
            if write_log:
                parts.append(write_log)
            # 根据写入操作类型注入针对性验证清单
            playbook = AgentEngine._select_verification_playbook(_state.write_operations_log)
            if playbook:
                parts.append(playbook)

        # 注入任务清单状态（含每步验证结果），帮助 verifier 对照验证条件
        task_list_notice = self._context_builder._build_task_list_status_notice()
        if task_list_notice:
            parts.append(f"任务清单验证记录：\n{task_list_notice}")

        prompt = "\n".join(parts)

        try:
            result = await self.run_subagent(
                agent_name="verifier",
                prompt=prompt,
                on_event=on_event,
            )
        except Exception:  # noqa: BLE001
            logger.debug("verifier advisory 执行异常，fail-open", exc_info=True)
            return None

        if not result.success:
            logger.info("verifier advisory 执行失败: %s", result.error)
            return None

        # 解析 verdict
        verdict_text = result.summary.strip()
        verdict = "unknown"
        confidence = "unknown"
        issues: list[str] = []
        checks: list[str] = []

        try:
            parsed = json.loads(verdict_text)
            if isinstance(parsed, dict):
                verdict = str(parsed.get("verdict", "unknown")).lower()
                confidence = str(parsed.get("confidence", "unknown")).lower()
                issues = parsed.get("issues", [])
                checks = parsed.get("checks", [])
        except (json.JSONDecodeError, TypeError):
            # verifier 未按格式输出，视为 unknown
            pass

        # 发射结构化 VERIFICATION_REPORT 事件（供前端渲染验证卡片）
        if on_event is not None:
            try:
                self.emit(
                    on_event,
                    ToolCallEvent(
                        event_type=EventType.VERIFICATION_REPORT,
                        verification_verdict=verdict,
                        verification_confidence=confidence,
                        verification_checks=[str(c) for c in checks[:10]],
                        verification_issues=[str(i) for i in issues[:10]],
                        verification_mode="blocking" if blocking else "advisory",
                    ),
                )
            except Exception:  # noqa: BLE001
                pass

        if verdict == "pass":
            check_str = "、".join(str(c) for c in checks[:3]) if checks else "基本检查"
            return f"\n\n✅ **验证通过**（{check_str}）"
        elif verdict == "fail":
            issue_str = "、".join(str(i) for i in issues[:3]) if issues else "未知问题"
            if blocking and confidence == "high":
                return f"BLOCK:⚠️ 验证未通过：{issue_str}。请修正后再次调用 finish_task。"
            return f"\n\n⚠️ **验证发现问题**（advisory）：{issue_str}（任务仍标记完成，建议复查）"
        else:
            return f"\n\n🔍 **验证结果不确定**：{verdict_text[:200]}"

    @staticmethod
    def _select_verification_playbook(
        write_ops: list[dict[str, str]],
    ) -> str:
        """根据写入操作日志中的工具类型，选择针对性验证清单注入 verifier prompt。

        返回空字符串表示无需额外清单（verifier.md 中已有通用清单）。
        """
        if not write_ops:
            return ""

        tool_names = {entry.get("tool_name", "") for entry in write_ops}
        has_run_code = "run_code" in tool_names
        has_write_cells = "write_cells" in tool_names
        has_create_sheet = "create_sheet" in tool_names
        has_delete_sheet = "delete_sheet" in tool_names
        has_insert = bool(tool_names & {"insert_rows", "insert_columns"})

        # 检测公式写入（values 中含 = 开头的字符串）
        has_formula = False
        for entry in write_ops:
            summary = entry.get("summary", "")
            if "公式" in summary or "VLOOKUP" in summary.upper() or "formula" in summary.lower():
                has_formula = True
                break

        # 检测跨 sheet 操作（涉及多个不同 sheet）
        sheets = {entry.get("sheet", "") for entry in write_ops if entry.get("sheet")}
        is_cross_sheet = len(sheets) > 1 or has_create_sheet

        sections: list[str] = ["## 针对性验证清单（根据本轮操作自动生成）"]

        if has_formula:
            sections.append(
                "### 公式验证\n"
                "- 用 `run_code` + openpyxl(data_only=False) 回读公式文本，确认公式语法正确\n"
                "- 检查公式引用的 sheet 和范围是否有效（不指向空区域）\n"
                "- 抽样 2-3 个公式单元格，用 data_only=True 读取计算值，判断是否合理"
            )

        if is_cross_sheet:
            sections.append(
                "### 跨表一致性\n"
                "- 验证源表和目标表的行数关系是否符合预期\n"
                "- 对比关键列的值域（如 ID 列）是否一致\n"
                "- 检查新建的 sheet 是否存在且列头正确"
            )

        if has_run_code:
            sections.append(
                "### run_code 写入验证\n"
                "- 用 `read_excel` 或 `run_code`(只读) 检查目标文件的行数和列数\n"
                "- 抽样首行和末行数据，确认写入内容正确\n"
                "- 验证数据类型（数字未变为字符串、日期格式正确）"
            )

        if has_write_cells and not has_formula:
            sections.append(
                "### 数据写入验证\n"
                "- 用 `read_excel` 读取写入范围，确认行列数匹配\n"
                "- 抽检首行和末行的值是否与预期一致\n"
                "- 检查是否有意外的空值或类型错误"
            )

        if has_delete_sheet:
            sections.append(
                "### 删除验证\n"
                "- 用 `list_sheets` 确认目标 sheet 已不存在\n"
                "- 确认其他 sheet 未受影响"
            )

        if has_insert:
            sections.append(
                "### 插入行/列验证\n"
                "- 验证插入后总行数/列数是否正确\n"
                "- 检查插入位置附近的数据是否正确偏移（无覆盖）"
            )

        # 只有标题没有具体清单时返回空
        if len(sections) <= 1:
            return ""

        return "\n\n".join(sections)

    def _build_full_mode_contexts(self) -> list[str]:
        """为 full 模式子代理构建主代理级别的丰富上下文。"""
        contexts: list[str] = []

        # 1. MCP 扩展能力概要
        mcp_notice = self._context_builder._build_mcp_context_notice()
        if mcp_notice:
            contexts.append(mcp_notice)

        # 2. 工具分类索引
        tool_index = self._context_builder._build_tool_index_notice()
        if tool_index:
            contexts.append(tool_index)

        # 3. 权限状态说明
        access_notice = self._context_builder._build_access_notice()
        if access_notice:
            contexts.append(access_notice)

        # 4. 备份模式说明
        backup_notice = self._context_builder._build_backup_notice()
        if backup_notice:
            contexts.append(backup_notice)

        return contexts

    @staticmethod
    def _render_task_brief(brief: dict[str, Any]) -> str:
        """将结构化 task_brief 渲染为 Markdown 格式的任务指令。"""
        title = str(brief.get("title", "")).strip()
        parts: list[str] = [f"## 任务：{title}"]

        background = str(brief.get("background", "")).strip()
        if background:
            parts.append(f"### 背景\n{background}")

        objectives = brief.get("objectives")
        if isinstance(objectives, list) and objectives:
            items = "\n".join(
                f"{i + 1}. {str(obj).strip()}"
                for i, obj in enumerate(objectives)
                if str(obj).strip()
            )
            if items:
                parts.append(f"### 目标\n{items}")

        constraints = brief.get("constraints")
        if isinstance(constraints, list) and constraints:
            items = "\n".join(
                f"- {str(c).strip()}"
                for c in constraints
                if str(c).strip()
            )
            if items:
                parts.append(f"### 约束\n{items}")

        deliverables = brief.get("deliverables")
        if isinstance(deliverables, list) and deliverables:
            items = "\n".join(
                f"- {str(d).strip()}"
                for d in deliverables
                if str(d).strip()
            )
            if items:
                parts.append(f"### 交付物\n{items}")

        return "\n\n".join(parts)

    async def _delegate_to_subagent(
        self,
        *,
        task: str,
        agent_name: str | None = None,
        file_paths: list[Any] | None = None,
        on_event: EventCallback | None = None,
    ) -> DelegateSubagentOutcome:
        """执行 delegate_to_subagent 并返回结构化结果。

        委托给 SubagentOrchestrator 组件。
        """
        return await self._subagent_orchestrator.delegate(
            task=task,
            agent_name=agent_name,
            file_paths=file_paths,
            on_event=on_event,
        )

    # 待办：过渡期残余，待测试迁移后删除
    # 当前调用方：test_pbt_llm_routing.py:477, test_engine.py:2691, engine.py:3233
    async def _handle_delegate_to_subagent(
        self,
        *,
        task: str,
        agent_name: str | None = None,
        file_paths: list[Any] | None = None,
        on_event: EventCallback | None = None,
    ) -> str:
        """处理 delegate_to_subagent 元工具。"""
        outcome = await self._delegate_to_subagent(
            task=task,
            agent_name=agent_name,
            file_paths=file_paths,
            on_event=on_event,
        )
        return outcome.reply

    async def _parallel_delegate_to_subagents(
        self,
        *,
        tasks: list[dict[str, Any]],
        on_event: EventCallback | None = None,
    ) -> "ParallelDelegateOutcome":
        """执行 parallel_delegate 并返回聚合结果。

        委托给 SubagentOrchestrator.delegate_parallel。
        """
        from excelmanus.engine_core.subagent_orchestrator import (
            ParallelDelegateOutcome,
            ParallelDelegateTask,
        )

        parsed_tasks: list[ParallelDelegateTask] = []
        for item in tasks:
            if not isinstance(item, dict):
                return ParallelDelegateOutcome(
                    reply="工具参数错误: tasks 中每个元素必须为对象。",
                    success=False,
                )
            task_text = item.get("task", "")
            if not isinstance(task_text, str) or not task_text.strip():
                return ParallelDelegateOutcome(
                    reply="工具参数错误: 每个子任务的 task 必须为非空字符串。",
                    success=False,
                )
            raw_paths = item.get("file_paths")
            normalized = self._normalize_subagent_file_paths(raw_paths)
            parsed_tasks.append(ParallelDelegateTask(
                task=task_text.strip(),
                agent_name=item.get("agent_name"),
                file_paths=normalized,
            ))

        return await self._subagent_orchestrator.delegate_parallel(
            tasks=parsed_tasks,
            on_event=on_event,
        )

    def _handle_list_subagents(self) -> str:
        """列出可用子代理。"""
        agents = self._subagent_registry.list_all()
        if not agents:
            return "当前没有可用子代理。"
        lines: list[str] = [f"共 {len(agents)} 个可用子代理：\n"]
        for agent in agents:
            lines.append(f"- {agent.name} ({agent.permission_mode})：{agent.description}")
        return "\n".join(lines)

    # ── 问答与审批交互（委托到 InteractionHandler）──────────

    async def handle_ask_user_blocking(self, *, arguments: dict[str, Any], tool_call_id: str, on_event: EventCallback | None, iteration: int) -> str:
        return await self._interaction_handler.handle_ask_user_blocking(arguments=arguments, tool_call_id=tool_call_id, on_event=on_event, iteration=iteration)

    async def await_question_answer(self, pending_q: PendingQuestion) -> Any:
        return await self._interaction_handler.await_question_answer(pending_q)

    @property
    def interaction_registry(self) -> InteractionRegistry:
        return self._interaction_registry

    async def process_subagent_approval_inline(self, **kwargs: Any) -> tuple[str, bool]:
        return await self._interaction_handler.process_subagent_approval_inline(**kwargs)

    async def _tool_calling_loop(
        self,
        route_result: SkillMatchResult,
        on_event: EventCallback | None,
        *,
        start_iteration: int = 1,
        approval_resolver: ApprovalResolver | None = None,
        question_resolver: QuestionResolver | None = None,
    ) -> ChatResult:
        """迭代循环体：LLM 请求 → thinking 提取 → 工具调用遍历 → 熔断检测。"""

        def _finalize_result(**kwargs: Any) -> ChatResult:
            """统一出口：刷新 registry + checkpoint + 自动发射 FILES_CHANGED 事件。"""
            self._try_refresh_registry()
            # 每轮结束保存 checkpoint（SessionState + TaskStore）
            self.save_checkpoint()
            # 自动发射 FILES_CHANGED 事件（替代 finish_task 的 affected_files）
            if self._state.affected_files and on_event is not None:
                from excelmanus.events import EventType, ToolCallEvent
                self.emit(
                    on_event,
                    ToolCallEvent(
                        event_type=EventType.FILES_CHANGED,
                        changed_files=list(self._state.affected_files),
                    ),
                )
            # 注入 Think-Act 推理指标
            _s = self._state
            _total_calls = _s.silent_call_count + _s.reasoned_call_count
            kwargs.setdefault("reasoning_metrics", {
                "silent_call_count": _s.silent_call_count,
                "reasoned_call_count": _s.reasoned_call_count,
                "reasoning_chars_total": _s.reasoning_chars_total,
                "silent_call_rate": round(
                    _s.silent_call_count / max(1, _total_calls), 3,
                ),
            })
            return ChatResult(**kwargs)

        def _handle_finish_exit(
            tc_result: "ToolCallResult",
            tool_call_id: str,
            iteration: int,
        ) -> "ChatResult | None":
            """finish_task 成功接受时的统一退出处理，返回 ChatResult 或 None。"""
            if not (
                tc_result.tool_name == "finish_task"
                and tc_result.success
                and tc_result.finish_accepted
            ):
                return None
            if tool_call_id:
                self._memory.add_tool_result(tool_call_id, tc_result.result)
            self._last_iteration_count = iteration
            self._last_tool_call_count += 1
            self._last_success_count += 1
            reply = tc_result.result
            self._memory.add_assistant_message(reply)
            self._emit(
                on_event,
                ToolCallEvent(
                    event_type=EventType.RETRACT_THINKING,
                    iteration=iteration,
                ),
            )
            logger.info("finish_task 接受，退出循环: %s", _summarize_text(reply))
            return _finalize_result(
                reply=reply,
                tool_calls=list(all_tool_results),
                iterations=iteration,
                truncated=False,
                prompt_tokens=total_prompt_tokens,
                completion_tokens=total_completion_tokens,
                total_tokens=total_prompt_tokens + total_completion_tokens,
            )

        max_iter = self._config.max_iterations
        max_failures = self._config.max_consecutive_failures
        consecutive_failures = 0
        all_tool_results: list[ToolCallResult] = []
        current_route_result = route_result
        # 恢复执行时保留之前的统计，仅首次调用时重置
        if start_iteration <= 1:
            self._state.reset_loop_stats()
        consecutive_text_only: int = 0
        write_hint = _merge_write_hint(
            getattr(current_route_result, "write_hint", None),
            self._current_write_hint,
        )
        # 设置实例属性供 _build_meta_tools 读取
        self._current_write_hint = write_hint
        # token 使用累计
        total_prompt_tokens = 0
        total_completion_tokens = 0
        # 诊断收集
        self._turn_diagnostics = []

        for iteration in range(start_iteration, max_iter + 1):
            self._emit(
                on_event,
                ToolCallEvent(
                    event_type=EventType.ITERATION_START,
                    iteration=iteration,
                ),
            )


            # ── 后台 LLM 分类已内化到 router 同步流程，无需收割 ──

            if iteration == start_iteration:
                # 首轮：给事件循环一个 tick 处理已完成的线程回调，再短暂等待 registry
                await asyncio.sleep(0)
                await self.await_registry_scan(timeout=0.05)
                self._emit(
                    on_event,
                    ToolCallEvent(
                        event_type=EventType.PIPELINE_PROGRESS,
                        pipeline_stage="preparing_context",
                        pipeline_message="正在准备上下文...",
                    ),
                )

            system_prompts, context_error = self._context_builder._prepare_system_prompts_for_request(
                current_route_result.system_contexts,
                route_result=current_route_result,
            )
            if context_error is not None:
                self._last_iteration_count = iteration
                self._last_failure_count += 1
                self._memory.add_assistant_message(context_error)
                logger.warning("系统上下文预算检查失败，终止执行: %s", context_error)
                return _finalize_result(
                    reply=context_error,
                    tool_calls=list(all_tool_results),
                    iterations=iteration,
                    truncated=False,
                    prompt_tokens=total_prompt_tokens,
                    completion_tokens=total_completion_tokens,
                    total_tokens=total_prompt_tokens + total_completion_tokens,
                )

            # 注入 mention 上下文 XML 块到系统提示词
            mention_block = build_mention_context_block(
                getattr(self, "_mention_contexts", None) or [],
            )
            if mention_block:
                system_prompts.append(mention_block)

            # 上下文自动压缩（Compaction）：超阈值时后台静默压缩早期对话，
            # 使用增强的 ExcelManus 场景化摘要提示词，避免硬截断导致重要上下文丢失。
            if iteration > 1:
                _sys_msgs = self._memory.build_system_messages(system_prompts)
                if self._compaction_manager.should_compact(self._memory, _sys_msgs):
                    self._emit(
                        on_event,
                        ToolCallEvent(
                            event_type=EventType.PIPELINE_PROGRESS,
                            pipeline_stage="compacting",
                            pipeline_message="正在压缩上下文...",
                        ),
                    )
                    # 压缩前先提取记忆，避免早期对话被丢弃后信息丢失
                    try:
                        await self.extract_and_save_memory(
                            trigger="pre_compaction", on_event=on_event,
                        )
                    except Exception:
                        logger.debug("压缩前记忆提取失败，继续压缩", exc_info=True)
                    _summary_model = self._config.aux_model or self._active_model
                    try:
                        await self._compaction_manager.auto_compact(
                            memory=self._memory,
                            system_msgs=_sys_msgs,
                            client=self._client,
                            summary_model=_summary_model,
                        )
                    except Exception as _compact_exc:
                        logger.debug("自动 Compaction 异常，跳过: %s", _compact_exc)
                # summarization 作为 compaction 的次级兜底
                elif (
                    self._config.summarization_enabled
                    and self._config.aux_model
                ):
                    _cur_tokens = self._memory._total_tokens_with_system_messages(_sys_msgs)
                    _threshold_ratio = self._config.summarization_threshold_ratio
                    if _cur_tokens > self._config.max_context_tokens * _threshold_ratio:
                        try:
                            await self._memory.summarize_and_trim(
                                threshold=int(self._config.max_context_tokens * (_threshold_ratio - 0.1)),
                                system_msgs=_sys_msgs,
                                client=self._client,
                                summary_model=self._config.aux_model,
                                keep_recent_turns=self._config.summarization_keep_recent_turns,
                            )
                        except Exception as _sum_exc:
                            logger.debug("对话摘要异常，跳过: %s", _sum_exc)

            messages = self._memory.trim_for_request(
                system_prompts=system_prompts,
                max_context_tokens=self._config.max_context_tokens,
            )

            # 分层 schema（core=完整, extended=摘要/已展开=完整）
            _task_tags = tuple(getattr(current_route_result, "task_tags", ()) or ())
            tools = self._meta_tool_builder.build_v5_tools(write_hint=write_hint, task_tags=_task_tags)
            tool_scope = None

            kwargs: dict[str, Any] = {
                "model": self._active_model,
                "messages": messages,
            }
            if tools:
                kwargs["tools"] = tools

            # 注入 thinking 参数（根据探测到的 thinking_type + ThinkingConfig）
            caps = self._model_capabilities
            tc = self._thinking_config
            if caps and caps.supports_thinking and not tc.is_disabled:
                ttype = caps.thinking_type
                budget = tc.effective_budget()
                if ttype == "claude":
                    kwargs["_thinking_enabled"] = True
                    kwargs["_thinking_budget"] = budget
                elif ttype == "gemini":
                    kwargs["_thinking_budget"] = budget
                elif ttype == "gemini_level":
                    kwargs["_thinking_level"] = tc.gemini_level
                elif ttype == "openai_reasoning":
                    kwargs["reasoning_effort"] = tc.openai_effort
                elif ttype == "enable_thinking":
                    extra = kwargs.get("extra_body", {})
                    extra["enable_thinking"] = True
                    extra["thinking_budget"] = budget
                    kwargs["extra_body"] = extra
                elif ttype == "glm_thinking":
                    extra = kwargs.get("extra_body", {})
                    extra["thinking"] = {"type": "enabled"}
                    kwargs["extra_body"] = extra
                elif ttype == "openrouter":
                    extra = kwargs.get("extra_body", {})
                    # OpenRouter 统一接口：同时传 effort 和 max_tokens
                    extra["reasoning"] = {
                        "effort": tc.openai_effort,
                        "max_tokens": budget,
                    }
                    kwargs["extra_body"] = extra
                # "deepseek" → 模型自动输出推理内容，无需额外参数

            # 提示词缓存优化：同一 session_turn 内共享 cache key，
            # 确保 OpenAI 路由到同一缓存机器，最大化系统提示前缀 cache hit。
            if self._config.prompt_cache_key_enabled:
                kwargs["prompt_cache_key"] = f"em_s{self._session_turn}"

            # 尝试流式调用
            if iteration == start_iteration:
                self._emit(
                    on_event,
                    ToolCallEvent(
                        event_type=EventType.PIPELINE_PROGRESS,
                        pipeline_stage="calling_llm",
                        pipeline_message="正在与模型通信...",
                    ),
                )
            else:
                self._emit(
                    on_event,
                    ToolCallEvent(
                        event_type=EventType.PIPELINE_PROGRESS,
                        pipeline_stage="calling_llm",
                        pipeline_message=f"正在与模型通信（第 {iteration} 轮）...",
                    ),
                )
            _llm_start_ts = time.monotonic()
            stream_kwargs = dict(kwargs)
            stream_kwargs["stream"] = True
            if isinstance(self._client, openai.AsyncOpenAI):
                stream_kwargs["stream_options"] = {"include_usage": True}

            try:
                stream_or_response = await self._llm_caller.create_chat_completion_with_system_fallback(stream_kwargs)
                # 检查返回值是否为异步迭代器（支持流式）
                if hasattr(stream_or_response, "__aiter__"):
                    message, usage = await self._llm_caller.consume_stream(
                        stream_or_response, on_event, iteration,
                        _llm_start_ts=_llm_start_ts,
                    )
                else:
                    # provider 不支持 stream，返回了普通 response 对象
                    message, usage = _extract_completion_message(stream_or_response)
            except Exception as stream_exc:
                # 流式调用失败时回退到非流式
                logger.warning("流式调用失败，回退到非流式: %s", stream_exc)
                response = await self._llm_caller.create_chat_completion_with_system_fallback(kwargs)
                message, usage = _extract_completion_message(response)

            tool_calls = _normalize_tool_calls(getattr(message, "tool_calls", None))

            # 图片降级：本轮 LLM 已收到完整 base64，后续轮次降级为文本引用
            self._memory.mark_images_sent()

            # 累计 token 使用量
            if usage is not None:
                total_prompt_tokens += _usage_token(usage, "prompt_tokens")
                total_completion_tokens += _usage_token(usage, "completion_tokens")

            # 提取 thinking 内容（流式模式下已累积到 message.thinking）
            thinking_content = getattr(message, "thinking", None) or ""

            # 仅在流式过程中未发射过 THINKING_DELTA 时，才发射完整 THINKING 事件，
            # 避免前端收到重复的 thinking 块。
            _already_streamed = getattr(message, "_thinking_streamed", False)
            if thinking_content and not _already_streamed:
                self._emit(
                    on_event,
                    ToolCallEvent(
                        event_type=EventType.THINKING,
                        thinking=thinking_content,
                        iteration=iteration,
                    ),
                )

            # ── 收集本轮迭代诊断快照 ──
            iter_prompt = _usage_token(usage, "prompt_tokens") if usage else 0
            iter_completion = _usage_token(usage, "completion_tokens") if usage else 0
            iter_cached = _extract_cached_tokens(usage)
            iter_cache_creation, iter_cache_read = _extract_anthropic_cache_tokens(usage)
            iter_ttft = _extract_ttft_ms(usage)
            diag = TurnDiagnostic(
                iteration=iteration,
                prompt_tokens=iter_prompt,
                completion_tokens=iter_completion,
                cached_tokens=iter_cached,
                cache_creation_input_tokens=iter_cache_creation,
                cache_read_input_tokens=iter_cache_read,
                ttft_ms=iter_ttft,
                thinking_content=thinking_content,
                tool_names=[
                    s.get("function", {}).get("name", "")
                    for s in tools
                    if s.get("function", {}).get("name")
                ] if tools else [],
            )
            self._turn_diagnostics.append(diag)

            # ── LLM 调用审计日志 ──
            if self._llm_call_store is not None:
                try:
                    _llm_latency = (time.monotonic() - _llm_start_ts) * 1000 if _llm_start_ts else 0.0
                    self._llm_call_store.log(
                        session_id=getattr(self, "_session_id", None),
                        turn=self._session_turn,
                        iteration=iteration,
                        model=self._active_model,
                        prompt_tokens=iter_prompt,
                        completion_tokens=iter_completion,
                        cached_tokens=iter_cached,
                        has_tool_calls=bool(tool_calls),
                        thinking_chars=len(thinking_content),
                        stream=True,
                        latency_ms=_llm_latency,
                        ttft_ms=iter_ttft,
                        cache_creation_tokens=iter_cache_creation,
                        cache_read_tokens=iter_cache_read,
                    )
                except Exception:
                    pass

            # ── Prompt Cache 效果日志 ──
            if iter_cache_read > 0 or iter_cache_creation > 0:
                _cache_ratio = (
                    iter_cache_read / max(1, iter_prompt) * 100
                    if iter_prompt > 0 else 0
                )
                logger.info(
                    "Prompt Cache 诊断: iter=%d ttft=%.0fms "
                    "cache_read=%d cache_creation=%d prompt=%d "
                    "cache_hit_ratio=%.1f%% latency=%.0fms",
                    iteration, iter_ttft,
                    iter_cache_read, iter_cache_creation, iter_prompt,
                    _cache_ratio, _llm_latency,
                )
            elif iter_ttft > 0:
                logger.debug(
                    "LLM 诊断: iter=%d ttft=%.0fms prompt=%d latency=%.0fms (no cache)",
                    iteration, iter_ttft, iter_prompt, _llm_latency,
                )

            # 无工具调用 → 纯文本回复处理（含 HTML 检测、执行守卫、写入门禁）
            if not tool_calls:
                text_action, text_result = self._handle_text_reply(
                    message=message,
                    iteration=iteration,
                    start_iteration=start_iteration,
                    max_iter=max_iter,
                    write_hint=write_hint,
                    consecutive_text_only=consecutive_text_only,
                    diag=diag,
                    all_tool_results=all_tool_results,
                    total_prompt_tokens=total_prompt_tokens,
                    total_completion_tokens=total_completion_tokens,
                    _finalize_result=_finalize_result,
                )
                if text_action == "return":
                    return text_result
                if text_action == "continue":
                    consecutive_text_only = text_result  # 回传更新后的计数
                    continue
                # text_action == "impossible" — 不应到达这里

            assistant_msg = _assistant_message_to_dict(message)
            if tool_calls:
                assistant_msg["tool_calls"] = [_to_plain(tc) for tc in tool_calls]
            self._memory.add_assistant_tool_message(assistant_msg)

            # ── Think-Act 推理检测（纯记录，不阻断执行） ──
            _text_content = (getattr(message, "content", None) or "").strip()
            _has_reasoning = bool(_text_content or thinking_content)
            _reasoning_chars = len(_text_content) + len(thinking_content)
            _tc_count = len(tool_calls)
            if _has_reasoning:
                self._state.reasoned_call_count += _tc_count
                self._state.reasoning_chars_total += _reasoning_chars
            else:
                self._state.silent_call_count += _tc_count
            diag.has_reasoning = _has_reasoning
            diag.reasoning_chars = _reasoning_chars
            diag.silent_tool_call_count = 0 if _has_reasoning else _tc_count

            # 遍历工具调用
            _tool_names_in_batch = [
                getattr(getattr(tc, "function", None), "name", "")
                for tc in tool_calls
            ]
            _tool_count = len(tool_calls)
            _tool_label = (
                _tool_names_in_batch[0] if _tool_count == 1
                else f"{_tool_count} 个工具"
            )
            self._emit(
                on_event,
                ToolCallEvent(
                    event_type=EventType.PIPELINE_PROGRESS,
                    pipeline_stage="executing_tools",
                    pipeline_message=f"正在执行 {_tool_label}...",
                ),
            )
            breaker_triggered = False
            breaker_summary = ""
            breaker_skip_error = (
                f"工具未执行：连续 {max_failures} 次工具调用失败，已触发熔断。"
            )
            # ── 批次拆分：相邻只读工具合并为并行批次 ──
            if self._config.parallel_readonly_tools:
                from excelmanus.tools.policy import PARALLELIZABLE_READONLY_TOOLS
                _batches = _split_tool_call_batches(tool_calls, PARALLELIZABLE_READONLY_TOOLS)
            else:
                _batches = [_ToolCallBatch([tc], False) for tc in tool_calls]

            for _batch in _batches:
                # ── breaker / question 跳过逻辑（适用于整个批次） ──
                if breaker_triggered:
                    for tc in _batch.tool_calls:
                        function = getattr(tc, "function", None)
                        tool_name = getattr(function, "name", "")
                        tool_call_id = getattr(tc, "id", "")
                        all_tool_results.append(
                            ToolCallResult(
                                tool_name=tool_name,
                                arguments={},
                                result=breaker_skip_error,
                                success=False,
                                error=breaker_skip_error,
                            )
                        )
                        if tool_call_id:
                            self._memory.add_tool_result(tool_call_id, breaker_skip_error)
                    continue

                if _batch.parallel:
                    # ── 并行路径：只读工具并发执行 ──
                    _parallel_results = await self._execute_tool_calls_parallel(
                        _batch.tool_calls, tool_scope, on_event, iteration,
                        route_result=current_route_result,
                    )
                    for tc, tc_result in _parallel_results:
                        function = getattr(tc, "function", None)
                        tool_name = getattr(function, "name", "")
                        tool_call_id = getattr(tc, "id", "")

                        consecutive_text_only = 0
                        all_tool_results.append(tc_result)

                        # 卡死检测
                        try:
                            _tc_args, _ = self._tool_dispatcher.parse_arguments(
                                getattr(function, "arguments", None)
                            )
                        except Exception:
                            _tc_args = {}
                        self._state.record_tool_call_for_stuck_detection(tool_name, _tc_args)

                        # finish_task 成功接受时退出循环
                        _finish_result = _handle_finish_exit(tc_result, tool_call_id, iteration)
                        if _finish_result is not None:
                            return _finish_result

                        # 按序写入 memory
                        if not tc_result.defer_tool_result and tool_call_id:
                            self._memory.add_tool_result(tool_call_id, tc_result.result)

                        # 统计更新（只读工具不触发 write_effect 分支）
                        self._last_tool_call_count += 1
                        if tc_result.success:
                            self._last_success_count += 1
                            consecutive_failures = 0
                        else:
                            self._last_failure_count += 1
                            consecutive_failures += 1

                        # 熔断检测
                        if (not breaker_triggered) and consecutive_failures >= max_failures:
                            recent_errors = [
                                f"- {r.tool_name}: {r.error}"
                                for r in all_tool_results[-max_failures:]
                                if not r.success
                            ]
                            breaker_summary = "\n".join(recent_errors)
                            breaker_triggered = True
                else:
                    # ── 串行路径（保留完整原有逻辑） ──
                    for tc in _batch.tool_calls:
                        function = getattr(tc, "function", None)
                        tool_name = getattr(function, "name", "")
                        tool_call_id = getattr(tc, "id", "")

                        if breaker_triggered:
                            all_tool_results.append(
                                ToolCallResult(
                                    tool_name=tool_name,
                                    arguments={},
                                    result=breaker_skip_error,
                                    success=False,
                                    error=breaker_skip_error,
                                )
                            )
                            if tool_call_id:
                                self._memory.add_tool_result(tool_call_id, breaker_skip_error)
                            continue

                        tc_result = await self._execute_tool_call(
                            tc,
                            tool_scope,
                            on_event,
                            iteration,
                            route_result=current_route_result,
                        )

                        consecutive_text_only = 0
                        all_tool_results.append(tc_result)

                        # 卡死检测：记录工具调用到滑动窗口
                        try:
                            _tc_args, _ = self._tool_dispatcher.parse_arguments(
                                getattr(function, "arguments", None)
                            )
                        except Exception:
                            _tc_args = {}
                        self._state.record_tool_call_for_stuck_detection(tool_name, _tc_args)

                        # finish_task 成功接受时退出循环
                        _finish_result = _handle_finish_exit(tc_result, tool_call_id, iteration)
                        if _finish_result is not None:
                            return _finish_result

                        if not tc_result.defer_tool_result and tool_call_id:
                            self._memory.add_tool_result(tool_call_id, tc_result.result)

                        if tc_result.pending_approval:
                            pending = self._approval.pending
                            if approval_resolver is not None and pending is not None:
                                # ── 内联审批：在同一轮对话内等待用户决策 ──
                                logger.info("内联审批等待决策: %s", tc_result.approval_id)
                                try:
                                    decision = await approval_resolver(pending)
                                except Exception as _resolver_exc:  # noqa: BLE001
                                    logger.warning("approval_resolver 异常，视为 reject: %s", _resolver_exc)
                                    decision = None

                                if decision in ("accept", "fullaccess"):
                                    if decision == "fullaccess":
                                        self._full_access_enabled = True
                                        logger.info("内联审批: fullaccess 已开启")
                                    # 执行已批准的工具
                                    exec_ok, exec_result, exec_record = await self._execute_approved_pending(
                                        pending, on_event=on_event,
                                    )
                                    # 用真实结果替换之前写入 memory 的审批提示
                                    if tool_call_id:
                                        self._memory.replace_tool_result(tool_call_id, exec_result)
                                    # 发射审批已解决事件
                                    self._emit(
                                        on_event,
                                        ToolCallEvent(
                                            event_type=EventType.APPROVAL_RESOLVED,
                                            approval_id=tc_result.approval_id or "",
                                            approval_tool_name=pending.tool_name,
                                            result=exec_result,
                                            success=exec_ok,
                                            iteration=iteration,
                                            approval_undoable=bool(
                                                exec_record is not None and exec_record.undoable
                                            ),
                                            approval_has_changes=bool(
                                                exec_record is not None and exec_record.changes
                                            ),
                                        ),
                                    )
                                    # 更新 tc_result 统计信息
                                    tc_result = replace(
                                        tc_result,
                                        pending_approval=False,
                                        success=exec_ok,
                                        result=exec_result,
                                        error=None if exec_ok else exec_result,
                                    )
                                    # 写入追踪：审批执行的工具如果是写入工具则标记
                                    if exec_ok and exec_record is not None:
                                        _effect = self._get_tool_write_effect(pending.tool_name)
                                        if exec_record.changes or _effect == "workspace_write":
                                            self._record_workspace_write_action()
                                        elif _effect == "external_write":
                                            self._record_external_write_action()
                                        if self._has_write_tool_call and write_hint != "may_write":
                                            write_hint = "may_write"
                                    logger.info(
                                        "内联审批完成: decision=%s ok=%s tool=%s",
                                        decision, exec_ok, pending.tool_name,
                                    )
                                else:
                                    # reject / None → 拒绝
                                    reject_msg = self._approval.reject_pending(
                                        tc_result.approval_id or (pending.approval_id if pending else ""),
                                    )
                                    if tool_call_id:
                                        self._memory.replace_tool_result(tool_call_id, reject_msg)
                                    self._emit(
                                        on_event,
                                        ToolCallEvent(
                                            event_type=EventType.APPROVAL_RESOLVED,
                                            approval_id=tc_result.approval_id or "",
                                            approval_tool_name=pending.tool_name if pending else "",
                                            result=reject_msg,
                                            success=False,
                                            iteration=iteration,
                                        ),
                                    )
                                    tc_result = replace(
                                        tc_result,
                                        pending_approval=False,
                                        success=False,
                                        result=reject_msg,
                                        error=reject_msg,
                                    )
                                    logger.info("内联审批拒绝: %s", tc_result.approval_id)
                                # 内联审批完成，不退出循环，继续处理后续工具调用
                            else:
                                # ── 无 resolver（Web API 等）：阻塞等待用户决策 ──
                                approval_id = tc_result.approval_id or (pending.approval_id if pending else "")
                                logger.info("阻塞等待审批决策: %s", approval_id)
                                fut = self._interaction_registry.create(approval_id)
                                try:
                                    decision_payload = await asyncio.wait_for(
                                        fut, timeout=DEFAULT_INTERACTION_TIMEOUT,
                                    )
                                except asyncio.TimeoutError:
                                    reject_msg = self._approval.reject_pending(approval_id)
                                    if tool_call_id:
                                        self._memory.replace_tool_result(tool_call_id, reject_msg)
                                    tc_result = replace(
                                        tc_result,
                                        pending_approval=False, success=False,
                                        result=reject_msg, error=reject_msg,
                                    )
                                    logger.info("审批等待超时，自动拒绝: %s", approval_id)
                                    self._interaction_registry.cleanup_done()
                                except asyncio.CancelledError:
                                    reject_msg = self._approval.reject_pending(approval_id)
                                    if tool_call_id:
                                        self._memory.replace_tool_result(tool_call_id, reject_msg)
                                    tc_result = replace(
                                        tc_result,
                                        pending_approval=False, success=False,
                                        result=reject_msg, error=reject_msg,
                                    )
                                    self._interaction_registry.cleanup_done()
                                else:
                                    decision = decision_payload.get("decision") if isinstance(decision_payload, dict) else str(decision_payload)
                                    self._interaction_registry.cleanup_done()
                                    if decision in ("accept", "fullaccess"):
                                        if decision == "fullaccess":
                                            self._full_access_enabled = True
                                            logger.info("Web 审批: fullaccess 已开启")
                                        exec_ok, exec_result, exec_record = await self._execute_approved_pending(
                                            pending, on_event=on_event,
                                        )
                                        if tool_call_id:
                                            self._memory.replace_tool_result(tool_call_id, exec_result)
                                        self._emit(
                                            on_event,
                                            ToolCallEvent(
                                                event_type=EventType.APPROVAL_RESOLVED,
                                                approval_id=approval_id,
                                                approval_tool_name=pending.tool_name,
                                                result=exec_result,
                                                success=exec_ok,
                                                iteration=iteration,
                                                approval_undoable=bool(
                                                    exec_record is not None and exec_record.undoable
                                                ),
                                                approval_has_changes=bool(
                                                    exec_record is not None and exec_record.changes
                                                ),
                                            ),
                                        )
                                        tc_result = replace(
                                            tc_result,
                                            pending_approval=False,
                                            success=exec_ok,
                                            result=exec_result,
                                            error=None if exec_ok else exec_result,
                                        )
                                        if exec_ok and exec_record is not None:
                                            _effect = self._get_tool_write_effect(pending.tool_name)
                                            if exec_record.changes or _effect == "workspace_write":
                                                self._record_workspace_write_action()
                                            elif _effect == "external_write":
                                                self._record_external_write_action()
                                            if self._has_write_tool_call and write_hint != "may_write":
                                                write_hint = "may_write"
                                        logger.info(
                                            "Web 审批完成: decision=%s ok=%s tool=%s",
                                            decision, exec_ok, pending.tool_name,
                                        )
                                    else:
                                        reject_msg = self._approval.reject_pending(approval_id)
                                        if tool_call_id:
                                            self._memory.replace_tool_result(tool_call_id, reject_msg)
                                        self._emit(
                                            on_event,
                                            ToolCallEvent(
                                                event_type=EventType.APPROVAL_RESOLVED,
                                                approval_id=approval_id,
                                                approval_tool_name=pending.tool_name if pending else "",
                                                result=reject_msg,
                                                success=False,
                                                iteration=iteration,
                                            ),
                                        )
                                        tc_result = replace(
                                            tc_result,
                                            pending_approval=False, success=False,
                                            result=reject_msg, error=reject_msg,
                                        )
                                        logger.info("Web 审批拒绝: %s", approval_id)

                        # 更新统计
                        self._last_tool_call_count += 1
                        if tc_result.success:
                            self._last_success_count += 1
                            consecutive_failures = 0
                            _write_effect = self._get_tool_write_effect(tc_result.tool_name)
                            if _write_effect == "workspace_write":
                                self._record_workspace_write_action()
                                self._window_perception.observe_write_tool_call(
                                    tool_name=tc_result.tool_name,
                                    arguments=tc_result.arguments,
                                )
                                self._context_builder.mark_window_notice_dirty()
                                if write_hint != "may_write":
                                    write_hint = "may_write"
                            elif _write_effect == "external_write":
                                self._record_external_write_action()
                                if write_hint != "may_write":
                                    write_hint = "may_write"
                            # Batch 1 精简: run_code / delegate_to_subagent 等可在 _execute_tool_call 内
                            # 通过 _record_write_action 传播写入；此处只负责同步局部 hint。
                            if self._has_write_tool_call and write_hint != "may_write":
                                write_hint = "may_write"
                        else:
                            self._last_failure_count += 1
                            # 已在 ToolDispatcher 中自动重试过的 retryable 错误
                            # 不再计入熔断计数（重试已耗尽说明是持续性故障）
                            consecutive_failures += 1

                        # 熔断检测
                        if (not breaker_triggered) and consecutive_failures >= max_failures:
                            recent_errors = [
                                f"- {r.tool_name}({r.error_kind or 'unknown'}): {r.error}"
                                for r in all_tool_results[-max_failures:]
                                if not r.success
                            ]
                            breaker_summary = "\n".join(recent_errors)
                            breaker_triggered = True

            # 说明：旧的 ask_user 退出路径已移除。
            # 阻塞式 ask_user 在 AskUserHandler 内 await Future，
            # 返回用户回答作为 tool result，循环不中断。

            # ── 延迟图片注入：所有 tool_result 写入 memory 后再注入 user 图片消息 ──
            # 如果在 tool_result 之前注入，会破坏 assistant(tool_calls) → tool(responses)
            # 的消息序列，导致 OpenAI 兼容 API 返回 400 错误。
            self._tool_dispatcher.flush_deferred_images()

            # ── Stuck Detection：检测重复/冗余工具调用模式 ──
            stuck_warning = self._state.detect_stuck_pattern()
            if stuck_warning:
                self._memory.add_user_message(stuck_warning)
                if diag:
                    diag.guard_events.append("stuck_detection")
                logger.warning("Stuck Detection 触发: %s", stuck_warning[:100])

            # ── Turn Checkpoint：每轮结束后对被修改文件做快照 ──
            if self._checkpoint_enabled and self._has_write_tool_call:
                try:
                    _reg = self._file_registry
                    if _reg is None or not _reg.has_versions:
                        raise RuntimeError("checkpoint requires FileRegistry with versions")
                    dirty = list(_reg.staged_file_map().keys()) or list(
                        _reg.list_all_tracked()
                    )
                    turn_tools = [
                        r.tool_name for r in all_tool_results
                        if r.tool_name and r.success
                    ]
                    cp = _reg.create_turn_checkpoint(
                        turn_number=iteration,
                        dirty_files=dirty,
                        tool_names=turn_tools[-5:],
                    )
                    if cp:
                        logger.debug(
                            "Turn checkpoint created: turn=%d files=%d",
                            iteration, len(cp.files_modified),
                        )
                except Exception:
                    logger.warning("Turn checkpoint 创建失败", exc_info=True)

            if breaker_triggered:
                reply = (
                    f"连续 {max_failures} 次工具调用失败，已终止执行。"
                    f"错误摘要：\n{breaker_summary}"
                )
                self._memory.add_assistant_message(reply)
                self._last_iteration_count = iteration
                logger.warning("连续 %d 次工具失败，熔断终止", max_failures)
                logger.info("最终结果摘要: %s", _summarize_text(reply))
                return _finalize_result(
                    reply=reply,
                    tool_calls=list(all_tool_results),
                    iterations=iteration,
                    truncated=False,
                    prompt_tokens=total_prompt_tokens,
                    completion_tokens=total_completion_tokens,
                    total_tokens=total_prompt_tokens + total_completion_tokens,
                )

        self._last_iteration_count = max_iter
        reply = f"已达到最大迭代次数（{max_iter}），返回当前结果。请尝试简化任务或分步执行。"
        self._memory.add_assistant_message(reply)
        logger.warning("达到迭代上限 %d，截断返回", max_iter)
        logger.info("最终结果摘要: %s", _summarize_text(reply))
        return _finalize_result(
            reply=reply,
            tool_calls=list(all_tool_results),
            iterations=max_iter,
            truncated=True,
            prompt_tokens=total_prompt_tokens,
            completion_tokens=total_completion_tokens,
            total_tokens=total_prompt_tokens + total_completion_tokens,
        )

    def _handle_text_reply(
        self,
        *,
        message: Any,
        iteration: int,
        start_iteration: int,
        max_iter: int,
        write_hint: str,
        consecutive_text_only: int,
        diag: Any,
        all_tool_results: list,
        total_prompt_tokens: int,
        total_completion_tokens: int,
        _finalize_result: Any,
    ) -> tuple[str, Any]:
        """处理 LLM 返回纯文本（无 tool_calls）的情况。

        返回 (action, payload):
        - ("return", ChatResult) — 调用方应 return 该结果
        - ("continue", updated_consecutive_text_only) — 调用方应 continue 迭代
        """
        reply_text = _message_content_to_text(getattr(message, "content", None))

        # HTML 页面检测
        if _looks_like_html_document(reply_text):
            error_reply = self._format_html_endpoint_error(reply_text)  # kept on engine for _handle_text_reply
            self._memory.add_assistant_message(error_reply)
            self._last_iteration_count = iteration
            logger.error(
                "检测到疑似 HTML 页面响应，base_url=%s，已返回配置提示",
                self._config.base_url,
            )
            logger.info("最终结果摘要: %s", _summarize_text(error_reply))
            return "return", _finalize_result(
                reply=error_reply,
                tool_calls=list(all_tool_results),
                iterations=iteration,
                truncated=False,
                prompt_tokens=total_prompt_tokens,
                completion_tokens=total_completion_tokens,
                total_tokens=total_prompt_tokens + total_completion_tokens,
            )

        self._memory.add_assistant_message(reply_text)

        # ── 澄清放行：agent 返回澄清性文本，直接放行 ──
        if _looks_like_clarification(reply_text):
            self._last_iteration_count = iteration
            logger.info("澄清放行：检测到澄清性文本回复")
            return "return", _finalize_result(
                reply=reply_text,
                tool_calls=list(all_tool_results),
                iterations=iteration,
                truncated=False,
                prompt_tokens=total_prompt_tokens,
                completion_tokens=total_completion_tokens,
                total_tokens=total_prompt_tokens + total_completion_tokens,
            )

        # ── 等待用户操作放行：agent 需要用户上传/提供素材时，不应被门禁强制继续 ──
        if _looks_like_waiting_for_user_action(reply_text):
            self._last_iteration_count = iteration
            if diag:
                diag.guard_events.append("waiting_for_user_passthrough")
            logger.info("等待用户操作放行：检测到 agent 正在等待用户提供素材")
            return "return", _finalize_result(
                reply=reply_text,
                tool_calls=list(all_tool_results),
                iterations=iteration,
                truncated=False,
                prompt_tokens=total_prompt_tokens,
                completion_tokens=total_completion_tokens,
                total_tokens=total_prompt_tokens + total_completion_tokens,
            )

        # ── guard_mode 控制：执行守卫 & 写入门禁 ──
        _guard_mode = getattr(self._config, "guard_mode", "off")

        if _guard_mode == "soft":
            # ── soft 模式：执行守卫 — 仅记录诊断，不强制继续 ──
            if (
                write_hint != "may_write"
                and not self._active_skills
                and _contains_formula_advice(reply_text, vba_exempt=self._vba_exempt)
                and not self._execution_guard_fired
                and not all_tool_results
            ):
                self._execution_guard_fired = True
                if diag:
                    diag.guard_events.append("execution_guard_soft")
                logger.info("执行守卫(soft)：检测到公式建议未写入（仅记录，不强制继续）")

            # ── soft 模式：写入门禁 — 仅记录诊断，不强制继续 ──
            if write_hint == "may_write" and not self._has_write_tool_call:
                if diag:
                    diag.guard_events.append("write_guard_soft")
                logger.info("写入门禁(soft)：无写入工具调用（仅记录，不强制继续）")

        elif _guard_mode == "off":
            # ── off 模式：完全跳过所有门禁，agent 自然停止 ──
            pass

        else:
            logger.warning("未知 guard_mode=%r，按 off 处理", _guard_mode)

        self._last_iteration_count = iteration
        logger.info("最终结果摘要: %s", _summarize_text(reply_text))
        return "return", _finalize_result(
            reply=reply_text,
            tool_calls=list(all_tool_results),
            iterations=iteration,
            truncated=False,
            prompt_tokens=total_prompt_tokens,
            completion_tokens=total_completion_tokens,
            total_tokens=total_prompt_tokens + total_completion_tokens,
        )

    def _try_refresh_registry(self) -> None:
        """写入操作后增量刷新 FileRegistry（debounce：每轮最多一次）。"""
        if not self._registry_refresh_needed:
            return
        self._registry_refresh_needed = False
        if self._file_registry is not None:
            try:
                self._file_registry.scan_workspace()
                logger.info("FileRegistry 增量刷新完成")
            except Exception:
                logger.debug("FileRegistry 增量刷新失败", exc_info=True)

    async def _execute_tool_call(
        self,
        tc: Any,
        tool_scope: Sequence[str] | None,
        on_event: EventCallback | None,
        iteration: int,
        route_result: SkillMatchResult | None = None,
        skip_start_event: bool = False,
    ) -> ToolCallResult:
        """单个工具调用：委托给 ToolDispatcher.execute()。"""
        return await self._tool_dispatcher.execute(
            tc, tool_scope, on_event, iteration,
            route_result=route_result,
            skip_start_event=skip_start_event,
        )

    async def _execute_tool_calls_parallel(
        self,
        batch: list[Any],
        tool_scope: Sequence[str] | None,
        on_event: EventCallback | None,
        iteration: int,
        route_result: SkillMatchResult | None,
    ) -> list[tuple[Any, ToolCallResult]]:
        """并发执行一批只读工具调用，返回与输入同序的 (tc, result) 列表。

        1. 按序预发射所有 TOOL_CALL_START 事件（保证前端展示顺序）
        2. asyncio.gather 并发执行（skip_start_event=True 避免重复发射）
        3. 异常转为失败 ToolCallResult，不影响其他工具
        """
        from excelmanus.events import EventType, ToolCallEvent

        # 按序预发射 TOOL_CALL_START
        for tc in batch:
            func = getattr(tc, "function", None)
            args, _ = self._tool_dispatcher.parse_arguments(
                getattr(func, "arguments", None),
            )
            self._emit(
                on_event,
                ToolCallEvent(
                    event_type=EventType.TOOL_CALL_START,
                    tool_call_id=getattr(tc, "id", ""),
                    tool_name=getattr(func, "name", ""),
                    arguments=args,
                    iteration=iteration,
                ),
            )

        # 并发执行
        async def _run_one(tc: Any) -> tuple[Any, ToolCallResult]:
            result = await self._execute_tool_call(
                tc, tool_scope, on_event, iteration,
                route_result=route_result,
                skip_start_event=True,
            )
            return (tc, result)

        raw_results = await asyncio.gather(
            *[_run_one(tc) for tc in batch],
            return_exceptions=True,
        )

        # 异常转为失败结果，保持位置顺序
        ordered: list[tuple[Any, ToolCallResult]] = []
        for i, r in enumerate(raw_results):
            if isinstance(r, BaseException):
                tc = batch[i]
                name = getattr(getattr(tc, "function", None), "name", "")
                ordered.append((tc, ToolCallResult(
                    tool_name=name,
                    arguments={},
                    result=f"并行执行异常: {r}",
                    success=False,
                    error=str(r),
                )))
            else:
                ordered.append(r)
        return ordered

    def _enrich_tool_result_with_window_perception(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        result_text: str,
        success: bool,
    ) -> str:
        """在工具返回中附加窗口感知信息。"""
        requested_mode = self._requested_window_return_mode()
        try:
            return self._window_perception.enrich_tool_result(
                tool_name=tool_name,
                arguments=arguments,
                result_text=result_text,
                success=success,
                mode=requested_mode,
                model_id=self._active_model,
            )
        except Exception:
            logger.warning(
                "窗口感知增强失败，已回退 enriched 模式: tool=%s",
                tool_name,
                exc_info=True,
            )
            try:
                return self._window_perception.enrich_tool_result(
                    tool_name=tool_name,
                    arguments=arguments,
                    result_text=result_text,
                    success=success,
                    mode="enriched",
                    model_id=self._active_model,
                )
            except Exception:
                return result_text

    def _enrich_subagent_tool_result_with_window_perception(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        result_text: str,
        success: bool,
    ) -> str:
        """子代理工具结果复用主会话窗口感知增强逻辑。"""
        return self._enrich_tool_result_with_window_perception(
            tool_name=tool_name,
            arguments=arguments,
            result_text=result_text,
            success=success,
        )

    def _requested_window_return_mode(self) -> str:
        """读取配置中的请求模式（含 adaptive）。"""
        raw_mode = str(
            getattr(self._config, "window_return_mode", "adaptive") or "adaptive"
        ).strip().lower()
        if raw_mode in {"unified", "anchored", "enriched", "adaptive"}:
            return raw_mode
        return "enriched"

    def _effective_window_return_mode(self) -> str:
        """返回当前会话有效模式（只会是 unified/anchored/enriched）。"""
        if not self._window_perception.enabled:
            return "enriched"
        requested_mode = self._requested_window_return_mode()
        return self._window_perception.resolve_effective_mode(
            requested_mode=requested_mode,
            model_id=self._active_model,
        )

    def _apply_tool_result_hard_cap(self, text: str) -> str:
        """对工具结果应用全局硬截断，避免超长输出撑爆上下文。"""
        normalized = str(text or "")
        cap = int(self._config.tool_result_hard_cap_chars)
        if cap <= 0 or len(normalized) <= cap:
            return normalized
        return (
            f"{normalized[:cap]}\n"
            f"[结果已全局截断，原始长度: {len(normalized)} 字符，"
            f"上限: {cap} 字符]"
        )

    def _format_pending_prompt(self, pending: PendingApproval) -> str:
        """构造待确认提示。"""
        return (
            "检测到高风险操作，已进入待确认队列。\n"
            f"- ID: `{pending.approval_id}`\n"
            f"- 工具: `{pending.tool_name}`\n"
            "请执行以下命令之一：\n"
            f"- `/accept {pending.approval_id}` 执行\n"
            f"- `/reject {pending.approval_id}` 拒绝"
        )

    @staticmethod
    def _prepare_approval_arguments(
        tool_name: str,
        arguments: dict[str, Any],
        *,
        force_delete_confirm: bool,
    ) -> dict[str, Any]:
        """按执行上下文调整参数。"""
        copied = dict(arguments)
        if force_delete_confirm and tool_name in {"delete_file"}:
            copied["confirm"] = True
        return copied

    async def _call_registry_tool(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        tool_scope: Sequence[str] | None = None,
    ) -> Any:
        """在线程池中调用工具，并绑定当前会话的记忆上下文。

        委托给 ToolDispatcher 组件。
        """
        return await self._tool_dispatcher.call_registry_tool(
            tool_name=tool_name,
            arguments=arguments,
            tool_scope=tool_scope,
        )

    async def _execute_tool_with_audit(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        tool_scope: Sequence[str] | None = None,
        approval_id: str,
        created_at_utc: str,
        undoable: bool,
        force_delete_confirm: bool = False,
    ) -> tuple[str, AppliedApprovalRecord]:
        """执行高风险工具并保存审计记录。"""
        audited_arguments = self._prepare_approval_arguments(
            tool_name,
            arguments,
            force_delete_confirm=force_delete_confirm,
        )

        def _execute(
            name: str,
            args: dict[str, Any],
            scope: Sequence[str],
        ) -> Any:
            from excelmanus.tools import memory_tools

            with memory_tools.bind_memory_context(self._persistent_memory):
                return self._registry.call_tool(name, args, tool_scope=scope)

        try:
            return await asyncio.to_thread(
                self._approval.execute_and_audit,
                approval_id=approval_id,
                tool_name=tool_name,
                arguments=audited_arguments,
                tool_scope=list(tool_scope) if tool_scope else None,
                execute=_execute,
                undoable=undoable,
                created_at_utc=created_at_utc,
                session_turn=self._state.session_turn,
                session_id=self._session_id,
            )
        except Exception as exc:  # noqa: BLE001
            # execute_and_audit 在失败时会先写入 manifest 与 _applied，再抛异常。
            # 这里将失败记录带回调用方，避免上层丢失审计上下文。
            record = self._approval.get_applied(approval_id)
            if record is None:
                raise
            raise _AuditedExecutionError(cause=exc, record=record) from exc

    async def _execute_approved_pending(
        self,
        pending: PendingApproval,
        *,
        on_event: EventCallback | None = None,
    ) -> tuple[bool, str, AppliedApprovalRecord | None]:
        """执行待确认操作并处理副作用（写入追踪、CoW 映射等）。

        返回 (success, result_text, record)。
        共享逻辑：同时被 _handle_accept_command 和 _tool_calling_loop 内联审批使用。
        """
        try:
            _, record = await self._execute_tool_with_audit(
                tool_name=pending.tool_name,
                arguments=pending.arguments,
                tool_scope=None,
                approval_id=pending.approval_id,
                created_at_utc=pending.created_at_utc,
                undoable=not self._approval.is_read_only_safe_tool(pending.tool_name) and pending.tool_name not in {"run_code", "run_shell"},
                force_delete_confirm=True,
            )
        except ToolNotAllowedError:
            self._approval.clear_pending()
            msg = f"accept 执行失败：工具 `{pending.tool_name}` 当前不在授权范围内。"
            return False, msg, None
        except Exception as exc:  # noqa: BLE001
            self._approval.clear_pending()
            return False, f"accept 执行失败：{exc}", None

        # ── run_code RED 路径 → 写入追踪 ──
        if pending.tool_name == "run_code":
            from excelmanus.security.code_policy import extract_excel_targets
            _rc_code = pending.arguments.get("code") or ""
            _rc_result_json: dict | None = None
            try:
                _rc_result_json = json.loads(record.result_preview or "")
                if not isinstance(_rc_result_json, dict):
                    _rc_result_json = None
            except (json.JSONDecodeError, TypeError):
                pass
            _has_cow = bool(_rc_result_json and _rc_result_json.get("cow_mapping"))
            _has_ast_write = any(
                t.operation == "write"
                for t in extract_excel_targets(_rc_code)
            )
            if record.changes or _has_cow or _has_ast_write:
                self._record_workspace_write_action()
        # ── run_code RED 路径 → window 感知桥接 ──
        if pending.tool_name == "run_code" and self._window_perception is not None:
            _rc_code = pending.arguments.get("code") or ""
            _rc_stdout = ""
            try:
                _rc_result_json2 = json.loads(record.result_preview or "")
                _rc_stdout = _rc_result_json2.get("stdout_tail", "") if isinstance(_rc_result_json2, dict) else ""
            except (json.JSONDecodeError, TypeError):
                pass
            self._window_perception.observe_code_execution(
                code=_rc_code,
                audit_changes=record.changes,
                stdout_tail=_rc_stdout,
                iteration=0,
            )
            self._context_builder.mark_window_notice_dirty()
        # ── run_code RED 路径 → files_changed 事件 ──
        if pending.tool_name == "run_code" and on_event is not None:
            self._tool_dispatcher._emit_files_changed_from_audit(
                self, on_event, pending.approval_id,
                pending.arguments.get("code") or "",
                record.changes,
                0,
            )

        # ── 通用 CoW 映射提取 ──
        if record.result_preview:
            try:
                _accept_result = json.loads(record.result_preview)
                if isinstance(_accept_result, dict):
                    _accept_cow = _accept_result.get("cow_mapping")
                    if _accept_cow and isinstance(_accept_cow, dict):
                        self._state.register_cow_mappings(_accept_cow)
                        logger.info(
                            "审批 CoW 映射已注册: tool=%s mappings=%s",
                            pending.tool_name, _accept_cow,
                        )
            except (json.JSONDecodeError, TypeError):
                pass

        self._approval.clear_pending()
        result_text = record.result_preview or f"已执行 `{pending.tool_name}`。"
        return True, result_text, record

    def clear_memory(self) -> None:
        """清除对话历史。"""
        if self._active_skills:
            _primary = self._active_skills[-1]
            self._skill_resolver.run_skill_hook(
                skill=_primary,
                event=HookEvent.STOP,
                payload={"reason": "clear_memory"},
            )
            self._skill_resolver.run_skill_hook(
                skill=_primary,
                event=HookEvent.SESSION_END,
                payload={"reason": "clear_memory"},
            )
        self._memory.clear()
        self._loaded_skill_names.clear()
        self._hook_started_skills.clear()
        self._active_skills.clear()
        self._tools_cache = None  # 技能清空 → 失效缓存
        self._question_flow.clear()
        self._system_question_actions.clear()
        self._batch_answers.clear()
        self._pending_question_route_result = None
        self._pending_approval_route_result = None
        self._pending_approval_tool_call_id = None
        self._task_store.clear()
        self._approval.clear_pending()
        self._window_perception.reset()
        # 重置轮级状态变量，防止跨对话污染
        self._state.reset_session()
        self._system_mode_fallback = type(self)._system_mode_fallback_cache.get(self._system_mode_cache_key)
        self._last_route_result = SkillMatchResult(
            skills_used=[],
            route_mode="fallback",
        )

    @property
    def turn_count(self) -> int:
        """当前会话轮次计数，供 CLI 提示符展示。"""
        return self._state.session_turn

    def conversation_summary(self) -> str:
        """返回对话历史摘要文本，供 /history 展示。"""
        messages = self._memory.messages
        if not messages:
            return ""
        user_count = sum(1 for m in messages if m.get("role") == "user")
        assistant_count = sum(1 for m in messages if m.get("role") == "assistant")
        tool_count = sum(
            1 for m in messages
            if m.get("role") == "assistant" and m.get("tool_calls")
        )
        parts = [
            f"对话轮次: {self._state.session_turn}",
            f"用户消息: {user_count}",
            f"助手回复: {assistant_count}",
            f"工具调用消息: {tool_count}",
            f"总消息数: {len(messages)}",
        ]
        return " · ".join(parts)

    def save_conversation(self, path: str | None = None) -> str | None:
        """将对话历史保存为 JSON 文件，返回保存路径或 None。"""
        import json as _json
        from datetime import datetime as _dt
        from pathlib import Path as _Path

        messages = self._memory.get_messages()
        if not messages:
            return None

        if path:
            save_path = _Path(path)
        else:
            out_dir = _Path(self._config.workspace_root) / "outputs" / "conversations"
            out_dir.mkdir(parents=True, exist_ok=True)
            timestamp = _dt.now().strftime("%Y%m%d_%H%M%S")
            save_path = out_dir / f"conversation_{timestamp}.json"

        save_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "model": self._active_model,
            "session_turn": self._state.session_turn,
            "messages": messages,
            "session_diagnostics": self._session_diagnostics,
            "prompt_injection_snapshots": self._state.prompt_injection_snapshots,
        }
        save_path.write_text(
            _json.dumps(payload, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        return str(save_path)

    # ── 模型能力 ──────────────────────────────────

    def set_model_capabilities(self, caps: Any) -> None:
        """设置当前模型的能力探测结果。"""
        self._model_capabilities = caps

    def get_model_capabilities(self) -> Any:
        """返回当前模型的能力探测结果。"""
        return self._model_capabilities

    def set_thinking_budget(self, budget: int) -> None:
        """设置 thinking token 预算（兼容旧接口）。"""
        self._thinking_config = ThinkingConfig(
            effort=self._thinking_config.effort,
            budget_tokens=max(0, budget),
        )

    def set_thinking_effort(self, effort: str) -> None:
        """设置 thinking 等级。"""
        if effort not in _EFFORT_RATIOS:
            logger.warning("无效的 thinking effort: %r，忽略", effort)
            return
        self._thinking_config = ThinkingConfig(
            effort=effort,
            budget_tokens=self._thinking_config.budget_tokens,
        )

    def set_thinking_config(self, effort: str | None = None, budget: int | None = None) -> None:
        """统一设置 thinking 配置。"""
        new_effort = effort if effort and effort in _EFFORT_RATIOS else self._thinking_config.effort
        new_budget = max(0, budget) if budget is not None else self._thinking_config.budget_tokens
        self._thinking_config = ThinkingConfig(effort=new_effort, budget_tokens=new_budget)

    @property
    def thinking_config(self) -> ThinkingConfig:
        """当前 thinking 配置（只读）。"""
        return self._thinking_config

    # ── 多模型切换 ──────────────────────────────────

    @property
    def current_model(self) -> str:
        """当前使用的模型标识符。"""
        return self._active_model

    @property
    def current_model_name(self) -> str | None:
        """当前激活的模型 profile 短名称，None 表示使用默认配置。"""
        return self._active_model_name

    def list_models(self) -> list[dict[str, str]]:
        """列出所有可用模型档案，含当前激活标记。"""
        result: list[dict[str, str]] = []
        # 默认模型（来自主配置）
        is_default_active = self._active_model_name is None
        result.append({
            "name": "default",
            "model": self._config.model,
            "base_url": self._config.base_url,
            "description": "默认模型（主配置）",
            "active": "yes" if is_default_active else "",
        })
        for profile in self._config.models:
            result.append({
                "name": profile.name,
                "model": profile.model,
                "base_url": profile.base_url,
                "description": profile.description,
                "active": "yes" if self._active_model_name == profile.name else "",
            })
        return result

    def model_names(self) -> list[str]:
        """返回所有可用模型短名称列表（含 default）。"""
        names = ["default"]
        names.extend(p.name for p in self._config.models)
        return names

    def switch_model(self, name: str) -> str:
        """切换到指定模型档案。返回切换结果描述。

        支持智能匹配：精确匹配 > 前缀匹配 > 包含匹配。
        """
        name = name.strip()
        if not name:
            return "请指定模型名称。用法：/model <名称>，/model list 查看可用模型。"

        # 切换回默认
        if name.lower() == "default":
            self._active_model = self._config.model
            self._active_api_key = self._config.api_key
            self._active_base_url = self._config.base_url
            self._active_protocol = self._config.protocol
            self._active_model_name = None
            self._client = create_client(
                api_key=self._active_api_key,
                base_url=self._active_base_url,
                protocol=self._active_protocol,
            )
            self._sync_router_model_runtime()
            self._model_capabilities = None
            return f"已切换到默认模型：{self._config.model}"

        # 在 profiles 中查找：精确匹配 > 前缀匹配 > 包含匹配
        profiles = self._config.models
        lowered = name.lower()

        # 精确匹配
        matched = next((p for p in profiles if p.name.lower() == lowered), None)
        # 前缀匹配
        if matched is None:
            prefix_matches = [p for p in profiles if p.name.lower().startswith(lowered)]
            if len(prefix_matches) == 1:
                matched = prefix_matches[0]
        # 包含匹配（模型标识符中包含输入）
        if matched is None:
            contain_matches = [
                p for p in profiles
                if lowered in p.name.lower() or lowered in p.model.lower()
            ]
            if len(contain_matches) == 1:
                matched = contain_matches[0]

        if matched is None:
            available = ", ".join(p.name for p in profiles) if profiles else "无"
            return f"未找到模型 {name!r}。可用模型：default, {available}"

        self._active_model = matched.model
        self._active_api_key = matched.api_key
        self._active_base_url = matched.base_url
        self._active_protocol = matched.protocol
        self._active_model_name = matched.name
        self._client = create_client(
            api_key=self._active_api_key,
            base_url=self._active_base_url,
            protocol=self._active_protocol,
        )
        self._sync_router_model_runtime()
        self._model_capabilities = None
        desc = f"（{matched.description}）" if matched.description else ""
        return f"已切换到模型：{matched.name} → {matched.model}{desc}"

    def _sync_router_model_runtime(self) -> None:
        """在主模型切换后同步路由模型运行时（仅跟随模式）。"""
        if not self._router_follow_active_model:
            return
        self._router_client = self._client
        self._router_model = self._active_model
        # adviser 也跟随主模型（仅当 adviser 未配置独立模型时）
        if self._advisor_follow_active_model:
            self._advisor_client = self._client
            self._advisor_model = self._active_model

    async def _adapt_guidance_only_slash_route(
        self,
        *,
        route_result: SkillMatchResult,
        user_message: str,
        slash_command: str | None,
        raw_args: str,
    ) -> tuple[SkillMatchResult, str]:
        """将仅指导型 slash 技能回落为任务执行路由，避免“只讲不做”。

        触发条件：
        - 手动 slash 命令命中（route_mode=slash_direct）
        - 命中的 skill 不是 command_dispatch=tool
        - slash 参数中包含可执行任务文本
        """
        if not slash_command or route_result.route_mode != "slash_direct":
            return route_result, user_message

        task_text = raw_args.strip()
        if not task_text:
            return route_result, user_message

        skill = self._skill_resolver.pick_route_skill(route_result)
        if skill is None:
            return route_result, user_message
        if skill.command_dispatch == "tool":
            return route_result, user_message

        # 先尝试词法分类，避免重复触发 write_hint LLM 调用
        pre_hint: str | None = None
        if self._skill_router is not None:
            pre_hint = self._skill_router._classify_write_hint_lexical(task_text) or None
        fallback = await self._route_skills(task_text, write_hint=pre_hint)
        guidance_context = (
            f"[Slash Guidance] 已启用技能 `{skill.name}` 的方法论约束。\n"
            "该技能仅用于补充执行规范，不改变用户任务目标。\n"
            "请优先调用工具完成任务，不要只输出「我先…」「我将…」等计划性文字。"
        )
        fallback_contexts = list(fallback.system_contexts)
        fallback_contexts.append(guidance_context)
        adapted = SkillMatchResult(
            skills_used=list(fallback.skills_used),
            route_mode=fallback.route_mode,
            system_contexts=fallback_contexts,
            parameterized=fallback.parameterized,
            write_hint=fallback.write_hint,
        )
        logger.info(
            "斜杠技能 %s 为 guidance-only，已回落到任务路由: %s",
            skill.name,
            _summarize_text(task_text),
        )
        return adapted, task_text

    async def _route_skills(
        self,
        user_message: str,
        *,
        slash_command: str | None = None,
        raw_args: str | None = None,
        write_hint: str | None = None,
        chat_mode: str = "write",
        on_event: EventCallback | None = None,
        images: list[dict[str, Any]] | None = None,
    ) -> SkillMatchResult:
        if self._skill_router is None:
            return SkillMatchResult(
                skills_used=[],
                route_mode="all_tools",
                system_contexts=[],
            )

        blocked_skillpacks = (
            set(self._restricted_code_skillpacks)
            if not self._full_access_enabled
            else None
        )
        return await self._skill_router.route(
            user_message,
            slash_command=slash_command,
            raw_args=raw_args,
            blocked_skillpacks=blocked_skillpacks,
            write_hint=write_hint,
            chat_mode=chat_mode,
            on_event=on_event,
            images=images,
        )

    @staticmethod
    def _schema_accepts_string(schema: Any) -> bool:
        if not isinstance(schema, dict):
            return False
        type_value = schema.get("type")
        if type_value == "string":
            return True
        if isinstance(type_value, list) and "string" in type_value:
            return True
        return False

    def _map_command_dispatch_arguments(
        self,
        *,
        tool_name: str,
        raw_args: str,
    ) -> tuple[dict[str, Any] | None, str | None]:
        normalized_raw = raw_args.strip()
        if not normalized_raw:
            return {}, None

        try:
            parsed = json.loads(normalized_raw)
        except Exception:  # noqa: BLE001
            parsed = None

        if isinstance(parsed, dict):
            return parsed, None

        tool_def = getattr(self._registry, "get_tool", lambda _: None)(tool_name)
        if tool_def is None:
            return None, f"未找到命令分发目标工具：{tool_name}"

        schema = getattr(tool_def, "input_schema", {}) or {}
        properties = schema.get("properties", {})
        if not isinstance(properties, dict):
            return None, "命令分发失败：目标工具参数 schema 非法。"

        if len(properties) == 1:
            key, val = next(iter(properties.items()))
            if self._schema_accepts_string(val):
                return {key: normalized_raw}, None

        for candidate in ("input", "query", "text", "path"):
            if candidate in properties and self._schema_accepts_string(properties[candidate]):
                return {candidate: normalized_raw}, None

        return (
            None,
            "命令分发失败：无法将参数自动映射到工具入参。请使用 JSON 对象参数。",
        )

    async def _run_command_dispatch_skill(
        self,
        *,
        skill: Skillpack,
        raw_args: str,
        route_result: SkillMatchResult,
        on_event: EventCallback | None,
    ) -> ChatResult:
        tool_name = skill.command_tool or ""
        arguments, error_message = self._map_command_dispatch_arguments(
            tool_name=tool_name,
            raw_args=raw_args,
        )
        if arguments is None:
            reply = error_message or "命令分发失败。"
            self._memory.add_assistant_message(reply)
            self._last_iteration_count = 1
            self._last_tool_call_count = 0
            self._last_success_count = 0
            self._last_failure_count = 1
            return ChatResult(
                reply=reply,
                tool_calls=[],
                iterations=1,
                truncated=False,
            )

        tool_call_id = f"dispatch_{int(time.time() * 1000)}"
        tc = SimpleNamespace(
            id=tool_call_id,
            function=SimpleNamespace(
                name=tool_name,
                arguments=json.dumps(arguments, ensure_ascii=False),
            ),
        )
        tc_result = await self._execute_tool_call(
            tc,
            None,
            on_event,
            iteration=1,
            route_result=route_result,
        )

        if not tc_result.defer_tool_result:
            self._memory.add_tool_result(tool_call_id, tc_result.result)

        if tc_result.pending_question and self._pending_question_route_result is None:
            self._pending_question_route_result = route_result
        if tc_result.pending_approval:
            self._pending_approval_route_result = route_result
            self._pending_approval_tool_call_id = tool_call_id

        if self._question_flow.has_pending():
            reply = self._question_flow.format_prompt()
        else:
            reply = tc_result.result

        self._memory.add_assistant_message(reply)
        self._last_iteration_count = 1
        self._last_tool_call_count = 1
        self._last_success_count = 1 if tc_result.success else 0
        self._last_failure_count = 0 if tc_result.success else 1
        return ChatResult(
            reply=reply,
            tool_calls=[tc_result],
            iterations=1,
            truncated=False,
        )


    def _effective_system_mode(self) -> str:
        configured = self._config.system_message_mode
        if configured != "auto":
            return configured
        if type(self)._system_mode_fallback_cache.get(self._system_mode_cache_key) == "merge":
            return "merge"
        return "replace"

    def _format_html_endpoint_error(self, raw_text: str) -> str:
        """将 HTML 错配响应转换为可操作的配置提示。"""
        first_line = raw_text.strip().splitlines()[0] if raw_text.strip() else "(空)"
        preview = first_line[:120].replace("<", "[").replace(">", "]")
        return (
            "LLM 接口返回了 HTML 页面而不是模型 JSON 响应。\n"
            "这通常是 EXCELMANUS_BASE_URL 指向了网站首页，而不是 OpenAI 兼容 API 地址。\n"
            f"当前 EXCELMANUS_BASE_URL: {self._config.base_url}\n"
            "请改为可用的 API 端点（通常以 `/v1` 结尾），然后重试。\n"
            f"响应片段: {preview}"
        )

