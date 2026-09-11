"""Agent 核心引擎：入口处理后进入 Tool Calling 步循环。"""

from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from collections.abc import Sequence
from pathlib import Path
from dataclasses import replace
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import openai

from excelmanus.approval import AppliedApprovalRecord, ApprovalManager, PendingApproval
from excelmanus.compaction import CompactionManager
from excelmanus.context_budget import ContextBudget
from excelmanus.workspace import IsolatedWorkspace, SandboxEnv, WorkspaceTransaction
from excelmanus.providers import create_client
from excelmanus.config import ExcelManusConfig, ModelProfile, format_deprecated_model_message
from excelmanus.events import EventCallback, EventType, ToolCallEvent
from excelmanus.hooks import (
    HookDecision,
    HookEvent,
    SkillHookRunner,
)
from excelmanus.logger import get_logger
from excelmanus.memory import ConversationMemory
from excelmanus.interaction import InteractionRegistry, DEFAULT_INTERACTION_TIMEOUT
from excelmanus.question_flow import PendingQuestion, QuestionFlowManager
from excelmanus.skillpacks import (
    SkillMatchResult,
    SkillRouter,
    Skillpack,
    SkillpackManager,
)
from excelmanus.subagent import SubagentExecutor, SubagentRegistry, SubagentResult
from excelmanus.task_list import TaskStore
from excelmanus.tools import task_tools
from excelmanus.tools.introspection_tools import register_introspection_tools
from excelmanus.engine_core.command_handler import CommandHandler
from excelmanus.engine_core.context_builder import ContextBuilder
from excelmanus.engine_core.session_state import SessionState
from excelmanus.engine_core.subagent_orchestrator import SubagentOrchestrator
from excelmanus.engine_core.llm_caller import (
    LLMCaller,
    compute_retry_delay,
    is_content_filter_error,
    is_nonretryable_auth_error,
    is_retryable_llm_error,
)
from excelmanus.error_guidance import FailureGuidance as _FailureGuidance, classify_failure as _classify_failure
from excelmanus.engine_core.interaction_handler import InteractionHandler
from excelmanus.engine_core.meta_tools import MetaToolBuilder
from excelmanus.engine_core.skill_resolver import SkillResolver
from excelmanus.engine_core.tool_dispatcher import ToolDispatcher
from excelmanus.mentions.parser import MentionParser, ResolvedMention
from excelmanus.mcp.manager import MCPManager, parse_tool_prefix
from excelmanus.tools.registry import ToolNotAllowedError
from excelmanus.engine_types import (
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
)
from excelmanus.engine_utils import (
    _WRITE_EFFECT_VALUES,
    _message_content_to_text,
    _normalize_tool_calls,
    _extract_completion_message,
    _usage_token,
    _extract_cached_tokens,
    _extract_anthropic_cache_tokens,
    _extract_ttft_ms,
    _looks_like_html_document,
    _user_requests_vba,
    _summarize_text,
    _split_tool_call_batches,
    _extract_text_tool_calls,
    build_mention_context_block,
)

if TYPE_CHECKING:
    from excelmanus.database import Database
    from excelmanus.engine_core.subagent_orchestrator import ParallelDelegateOutcome
    from excelmanus.memory_extractor import MemoryExtractor
    from excelmanus.persistent_memory import PersistentMemory

logger = get_logger("engine")

from excelmanus.message_serialization import to_plain as _to_plain, assistant_message_to_dict as _assistant_message_to_dict  # noqa: E402


def _tool_access_from_chat_mode(chat_mode: str) -> str:
    """用户选定的对话模式 → 工具可见性。不是对写入意图的猜测。"""
    if chat_mode in ("read", "plan"):
        return "read_only"
    return "may_write"


def _failure_guidance_event(guidance: _FailureGuidance) -> ToolCallEvent:
    """将 FailureGuidance 转为 ToolCallEvent（使用 fg_* 字段）。"""
    return ToolCallEvent(
        event_type=EventType.FAILURE_GUIDANCE,
        fg_category=guidance.category,
        fg_code=guidance.code,
        fg_title=guidance.title,
        fg_message=guidance.message,
        fg_stage=guidance.stage,
        fg_retryable=guidance.retryable,
        fg_diagnostic_id=guidance.diagnostic_id,
        fg_actions=guidance.actions,
        fg_provider=guidance.provider,
        fg_model=guidance.model,
    )


class AgentEngine:
    """核心代理引擎，驱动 LLM 与工具之间的 Tool Calling 循环。"""

    # auto 模式系统消息兼容性探测结果（key-based 缓存，按 model+base_url 隔离）
    # 使用 OrderedDict 限制最大条目数，防止长期运行无界增长
    _SYSTEM_MODE_CACHE_MAX = 64
    _system_mode_fallback_cache: "OrderedDict[tuple[str, str], str]" = OrderedDict()

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
    ) -> None:
        # ── 核心组件初始化（必须在所有 property 代理字段赋值之前）──
        self._session_id: str | None = None
        self._history_snapshot_index: int = 0
        self._state = SessionState()
        # ── LLM 客户端统一管理（main / AUX） ──
        from excelmanus.engine_core.llm_client_manager import LLMClientManager
        self._llm_clients = LLMClientManager(config)
        self._client = self._llm_clients.main_client
        self._router_client = self._llm_clients.router_client
        self._router_model = self._llm_clients.router_model
        self._router_follow_active_model = self._llm_clients.router_follow_active_model
        self._config = config
        # ── 视觉能力推断：图片只交给主模型 ──
        self._is_vision_capable = self._infer_vision_capable(config, database)
        logger.info(
            "视觉模式: main_vision=%s",
            self._is_vision_capable,
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
            SkillpackManager(
                config, skill_router._loader,
                user_skill_dir=Path(config.skills_user_dir).expanduser().resolve(),
            )
            if skill_router is not None
            else None
        )
        self._memory = ConversationMemory(config)
        # 运行时变量注入系统提示词
        resolved_root = str(Path(config.workspace_root).resolve())
        self._runtime_vars: dict[str, str] = {
            "workspace_root": resolved_root,
        }
        for _var_key, _var_val in self._runtime_vars.items():
            self._memory.system_prompt = self._memory.system_prompt.replace(
                f"{{{{{_var_key}}}}}", _var_val
            )
            self._memory.system_prompt = self._memory.system_prompt.replace(
                f"{{{_var_key}}}", _var_val
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
        # 会话级权限控制：从持久化配置读取，继承上次设置
        self._full_access_enabled: bool = self._load_persisted_full_access(database)
        # 会话级子代理开关：初始化继承配置，可通过 /subagent 动态切换
        self._subagent_enabled: bool = config.subagent_enabled
        self._subagent_registry = SubagentRegistry(config)
        self._restricted_code_skillpacks: set[str] = {"excel_code_runner"}
        # 会话级 skill 累积：记录本会话已加载过的 skill 名称及其最后激活轮次
        self._loaded_skill_names: dict[str, int] = {}
        # 当前激活技能列表：末尾为主 skill，空列表表示未激活
        self._active_skills: list[Skillpack] = []
        # ── 工具 schema 缓存（同 turn 内 chat_mode/skill 集合不变则复用）──
        self._tools_cache: list[dict[str, Any]] | None = None
        self._tools_cache_key: tuple[str, str, frozenset[str], bool] | None = None
        _cache_key = (config.model, config.base_url)
        self._system_mode_cache_key = _cache_key
        self._system_mode_fallback: str | None = type(self)._system_mode_fallback_cache.get(_cache_key)
        # ── 状态变量由 self._state 统一管理 ──
        # self._state 在 __init__ 顶部初始化，以下属性通过 @property 代理访问：
        # _session_turn, _last_iteration_count, _last_tool_call_count,
        # _last_success_count, _last_failure_count,
        # _has_write_tool_call, _turn_diagnostics, _session_diagnostics,
        # _execution_guard_fired, _vba_exempt
        self._credential_resolver: Any = None  # CredentialResolver，由 SessionManager 注入
        self._pool_account_id: str | None = None  # 号池账号 ID（pool_oauth 来源时设置）
        self._pool_profile_name: str | None = None  # 号池 profile 名称
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
                logger.warning("FileRegistry 初始化失败", exc_info=True)
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
        self._mention_contexts: list[ResolvedMention] | None = None
        self._current_chat_mode: str = "write"

        # ── 上下文自动压缩（Compaction）──────────────────────
        self._compaction_manager = CompactionManager(config)
        # 缓存最近一次 _tool_calling_loop 中构建的 system_msgs，
        # 供 get_compaction_status / /compact 命令使用更准确的 token 计数。
        self._last_system_msgs: list[dict] | None = None

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
                self._llm_call_store = _LCS(database)
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
        # ── Embedding 客户端（独立于 persistent_memory，供多个语义增强层共享） ──
        self._embedding_client: Any = None  # 类型：EmbeddingClient | None
        if config.embedding_enabled:
            try:
                from excelmanus.embedding.client import EmbeddingClient
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
            except Exception:
                logger.debug("Embedding 客户端初始化失败", exc_info=True)
                self._embedding_client = None
        # 语义记忆增强层（延迟初始化，待首轮 chat 时异步同步索引）
        self._semantic_memory: Any = None  # 类型：SemanticMemory | None
        if persistent_memory is not None and self._embedding_client is not None:
            try:
                from excelmanus.embedding.semantic_memory import SemanticMemory
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
        # 错误解决方案语义存储：历史错误→解决方案对的向量索引
        self._error_solution_store: Any = None  # 类型：ErrorSolutionStore | None
        if self._embedding_client is not None:
            try:
                from excelmanus.embedding.error_solution_store import ErrorSolutionStore
                _error_store_dir = Path(config.workspace_root) / ".excelmanus" / "error_solutions"
                self._error_solution_store = ErrorSolutionStore(
                    embedding_client=self._embedding_client,
                    store_dir=_error_store_dir,
                )
            except Exception:
                logger.debug("错误解决方案存储初始化失败", exc_info=True)
                self._error_solution_store = None
        # 将 embedding 客户端注入 CompactionManager（延迟注入，因为 compaction 先于 embedding 初始化）
        if self._embedding_client is not None:
            self._compaction_manager._embedding_client = self._embedding_client
        # 将 embedding 客户端和语义记忆注入 MemoryExtractor（延迟注入，用于记忆语义去重）
        if self._embedding_client is not None and self._semantic_memory is not None and memory_extractor is not None:
            memory_extractor._embedding_client = self._embedding_client
            memory_extractor._semantic_memory = self._semantic_memory
        # 会话启动时清理过期记忆
        if persistent_memory is not None and config.memory_expire_days > 0:
            try:
                persistent_memory.cleanup_expired(config.memory_expire_days)
            except Exception:
                logger.debug("记忆过期清理失败，已跳过", exc_info=True)
        # 会话启动时加载核心记忆到 system prompt
        if persistent_memory is not None:
            core_memory = persistent_memory.load_core()
            if core_memory:
                original = self._memory.system_prompt
                self._memory.system_prompt = (
                    f"{original}\n\n## 持久记忆\n{core_memory}"
                )
        self._session_summary_store: Any = None  # 由 SessionManager 注入

        # ── 用户自定义规则 ─────────────────────────────────
        self._rules_manager: Any = None  # 类型：RulesManager | None
        try:
            from excelmanus.rules import RulesManager as _RM
            from excelmanus.stores.rules_store import RulesStore as _RS
            _rules_db_store = _RS(database) if database is not None else None
            self._rules_manager = _RM(db_store=_rules_db_store)
        except Exception:
            logger.debug("RulesManager 初始化失败", exc_info=True)

        # ── Playbook 存储（仅供 /playbook 命令查阅，默认路径不再自动注入）──
        self._playbook_store: Any = None
        if config.playbook_enabled:
            try:
                from excelmanus.playbook import PlaybookStore
                _pb_db_path = config.playbook_db_path
                if not _pb_db_path:
                    _pb_db_path = str(Path(config.workspace_root) / ".excelmanus" / "playbook.db")
                Path(_pb_db_path).parent.mkdir(parents=True, exist_ok=True)
                self._playbook_store = PlaybookStore(_pb_db_path)
                logger.info(
                    "Playbook 已启用: db=%s, bullets=%d",
                    _pb_db_path, self._playbook_store.count(),
                )
            except Exception:
                logger.debug("Playbook 初始化失败", exc_info=True)
                self._playbook_store = None

        # ── MCP Client 集成 ──────────────────────────────────
        self._mcp_manager = mcp_manager or MCPManager(
            config.workspace_root, app_config=config,
        )
        self._own_mcp_manager = own_mcp_manager

        # ── 多模型切换 ──────────────────────────────────
        self._active_model: str = config.model
        self._active_api_key: str = config.api_key
        self._active_base_url: str = config.base_url
        self._active_protocol: str = config.protocol
        self._active_model_name: str | None = None  # 当前激活的 profile name
        self._active_profile: ModelProfile | None = None  # 当前激活的完整 profile

        # ── 上下文预算管理（切换模型时自动更新） ──
        # base_tokens（锁定值，不随模型切换变化）仅在用户显式指定时设置。
        # 环境变量存在即锁定；否则用「配置值 ≠ 模型推断值」兼容编程传入。
        # 未锁定时由 model_tokens 驱动，切换模型时自动更新。
        from excelmanus.config import is_context_window_user_pinned
        _user_pinned = is_context_window_user_pinned(
            config.max_context_tokens, config.model,
        )
        self._context_budget = ContextBudget(
            base_tokens=config.max_context_tokens if _user_pinned else 0,
            model=config.model,
        )
        # CompactionManager / Memory 在 ContextBudget 之前用 config 快照初始化，
        # 必须立刻对齐，否则对话页会显示设置页以外的窗口。
        self._sync_context_window_consumers()

        # ── 模型能力探测结果（由 API 层或启动时注入） ──
        from excelmanus.model_probe import ModelCapabilities
        self._model_capabilities: ModelCapabilities | None = None
        self._thinking_config = ThinkingConfig(
            effort=config.thinking_effort,
            budget_tokens=config.thinking_budget,
        )

        # ── Guide / 插话队列：当前步不读，下一步开始时并入 user 消息 ──
        self._guide_messages: list[str] = []
        self._interrupt_queue: list[str] = []
        # ── 渠道上下文（Bot 渠道提示词注入） ──
        self._channel_context: str | None = None

        # ── /tools 与 /reasoning 展示开关（仅会话级，不持久化） ──
        self._show_tool_calls: bool = False
        self._show_reasoning: bool = False

        # ── 解耦组件延迟初始化 ──────────────────────────────
        self._tool_dispatcher = ToolDispatcher(self)
        self._subagent_orchestrator = SubagentOrchestrator(self)
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
            _probe_task = loop.create_task(_do_probe())
            _probe_task.add_done_callback(
                lambda t: (
                    logger.debug("后台 probe 任务异常: %s", t.exception())
                    if not t.cancelled() and t.exception() else None
                )
            )
            logger.info("已调度后台 probe 检测: model=%s", config.model)
        except RuntimeError:
            logger.debug("无事件循环，跳过后台 probe")

    @staticmethod
    def _infer_vision_capable(config: "ExcelManusConfig", db: "Database | None" = None) -> bool:
        """推断主模型是否支持视觉输入。

        优先级：手动覆盖 > 关键词+probe 交叉验证 > 关键词推断。
        对已知视觉模型（关键词匹配），probe=False 不覆盖关键词推断，
        避免 Codex 等 backend-api 的 probe 误判导致图片被拦截。
        """
        mv = config.main_model_vision
        if mv == "true":
            return True
        if mv == "false":
            return False

        # ── 关键词推断辅助 ──
        model_lower = config.model.lower()
        _NON_VISION_KEYWORDS = (
            "o3-mini",
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
            "gpt-4o", "gpt-4.1",
            "gpt-5",
            "gpt-6",
            "gpt-image-1",
            "o1", "o3", "o4",
            "grok-2-vision", "grok-4",
            "claude-opus-", "claude-sonnet-", "claude-haiku-",
            "claude-opus-4", "claude-sonnet-4", "claude-haiku-4",
            "claude-fable", "claude-mythos",
            "gemini",
            "amazon.nova", "nova-lite", "nova-pro", "nova-premier",
            "-vl", "-vision", "-multimodal",
            "qwen-vl", "qwen2-vl", "qwen2.5-vl", "qwen3-vl", "qwen3.5-vl",
            "qwen-omni", "qwen2.5-omni", "qwen3-omni",
            # Qwen 3.5+ 旗舰已是原生多模态（不再依赖 -vl 后缀）
            "qwen3.8",
            "qwen3.7-plus", "qwen3.7-flash", "qwen3.7-max",
            "qwen3.6-plus", "qwen3.6-flash",
            "qwen3.5-plus", "qwen3.5-flash",
            "qvq-",
            # DeepSeek V4.1 Flash（deepseek-flash）原生视觉；V3/chat/reasoner/v4-pro 仍为文本
            "deepseek-flash",
            "deepseek-v4-flash",
            "deepseek-v4.1",
            "deepseek-vl",
            "janus-pro",
            "llama-3.2-", "llama3.2-vision",
            "llama-4-", "llama4-",
            "pixtral",
            "ministral-3b", "ministral-8b", "ministral-14b",
            "mistral-small-3", "mistral-medium-3", "mistral-large-3",
            "phi-3-vision", "phi-3.5-vision", "phi-4-multimodal",
            "glm-4v", "glm-4.1v", "glm-4.5v", "glm-4.6v", "glm-5",
            "internvl",
            "minicpm-v",
            "minicpm-o",
            "ernie-4.5-vl", "ernie-vl",
            "command-a-vision",
            "aya-vision",
            "moonshot-v1-vision", "kimi-vl", "kimi-k3", "kimi-k2.5", "kimi-k2.6", "kimi-k2.7",
            "yi-vl",
            "doubao-1.5-vision", "doubao-1.6-vision", "doubao-vision", "seed1.5-vl", "seed-vl",
            "doubao-seed-2",
            "hunyuan-vision",
            "minimax-vl", "minimax-m3",
            "step-1v", "step-1.5v", "step-3",
            "step-r1-v-mini", "step-1o-vision", "step-1o-turbo-vision",
            "llava",
        )
        keyword_vision = any(kw in model_lower for kw in _VISION_KEYWORDS)

        # ── probe 交叉验证：probe 结果仅在与关键词推断一致时信任 ──
        # 部分 backend-api（如 Codex）的 probe 可能因端点限制而误判 vision=False，
        # 对已知视觉模型，关键词推断比 probe 更可靠。
        if db is not None:
            try:
                from excelmanus.model_probe import load_capabilities
                caps = load_capabilities(db, config.model, config.base_url)
                if caps is not None and caps.supports_vision is not None:
                    if caps.supports_vision:
                        logger.info(
                            "视觉能力来自 probe 检测结果: model=%s, vision=True",
                            config.model,
                        )
                        return True
                    # probe=False：仅对关键词也认为无视觉的模型信任
                    if not keyword_vision:
                        logger.info(
                            "视觉能力来自 probe 检测结果: model=%s, vision=False",
                            config.model,
                        )
                        return False
                    # probe=False 但关键词=True：probe 可能因 backend-api 差异误判，
                    # 信任关键词推断（如 Codex backend 不支持 probe 但实际支持 vision）
                    logger.warning(
                        "probe 标记 vision=False 但关键词推断为 True，"
                        "信任关键词推断: model=%s。"
                        "若确实不支持视觉，请设置 EXCELMANUS_MAIN_MODEL_VISION=false",
                        config.model,
                    )
                    return True
            except Exception:
                logger.debug("加载 probe 视觉检测结果失败，回退到关键词推断", exc_info=True)

        logger.info(
            "视觉能力来自关键词推断: model=%s → %s",
            config.model, keyword_vision,
        )
        return keyword_vision

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
        """记录工作区写入：写入态 + registry 刷新标记 + panorama 脏标记。"""
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

        # 清理所有 pending 状态，避免 rollback 后 chat() 误入旧的
        # pending question/approval/plan 处理路径，导致孤立 tool_call_id 400 错误
        self._question_flow.clear()
        self._system_question_actions.clear()
        self._batch_answers.clear()
        self._pending_question_route_result = None
        self._approval.clear_pending()
        self._pending_approval_route_result = None
        self._pending_approval_tool_call_id = None

        # ── rollback 额外状态清理（与 clear_memory 对齐） ──
        # 任务清单：任务在被回退的轮次中创建，已无效
        self._task_store.clear()
        # 工具 schema 缓存失效
        self._tools_cache = None
        # SessionState 中与已回退轮次相关的累积状态
        self._state.affected_files.clear()
        self._state.write_operations_log.clear()
        self._state.execution_guard_fired = False
        if rollback_files:
            self._state.backup_write_notice_shown = False
        # 图片追踪：清理已移除消息相关的图片状态
        self._memory.reset_image_tracking()

        return {
            "removed_messages": removed,
            "file_rollback_results": file_results,
            "turn_index": turn_index,
        }

    async def _run_registry_scan(self) -> None:
        """后台登记工作区文件。不打开 xlsx 抽表结构——概况由模型调 inspect。"""
        if self._file_registry is None:
            return
        try:
            await asyncio.to_thread(
                lambda: self._file_registry.scan_workspace(extract_sheet_meta=False)
            )
            self._registry_scan_done = True
            self._registry_scan_error = None
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            self._registry_scan_done = False
            self._registry_scan_error = str(exc) or exc.__class__.__name__
            logger.debug("FileRegistry 后台扫描失败", exc_info=True)
            return

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

    def _ensure_memory_extractor(self) -> Any:
        """仅在显式 extract 时构造；默认引擎路径不创建 aux LLM 客户端。"""
        if self._memory_extractor is not None:
            return self._memory_extractor
        if self._persistent_memory is None or not self._config.memory_enabled:
            return None
        try:
            from excelmanus.memory_extractor import MemoryExtractor
            from excelmanus.providers import create_client

            mem_model = self._config.aux_model or self._config.model
            mem_api_key = self._config.aux_api_key or self._config.api_key
            mem_base_url = self._config.aux_base_url or self._config.base_url
            mem_protocol = (
                self._config.aux_protocol
                if self._config.aux_enabled and self._config.aux_model
                else self._config.protocol
            )
            client = create_client(
                api_key=mem_api_key,
                base_url=mem_base_url,
                protocol=mem_protocol,
            )
            extractor = MemoryExtractor(client=client, model=mem_model)
            if self._embedding_client is not None and self._semantic_memory is not None:
                extractor._embedding_client = self._embedding_client
                extractor._semantic_memory = self._semantic_memory
            self._memory_extractor = extractor
            return extractor
        except Exception:
            logger.debug("MemoryExtractor 延迟创建失败", exc_info=True)
            return None

    async def extract_and_save_memory(
        self,
        *,
        trigger: str = "manual",
        on_event: EventCallback | None = None,
    ) -> list:
        """从对话历史中提取记忆并持久化。仅由显式触发调用。

        trigger: "manual" | "periodic" | "pre_compaction"
        若 PersistentMemory 未配置则静默跳过。
        所有异常均被捕获并记录日志。
        返回提取到的 MemoryEntry 列表（可能为空）。
        """
        extractor = self._ensure_memory_extractor()
        if extractor is None or self._persistent_memory is None:
            return []
        try:
            messages = self._memory.get_messages()
            entries = await extractor.extract(messages)
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
            # 提取新记忆后，检查是否应触发维护
            if entries:
                await self._maybe_run_memory_maintenance()
            return entries
        except Exception:
            logger.exception("持久记忆提取或保存失败，已跳过")
            return []

    async def _maybe_run_memory_maintenance(self) -> None:
        """条件性触发记忆维护代理（合并/删除/改写）。"""
        if not self._config.memory_maintenance_enabled:
            return
        if self._persistent_memory is None:
            return

        from excelmanus.memory_maintainer import MemoryMaintainer

        if not MemoryMaintainer.should_run(
            self._persistent_memory,
            min_entries=self._config.memory_maintenance_min_entries,
            new_threshold=self._config.memory_maintenance_new_threshold,
            interval_hours=self._config.memory_maintenance_interval_hours,
        ):
            return

        # 模型优先级: memory_maintenance_model > aux_model > 主模型
        client = getattr(self, "_aux_client", None) or self._client
        model = (
            self._config.memory_maintenance_model
            or getattr(self, "_aux_model", None)
            or self._model
        )
        maintainer = MemoryMaintainer(client, model)
        try:
            result = await maintainer.maintain(self._persistent_memory)
            logger.info(
                "记忆维护完成: kept=%d deleted=%d rewritten=%d merged=%d",
                result.kept, result.deleted, result.rewritten, result.merged,
            )
        except Exception:
            logger.debug("记忆维护执行失败，已跳过", exc_info=True)

    async def initialize_mcp(self) -> None:
        """异步初始化 MCP 连接（需在 event loop 中调用）。

        由 CLI 或 API 入口在启动时显式调用。

        注意：
        MCP 仅负责工具注册；Skill 仅负责策略与授权。
        """
        await self._mcp_manager.initialize(self._registry)
        self.sync_mcp_auto_approve()
        # 注册并发搜索工具（依赖 MCPManager 已初始化）
        self._register_search_tools()
        # 注册重试回调：Exa 后台重试成功后补注册 parallel_search + auto_approve
        self._mcp_manager._on_builtin_retry_success.append(
            self._on_mcp_retry_success,
        )

    def _on_mcp_retry_success(self) -> None:
        """MCP 内置 Server 重试成功后的回调。"""
        self.sync_mcp_auto_approve()
        self._register_search_tools()

    def _register_search_tools(self) -> None:
        """注册并发搜索工具（需在 MCP 初始化后调用，幂等）。

        仅当 Exa 搜索可用时注册 parallel_search，避免无用工具占位。
        已注册时跳过，保证幂等性。
        """
        if "exa" not in self._mcp_manager.connected_servers:
            return
        if self._registry.get_tool("parallel_search") is not None:
            return
        from excelmanus.tools.search_tools import get_tools as get_search_tools
        search_tools = get_search_tools(self._mcp_manager)
        if search_tools:
            self._registry.register_tools(search_tools)
            # parallel_search 是只读搜索工具，加入自动批准
            for t in search_tools:
                self._approval.register_read_only_safe_tools([t.name])

    def sync_mcp_auto_approve(self) -> None:
        """将当前 MCP 白名单同步到审批管理器。"""
        auto_approved = self._mcp_manager.auto_approved_tools
        if auto_approved:
            self._approval.register_mcp_auto_approve(auto_approved)
            self._approval.register_read_only_safe_tools(auto_approved)

    async def warmup_prompt_cache(self) -> None:
        """异步预热 Anthropic prompt cache（fire-and-forget）。

        仅对 ClaudeClient 实例生效。发送一个只含稳定 system prompt 前缀
        + 最小 user 消息的请求（max_tokens=1），使稳定前缀进入 cache。
        后续真实请求即可 cache HIT，大幅降低首次 TTFT。
        """
        from excelmanus.providers.claude import ClaudeClient
        if not isinstance(self._client, ClaudeClient):
            return
        stable_prompt = self._context_builder._build_stable_system_prompt()
        if not stable_prompt or len(stable_prompt) < 100:
            return
        messages = [
            {"role": "system", "content": stable_prompt},
            {"role": "user", "content": "hi"},
        ]
        try:
            await self._client.chat.completions.create(
                model=self._active_model,
                messages=messages,
                max_tokens=1,
            )
            logger.info("prompt cache 预热完成: stable_prefix=%d chars", len(stable_prompt))
        except Exception:
            logger.debug("prompt cache 预热失败，跳过", exc_info=True)

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

    def push_guide_message(self, message: str) -> None:
        """外部注入引导消息，当前步不读，下一步开始时并入 user 消息。"""
        text = str(message or "").strip()
        if text:
            self._guide_messages.append(text)

    def drain_guide_messages(self) -> list[str]:
        """取出并清空引导消息队列。"""
        msgs, self._guide_messages = self._guide_messages, []
        return msgs

    def push_interrupt_message(self, message: str) -> None:
        """飞行中的用户插话：当前步不重组提示词，下一步并入 user 消息。"""
        text = str(message or "").strip()
        if text:
            self._interrupt_queue.append(text)

    def drain_interrupt_messages(self) -> list[str]:
        msgs, self._interrupt_queue = self._interrupt_queue, []
        return msgs

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

    def _extract_provider_label(self) -> str:
        """从 active_base_url 安全提取 provider 名称（如 openai/deepseek）。"""
        url = self._active_base_url or ""
        if not url:
            return ""
        try:
            host = url.split("//")[-1].split("/")[0].split(":")[0]
            parts = host.split(".")
            return parts[-2] if len(parts) >= 2 else host
        except Exception:
            return ""

    def update_aux_config(
        self,
        *,
        aux_enabled: bool = True,
        aux_model: str | None = None,
        aux_api_key: str | None = None,
        aux_base_url: str | None = None,
    ) -> None:
        """热更新 AUX 配置（子代理默认模型、上下文压缩等附属任务）。

        当前端通过 API 修改 AUX 配置时，由 SessionManager 广播调用，
        确保已存活的引擎实例不会使用过时的 AUX 快照。
        """
        from dataclasses import replace as _dc_replace
        self._config = _dc_replace(
            self._config,
            aux_enabled=aux_enabled,
            aux_model=aux_model,
            aux_api_key=aux_api_key,
            aux_base_url=aux_base_url,
        )
        self._llm_clients.update_aux(
            self._config,
            aux_enabled=aux_enabled,
            aux_model=aux_model,
            aux_api_key=aux_api_key,
            aux_base_url=aux_base_url,
        )
        # 同步到兼容属性
        self._router_client = self._llm_clients.router_client
        self._router_model = self._llm_clients.router_model
        self._router_follow_active_model = self._llm_clients.router_follow_active_model

    def get_compaction_status(self) -> dict[str, Any]:
        """返回上下文压缩状态，供 API 层查询。

        max_tokens / usage_ratio 以 ContextBudget 为准，避免 CompactionManager
        持有的 config 快照与设置页、当前模型窗口脱节。
        """
        status = self._compaction_manager.get_status(
            self._memory, self._last_system_msgs,
        )
        max_tokens = self.max_context_tokens
        status["max_tokens"] = max_tokens
        current = int(status.get("current_tokens") or 0)
        status["usage_ratio"] = (
            round(current / max_tokens, 3) if max_tokens > 0 else 0.0
        )
        return status

    def _sync_context_window_consumers(self) -> None:
        """将 ContextBudget 的有效窗口同步到 memory / compaction。"""
        tokens = self.max_context_tokens
        self._memory.update_context_window(tokens)
        self._compaction_manager.max_context_tokens = tokens

    def apply_context_optimization(
        self,
        *,
        max_context_tokens: int | None = None,
        compaction_enabled: bool | None = None,
        compaction_threshold_ratio: float | None = None,
    ) -> None:
        """热更新上下文窗口 / 压缩配置（由 SessionManager 广播调用）。

        引擎持有 config 的 replace() 副本，设置页只改全局 get_config()
        不会自动传到已打开的对话，必须显式同步。
        """
        if max_context_tokens is not None:
            tokens = max(1, int(max_context_tokens))
            object.__setattr__(self._config, "max_context_tokens", tokens)
            self._context_budget.set_base_tokens(tokens)
            self._sync_context_window_consumers()
        if compaction_enabled is not None:
            object.__setattr__(self._config, "compaction_enabled", compaction_enabled)
            self._compaction_manager.enabled = compaction_enabled
        if compaction_threshold_ratio is not None:
            object.__setattr__(
                self._config, "compaction_threshold_ratio", compaction_threshold_ratio,
            )

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

    def _load_persisted_full_access(self, database: "Database | None") -> bool:
        """从用户级配置读取持久化的 full_access 开关（跨会话继承）。"""
        if database is None:
            return False
        try:
            from excelmanus.stores.config_store import UserConfigStore
            store = UserConfigStore(database.conn)
            return store.get_full_access()
        except Exception:
            logger.debug("读取持久化 full_access 失败", exc_info=True)
            return False

    def _persist_full_access(self, enabled: bool) -> None:
        """将 full_access 开关持久化到用户级配置（跨会话生效）。"""
        if self._database is None:
            return
        try:
            from excelmanus.stores.config_store import UserConfigStore
            store = UserConfigStore(self._database.conn)
            store.set_full_access(enabled)
        except Exception:
            logger.debug("持久化 full_access 失败", exc_info=True)

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
    def active_model(self) -> str:
        """当前活跃模型标识符（Protocol: EngineConfig）。"""
        return self._active_model

    @property
    def is_vision_capable(self) -> bool:
        """主模型是否支持视觉（Protocol: VisionContext）。"""
        return self._is_vision_capable

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

    def enable_bench_sandbox(self) -> None:
        """启用 benchmark 沙盒模式：解除所有交互式阻塞。

        - fullaccess = True：高风险工具直接执行，不弹确认
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

    def _ingest_mention_versions(self, mention_contexts: list[ResolvedMention] | None) -> None:
        """把 @file 提及携带的 content_version 写入本轮 seen / session。"""
        from excelmanus.workbook_commit import export_seen_versions, remember_content_version

        if mention_contexts:
            for rm in mention_contexts:
                mention = getattr(rm, "mention", None)
                if mention is None or getattr(mention, "kind", None) != "file":
                    continue
                version = getattr(mention, "content_version", None)
                if version:
                    remember_content_version(mention.value, version)
                    self.state.remember_file_version(mention.value, version)
        for path, version in export_seen_versions().items():
            self.state.remember_file_version(path, version)

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
        channel: str | None = None,
    ) -> ChatResult:
        """入口：斜杠与待答处理后进入步循环。不在这里探查工作簿或分析任务意图。"""
        self._question_resolver = question_resolver
        self._channel_context = channel
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
            # 后续 read_image 同一文件时可跳过重复注入
            from excelmanus.engine_core.tool_dispatcher import _image_content_hash_b64
            for img in normalized_images:
                _h = _image_content_hash_b64(img["data"])
                self._tool_dispatcher._injected_image_hashes.add(_h)
                logger.debug("前端附件 hash 已注册: %s", _h)

        # ── 视觉能力前置检查：附件只交给主模型阅读 ──
        if normalized_images and not self._is_vision_capable:
            reject_msg = (
                "当前主模型不支持图片识别，无法处理图片附件。\n\n"
                "请切换到支持视觉的主模型，或设置 `EXCELMANUS_MAIN_MODEL_VISION=true`。"
            )
            logger.warning(
                "拒绝图片请求: main_vision=%s",
                self._is_vision_capable,
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

            for image in normalized_images:
                parts.append({
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{image['media_type']};base64,{image['data']}",
                        "detail": image["detail"],
                    },
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

        # 待审批只卡住同一 tool_call_id 的回执路径，不阻塞无关的新用户回合。

        chat_start = time.monotonic()
        # 每次真正的 chat 调用递增轮次计数器
        self._state.increment_turn()
        self._current_chat_mode = chat_mode
        self._tools_cache = None  # 新 turn → 失效工具 schema 缓存

        effective_slash_command = slash_command
        effective_raw_args = raw_args or ""

        # 显式斜杠 / 直接 chat("/skill")；不是词法任务路由。
        if effective_slash_command is None:
            manual_skill_with_args = self._skill_resolver.resolve_skill_command_with_args(user_message)
            if manual_skill_with_args is not None:
                effective_slash_command, effective_raw_args = manual_skill_with_args

        if effective_slash_command is None and mention_contexts:
            for rm in mention_contexts:
                if rm.mention.kind == "skill" and not rm.error:
                    effective_slash_command = rm.mention.value
                    parse_result = MentionParser.parse(user_message)
                    effective_raw_args = parse_result.clean_text
                    break

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

        # 已激活 skill 只注入 instructions，完整资源在 activate_skill 的 tool result 里。
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
        )
        self._last_route_result = route_result

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

        # 追加用户消息
        _add_user_turn_to_memory(user_message)
        logger.info(
            "用户指令摘要: %s | skills=%s",
            _summarize_text(user_message),
            route_result.skills_used,
        )

        # 仅新任务重置执行守卫；同任务续跑需保留状态，避免重复注入提示。
        self._execution_guard_fired = False
        self._vba_exempt = _user_requests_vba(user_message)
        # 存储 mention 上下文供 _tool_calling_loop 注入系统提示词
        self._mention_contexts = mention_contexts
        self._ingest_mention_versions(mention_contexts)

        _pre_loop_ms = (time.monotonic() - chat_start) * 1000
        if _pre_loop_ms > 1000:
            logger.info("perf.chat: pre-loop %.0fms", _pre_loop_ms)
        else:
            logger.debug("perf.chat: pre-loop total %.0fms", _pre_loop_ms)

        chat_result = await self._tool_calling_loop(
            route_result, on_event,
            approval_resolver=approval_resolver,
            question_resolver=question_resolver,
        )

        # 注入路由诊断信息到 ChatResult。
        # tool_access 仅回显用户选定的 chat_mode 对应的工具可见性，不猜测写入意图。
        chat_result.tool_access = _tool_access_from_chat_mode(
            getattr(self, "_current_chat_mode", "write"),
        )
        chat_result.route_mode = route_result.route_mode
        chat_result.skills_used = list(route_result.skills_used)
        chat_result.turn_diagnostics = list(self._turn_diagnostics)

        # 累积到会话级诊断
        # 获取本轮提示词注入摘要
        _injection_summary_for_diag: list[dict[str, Any]] = []
        if self._state.prompt_injection_snapshots:
            _latest = self._state.prompt_injection_snapshots[-1]
            if _latest.get("session_turn") == self._session_turn:
                _injection_summary_for_diag = _latest.get("summary", [])
        _session_diag: dict[str, Any] = {
            "session_turn": self._session_turn,
            "tool_access": chat_result.tool_access,
            "route_mode": route_result.route_mode,
            "skills_used": list(route_result.skills_used),
            "iterations": chat_result.iterations,
            "prompt_tokens": chat_result.prompt_tokens,
            "completion_tokens": chat_result.completion_tokens,
            "total_tokens": chat_result.total_tokens,
            "write_guard_triggered": chat_result.write_guard_triggered,
            "turn_diagnostics": [d.to_dict() for d in self._turn_diagnostics],
            "prompt_injection_summary": _injection_summary_for_diag,
        }
        self._session_diagnostics.append(_session_diag)

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
            tool_result_enricher=None,
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
        from excelmanus.auth.providers.openai_codex import OpenAICodexProvider as _OpenAICodexProvider

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
                "recommended_level": _s.recommended_reasoning_level,
                "level_mismatch_count": _s.reasoning_level_mismatch_count,
                "upgrade_nudge_count": _s.reasoning_upgrade_nudge_count,
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
            if self._tool_dispatcher is not None:
                self._tool_dispatcher.reset_cancel()
                self._tool_dispatcher.begin_call_budget(None)
        tool_access = _tool_access_from_chat_mode(
            getattr(self, "_current_chat_mode", "write"),
        )
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

            await self._refresh_credential_if_needed(on_event=on_event)

            if iteration == start_iteration:
                self._emit(
                    on_event,
                    ToolCallEvent(
                        event_type=EventType.PIPELINE_PROGRESS,
                        pipeline_stage="preparing",
                        pipeline_message="正在准备本轮",
                    ),
                )
            else:
                self._emit(
                    on_event,
                    ToolCallEvent(
                        event_type=EventType.PIPELINE_PROGRESS,
                        pipeline_stage="calling_model",
                        pipeline_message="正在调用模型",
                    ),
                )

            _ctx_start = time.monotonic()
            system_prompts, context_error = self._context_builder._prepare_system_prompts_for_request(
                current_route_result.system_contexts,
                route_result=current_route_result,
            )
            if iteration == start_iteration:
                logger.debug("perf.loop: context_build %.0fms", (time.monotonic() - _ctx_start) * 1000)
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

            # 步前压缩：只在下一步模型调用前判断 pressure。
            # 先剪过长 tool 结果，再摘要；摘要没替换 surface 不当成功，也不重跑本步。
            if iteration > start_iteration:
                _sys_msgs = self._memory.build_system_messages(system_prompts)
                self._last_system_msgs = _sys_msgs
                if self._compaction_manager.should_compact(self._memory, _sys_msgs):
                    self._emit(
                        on_event,
                        ToolCallEvent(
                            event_type=EventType.PIPELINE_PROGRESS,
                            pipeline_stage="compacting",
                            pipeline_message="正在压缩上下文...",
                        ),
                    )
                    _summary_model = self._config.aux_model or self._active_model
                    _msgs_before_compact = len(self._memory.messages)
                    _compact_result = await self._compaction_manager.auto_compact(
                        memory=self._memory,
                        system_msgs=_sys_msgs,
                        client=self._client,
                        summary_model=_summary_model,
                    )
                    if not _compact_result.success:
                        logger.warning(
                            "步前压缩未替换摘要: %s",
                            _compact_result.error or "unknown",
                        )
                    if len(self._memory.messages) != _msgs_before_compact:
                        self._history_snapshot_index = 0

            # 步边界才并入插话 / guide，不在模型生成中途插 system。
            _queued_user = self.drain_interrupt_messages()
            _queued_user.extend(self.drain_guide_messages())
            for _queued in _queued_user:
                self._memory.add_user_message(_queued)
            if _queued_user:
                logger.info("下一步并入 %d 条插话/guide", len(_queued_user))

            messages = self._memory.trim_for_request(
                system_prompts=system_prompts,
                max_context_tokens=self.max_context_tokens,
            )

            # 分层 schema（core=完整, extended=摘要/已展开=完整）
            tools = self._meta_tool_builder.build_v5_tools(
                tool_access=tool_access,
            )
            tool_scope = None

            # 安全网：确保发送到 API 的 model 是实际模型 ID，不含 provider 前缀
            _api_model = self._active_model
            if _OpenAICodexProvider.is_codex_profile_name(_api_model):
                _api_model = _OpenAICodexProvider.model_from_profile_name(_api_model) or _api_model

            kwargs: dict[str, Any] = {
                "model": _api_model,
                "messages": messages,
            }
            if tools:
                kwargs["tools"] = tools

            # 注入 thinking 参数
            # 优先级：profile.thinking_mode > caps.thinking_type > 默认
            caps = self._model_capabilities
            tc = self._thinking_config
            _profile = self._active_profile
            _profile_thinking_mode = getattr(_profile, "thinking_mode", "auto") if _profile else "auto"

            if _profile_thinking_mode not in ("auto", ""):
                # 用户显式指定了 thinking_mode
                _effective_ttype = _profile_thinking_mode if _profile_thinking_mode != "disabled" else ""
            elif caps and caps.supports_thinking:
                _effective_ttype = caps.thinking_type
            else:
                _effective_ttype = ""

            budget = tc.effective_budget()
            if _effective_ttype == "claude":
                kwargs["_thinking_enabled"] = not tc.is_disabled
                kwargs["_thinking_budget"] = budget if not tc.is_disabled else 0
                kwargs["_thinking_effort"] = tc.claude_effort
            elif not tc.is_disabled:
                if _effective_ttype == "claude_compat":
                    extra = kwargs.get("extra_body", {})
                    from excelmanus.providers.claude import uses_adaptive_thinking
                    if uses_adaptive_thinking(str(_api_model)):
                        extra["thinking"] = {"type": "adaptive"}
                        extra["output_config"] = {"effort": tc.claude_effort}
                    else:
                        extra["thinking"] = {"type": "enabled", "budget_tokens": budget}
                    kwargs["extra_body"] = extra
                elif _effective_ttype == "gemini":
                    kwargs["_thinking_budget"] = budget
                elif _effective_ttype == "gemini_level":
                    kwargs["_thinking_level"] = tc.gemini_level
                elif _effective_ttype == "openai_reasoning":
                    kwargs["reasoning_effort"] = tc.openai_effort
                elif _effective_ttype == "enable_thinking":
                    extra = kwargs.get("extra_body", {})
                    extra["enable_thinking"] = True
                    extra["thinking_budget"] = budget
                    kwargs["extra_body"] = extra
                elif _effective_ttype == "glm_thinking":
                    extra = kwargs.get("extra_body", {})
                    extra["thinking"] = {"type": "enabled"}
                    extra["reasoning_effort"] = tc.openai_effort
                    kwargs["extra_body"] = extra
                elif _effective_ttype == "openrouter":
                    extra = kwargs.get("extra_body", {})
                    extra["reasoning"] = {
                        "effort": tc.openai_effort,
                        "max_tokens": budget,
                    }
                    kwargs["extra_body"] = extra
                # "deepseek" / "reasoning_content_auto" → 模型自动输出推理内容，无需额外参数

            # 注入 profile 自定义 extra_body / extra_headers
            if _profile:
                import json as _json
                if _profile.custom_extra_body:
                    try:
                        _ceb = _json.loads(_profile.custom_extra_body)
                        if isinstance(_ceb, dict):
                            merged = kwargs.get("extra_body", {})
                            merged.update(_ceb)
                            kwargs["extra_body"] = merged
                    except (ValueError, TypeError):
                        pass
                if _profile.custom_extra_headers:
                    try:
                        _ceh = _json.loads(_profile.custom_extra_headers)
                        if isinstance(_ceh, dict):
                            merged = kwargs.get("extra_headers", {})
                            merged.update(_ceh)
                            kwargs["extra_headers"] = merged
                    except (ValueError, TypeError):
                        pass

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
                        pipeline_stage="calling_model",
                        pipeline_message="正在调用模型",
                    ),
                )
            _llm_start_ts = time.monotonic()
            stream_kwargs = dict(kwargs)
            stream_kwargs["stream"] = True
            if isinstance(self._client, openai.AsyncOpenAI):
                stream_kwargs["stream_options"] = {"include_usage": True}

            # ── LLM 调用 + 5xx/429 自动重试 ──
            _retry_max = self._config.llm_retry_max_attempts
            _retry_base = self._config.llm_retry_base_delay_seconds
            _retry_cap = self._config.llm_retry_max_delay_seconds
            _auth_refresh_attempted = False  # 401 时仅尝试一次凭证刷新重试
            for _retry_attempt in range(1, _retry_max + 1):
                try:
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
                        # 可重试的瞬时错误 → 跳过非流式回退，直接进入重试
                        if is_retryable_llm_error(stream_exc):
                            raise
                        # 认证/权限错误回退无意义：同样会在非流式再次失败
                        if is_nonretryable_auth_error(stream_exc):
                            raise
                        # 内容安全策略拦截回退无意义：非流式同样会被拦截
                        if is_content_filter_error(stream_exc):
                            raise
                        # 流式调用失败时回退到非流式
                        logger.warning("流式调用失败，回退到非流式: %s", stream_exc)
                        response = await self._llm_caller.create_chat_completion_with_system_fallback(kwargs)
                        message, usage = _extract_completion_message(response)

                    # 成功 — 若经历过重试则通知前端
                    if _retry_attempt > 1:
                        self._emit(
                            on_event,
                            ToolCallEvent(
                                event_type=EventType.LLM_RETRY,
                                retry_status="succeeded",
                                retry_attempt=_retry_attempt,
                                retry_max_attempts=_retry_max,
                            ),
                        )
                    break  # 成功，退出重试循环

                except Exception as _retry_exc:
                    # ── 内容安全策略拦截：不可重试，立即通知前端 ──
                    if is_content_filter_error(_retry_exc):
                        self._emit(
                            on_event,
                            _failure_guidance_event(_classify_failure(
                                _retry_exc,
                                stage="calling_llm",
                                provider=self._extract_provider_label(),
                                model=self._active_model or "",
                            )),
                        )
                        raise

                    # ── 401/403 认证错误：尝试刷新凭证后重试一次 ──
                    if is_nonretryable_auth_error(_retry_exc) and not _auth_refresh_attempted:
                        _auth_refresh_attempted = True
                        # 诊断日志：记录当前使用的凭证信息
                        _key_preview = (self._active_api_key or "")[:20]
                        logger.warning(
                            "401 诊断: model=%s, base_url=%s, api_key_prefix=%s..., "
                            "has_resolver=%s",
                            self._active_model, self._active_base_url,
                            _key_preview, self._credential_resolver is not None,
                        )
                        _old_key = self._active_api_key
                        try:
                            await self._refresh_credential_if_needed(on_event=on_event)
                        except Exception:
                            logger.debug("401 后凭证刷新失败", exc_info=True)
                        if self._active_api_key != _old_key:
                            logger.info(
                                "401 后凭证已刷新，重试 LLM 调用 (attempt=%d)",
                                _retry_attempt,
                            )
                            self._emit(
                                on_event,
                                ToolCallEvent(
                                    event_type=EventType.PIPELINE_PROGRESS,
                                    pipeline_stage="credential_refreshed_retrying",
                                    pipeline_message="认证失败，已自动刷新凭证，正在重试...",
                                ),
                            )
                            # 用新凭证重建请求参数中的客户端引用
                            continue
                        # 凭证未变化，无法恢复
                        logger.warning(
                            "401 后凭证刷新未产生新 token，无法恢复: %s",
                            str(_retry_exc)[:200],
                        )
                        raise

                    if _retry_attempt < _retry_max and is_retryable_llm_error(_retry_exc):
                        _delay = compute_retry_delay(
                            _retry_attempt, _retry_base, _retry_cap, _retry_exc,
                        )
                        _err_brief = str(_retry_exc)[:200]
                        logger.warning(
                            "LLM 调用失败（可重试），%0.1f 秒后第 %d/%d 次重试: %s",
                            _delay, _retry_attempt, _retry_max - 1, _err_brief,
                        )
                        # 通知前端：正在重试
                        self._emit(
                            on_event,
                            ToolCallEvent(
                                event_type=EventType.LLM_RETRY,
                                retry_status="retrying",
                                retry_attempt=_retry_attempt,
                                retry_max_attempts=_retry_max,
                                retry_delay_seconds=_delay,
                                retry_error_message=_err_brief,
                            ),
                        )
                        self._emit(
                            on_event,
                            ToolCallEvent(
                                event_type=EventType.PIPELINE_PROGRESS,
                                pipeline_stage="llm_retrying",
                                pipeline_message=(
                                    f"模型服务暂时不可用，{_delay:.0f}秒后"
                                    f"第 {_retry_attempt}/{_retry_max - 1} 次重试..."
                                ),
                            ),
                        )
                        await asyncio.sleep(_delay)
                        # 重试前重新发射 calling_llm 进度
                        self._emit(
                            on_event,
                            ToolCallEvent(
                                event_type=EventType.PIPELINE_PROGRESS,
                                pipeline_stage="calling_llm",
                                pipeline_message=f"正在重试与模型通信（第 {_retry_attempt + 1}/{_retry_max} 次尝试）...",
                            ),
                        )
                        continue

                    # 不可重试或重试次数耗尽
                    if _retry_attempt >= _retry_max and is_retryable_llm_error(_retry_exc):
                        self._emit(
                            on_event,
                            ToolCallEvent(
                                event_type=EventType.LLM_RETRY,
                                retry_status="exhausted",
                                retry_attempt=_retry_attempt,
                                retry_max_attempts=_retry_max,
                                retry_error_message=str(_retry_exc)[:200],
                            ),
                        )
                    raise

            # 流式截断检测：consume_stream 因连续 chunk 解析错误而中止
            if getattr(message, "_stream_truncated", False):
                logger.warning(
                    "流式响应因连续 chunk 解析错误而被截断，输出可能不完整 (content_len=%d)",
                    len(getattr(message, "content", "") or ""),
                )
                self._emit(
                    on_event,
                    _failure_guidance_event(_classify_failure(
                        RuntimeError("流式响应解析中断：连续多个数据块解析失败"),
                        stage="streaming",
                        provider=self._extract_provider_label(),
                        model=self._active_model or "",
                    )),
                )

            tool_calls = _normalize_tool_calls(getattr(message, "tool_calls", None))

            # ── 文本工具调用恢复 ──────────────────────────────────
            # 部分模型（如 DeepSeek）将工具调用以纯文本 JSON 输出到
            # content 中，而非使用 API 的 tool_calls 机制。
            # 检测并恢复为正规工具调用，避免用户看到原始 JSON。
            _text_tc_recovered = False
            if not tool_calls:
                _raw_content = _message_content_to_text(
                    getattr(message, "content", None),
                )
                _registered = set(self._context_builder._all_tool_names())
                _recovered_calls, _cleaned_content = _extract_text_tool_calls(
                    _raw_content, _registered,
                )
                if _recovered_calls:
                    _text_tc_recovered = True
                    logger.info(
                        "文本工具调用恢复: %d 个 [%s]",
                        len(_recovered_calls),
                        ", ".join(tc.function.name for tc in _recovered_calls),
                    )
                    tool_calls = _recovered_calls
                    message = SimpleNamespace(
                        content=_cleaned_content,
                        tool_calls=_recovered_calls,
                        thinking=getattr(message, "thinking", None),
                        reasoning=getattr(message, "reasoning", None),
                        reasoning_content=getattr(message, "reasoning_content", None),
                        _thinking_streamed=getattr(message, "_thinking_streamed", False),
                        _stream_truncated=getattr(message, "_stream_truncated", False),
                    )
                    self._emit(
                        on_event,
                        ToolCallEvent(
                            event_type=EventType.PIPELINE_PROGRESS,
                            pipeline_stage="text_tool_recovery",
                            pipeline_message="检测到文本格式工具调用，正在恢复执行...",
                        ),
                    )

            _llm_elapsed_ms = (time.monotonic() - _llm_start_ts) * 1000
            _tc_names = [getattr(getattr(tc, "function", None), "name", "?") for tc in (tool_calls or [])]
            if iteration == start_iteration:
                logger.info(
                    "perf.loop: first_llm_call %.0fms → tools=%s",
                    _llm_elapsed_ms, _tc_names or "text_reply",
                )
            else:
                logger.debug(
                    "perf.loop: llm_call iter=%d %.0fms → tools=%s",
                    iteration, _llm_elapsed_ms, _tc_names or "text_reply",
                )

            # 图片生命周期：视觉模型保留图片利用 Provider 缓存，非视觉模型立即降级
            if self._is_vision_capable:
                self._memory.manage_image_lifecycle()
            else:
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

            # /reasoning 开启时额外发射推理内容通知
            if thinking_content and self._show_reasoning:
                self._emit(
                    on_event,
                    ToolCallEvent(
                        event_type=EventType.REASONING_NOTICE,
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
                text_tool_call_recovered=_text_tc_recovered,
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

            # 无工具调用 → 纯文本回复处理（仅 HTML 端点错误检测）
            if not tool_calls:
                text_action, text_result = self._handle_text_reply(
                    message=message,
                    iteration=iteration,
                    all_tool_results=all_tool_results,
                    total_prompt_tokens=total_prompt_tokens,
                    total_completion_tokens=total_completion_tokens,
                    _finalize_result=_finalize_result,
                )
                if text_action == "return":
                    return text_result

            assistant_msg = _assistant_message_to_dict(message)
            if tool_calls:
                assistant_msg["tool_calls"] = [_to_plain(tc) for tc in tool_calls]
            self._memory.add_assistant_tool_message(assistant_msg)

            # ── Think-Act 推理检测（级别感知，不阻断执行） ──
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

            # 推理级别匹配检测：有推理时检查深度是否匹配推荐级别
            if _has_reasoning and _tc_count > 0:
                _avg_chars = _reasoning_chars / _tc_count
                _rec_level = self._state.recommended_reasoning_level
                _thresholds = self._context_builder._REASONING_CHARS_THRESHOLDS
                _min_chars = _thresholds.get(_rec_level, 5)
                if _avg_chars < _min_chars:
                    self._state.reasoning_level_mismatch_count += 1

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
                _parallel_names = PARALLELIZABLE_READONLY_TOOLS
                # MCP auto-approved 工具（如 Exa 搜索）也可并行
                _mcp_auto = self._mcp_manager.auto_approved_tools
                if _mcp_auto:
                    _parallel_names = _parallel_names | frozenset(_mcp_auto)
                _batches = _split_tool_call_batches(tool_calls, _parallel_names)
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

                        all_tool_results.append(tc_result)

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

                        all_tool_results.append(tc_result)

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
                                approval_id = tc_result.approval_id or pending.approval_id
                                logger.info("内联审批等待决策: %s", approval_id)
                                try:
                                    decision = await approval_resolver(pending)
                                except Exception as _resolver_exc:  # noqa: BLE001
                                    logger.warning("approval_resolver 异常，视为 reject: %s", _resolver_exc)
                                    decision = None

                                updates, _wrote = await self._apply_approval_decision(
                                    decision, pending, approval_id,
                                    tool_call_id, on_event, iteration, "内联审批",
                                )
                                tc_result = replace(tc_result, **updates)
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
                                    updates, _wrote = await self._apply_approval_decision(
                                        decision, pending, approval_id,
                                        tool_call_id, on_event, iteration, "Web 审批",
                                    )
                                    tc_result = replace(tc_result, **updates)

                        # 更新统计
                        self._last_tool_call_count += 1
                        if tc_result.success:
                            self._last_success_count += 1
                            consecutive_failures = 0
                            _write_effect = self._get_tool_write_effect(tc_result.tool_name)
                            if _write_effect == "workspace_write":
                                self._record_workspace_write_action()
                            elif _write_effect == "external_write":
                                self._record_external_write_action()
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
        all_tool_results: list,
        total_prompt_tokens: int,
        total_completion_tokens: int,
        _finalize_result: Any,
    ) -> tuple[str, Any]:
        """处理 LLM 返回纯文本（无 tool_calls）的情况。

        纯文本一律结束本轮。仅保留 HTML 整页响应检测：那是 LLM 客户端
        配置错误（base_url 指到了网页），不是对回复内容的行为判断。
        """
        reply_text = _message_content_to_text(getattr(message, "content", None))

        if _looks_like_html_document(reply_text):
            error_reply = self._format_html_endpoint_error(reply_text)
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
            tc_id = getattr(tc, "id", "")
            tc_name = getattr(func, "name", "")
            self._emit(
                on_event,
                ToolCallEvent(
                    event_type=EventType.TOOL_CALL_START,
                    tool_call_id=tc_id,
                    tool_name=tc_name,
                    arguments=args,
                    iteration=iteration,
                ),
            )
            # /tools 开启时额外发射简要工具调用通知
            if self._show_tool_calls:
                self._emit(
                    on_event,
                    ToolCallEvent(
                        event_type=EventType.TOOL_CALL_NOTICE,
                        tool_call_id=tc_id,
                        tool_name=tc_name,
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
    ) -> tuple[Any, AppliedApprovalRecord]:
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

    async def _apply_approval_decision(
        self,
        decision: str | None,
        pending: PendingApproval,
        approval_id: str,
        tool_call_id: str | None,
        on_event: EventCallback | None,
        iteration: int,
        source: str,
    ) -> tuple[Any, bool]:
        """处理审批决策（accept/reject/fullaccess），返回 (updated_tc_result_kwargs, write_happened)。

        统一内联审批和 Web 审批的 accept/reject 逻辑。
        返回 (dict_for_replace, write_happened) — 调用方使用 replace(tc_result, **dict_for_replace)。
        """
        from excelmanus.events import EventType, ToolCallEvent

        write_happened = False
        if decision in ("accept", "fullaccess"):
            if decision == "fullaccess":
                self._full_access_enabled = True
                logger.info("%s: fullaccess 已开启", source)
            exec_ok, exec_result, exec_record = await self._execute_approved_pending(
                pending, on_event=on_event, tool_call_id=tool_call_id,
            )
            if tool_call_id:
                self._memory.replace_tool_result(tool_call_id, exec_result)
            self._emit(
                on_event,
                ToolCallEvent(
                    event_type=EventType.APPROVAL_RESOLVED,
                    tool_call_id=tool_call_id or "",
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
            if exec_ok and exec_record is not None:
                _effect = self._get_tool_write_effect(pending.tool_name)
                if exec_record.changes or _effect == "workspace_write":
                    self._record_workspace_write_action()
                    write_happened = True
                elif _effect == "external_write":
                    self._record_external_write_action()
                    write_happened = True
            logger.info(
                "%s完成: decision=%s ok=%s tool=%s",
                source, decision, exec_ok, pending.tool_name,
            )
            return dict(
                pending_approval=False,
                success=exec_ok,
                result=exec_result,
                error=None if exec_ok else exec_result,
            ), write_happened
        else:
            reject_msg = self._approval.reject_pending(approval_id)
            if tool_call_id:
                self._memory.replace_tool_result(tool_call_id, reject_msg)
            self._emit(
                on_event,
                ToolCallEvent(
                    event_type=EventType.APPROVAL_RESOLVED,
                    tool_call_id=tool_call_id or "",
                    approval_id=approval_id,
                    approval_tool_name=pending.tool_name if pending else "",
                    result=reject_msg,
                    success=False,
                    iteration=iteration,
                ),
            )
            logger.info("%s拒绝: %s", source, approval_id)
            return dict(
                pending_approval=False,
                success=False,
                result=reject_msg,
                error=reject_msg,
            ), False

    async def _execute_approved_pending(
        self,
        pending: PendingApproval,
        *,
        on_event: EventCallback | None = None,
        tool_call_id: str | None = None,
    ) -> tuple[bool, str, AppliedApprovalRecord | None]:
        """执行待确认操作并处理副作用（写入追踪、CoW 映射等）。

        返回 (success, result_text, record)。
        共享逻辑：同时被 _handle_accept_command 和 _tool_calling_loop 内联审批使用。
        """
        try:
            payload, record = await self._execute_tool_with_audit(
                tool_name=pending.tool_name,
                arguments=pending.arguments,
                tool_scope=None,
                approval_id=pending.approval_id,
                created_at_utc=pending.created_at_utc,
                undoable=self._approval.is_undoable_tool(pending.tool_name),
                force_delete_confirm=True,
            )
        except ToolNotAllowedError:
            self._approval.clear_pending()
            msg = f"accept 执行失败：工具 `{pending.tool_name}` 当前不在授权范围内。"
            return False, msg, None
        except Exception as exc:  # noqa: BLE001
            self._approval.clear_pending()
            return False, f"accept 执行失败：{exc}", None

        from excelmanus.engine_core.tool_result import coerce_legacy_result

        structured = coerce_legacy_result(payload)
        self._tool_dispatcher._apply_ui_meta_effects(structured)
        if on_event is not None:
            self._tool_dispatcher._emit_ui_meta_events(
                self,
                on_event,
                tool_call_id or pending.approval_id,
                pending.tool_name,
                pending.arguments,
                structured.ui_meta,
                0,
            )
            changed: list[str] = list(structured.ui_meta.files or [])
            if structured.ui_meta.text_diff:
                fp = structured.ui_meta.text_diff.get("file_path")
                if isinstance(fp, str) and fp and fp not in changed:
                    changed.append(fp)
            if structured.ui_meta.cow_mapping:
                for dst in structured.ui_meta.cow_mapping.values():
                    if dst and dst not in changed:
                        changed.append(dst)
            if changed:
                self._emit(
                    on_event,
                    ToolCallEvent(
                        event_type=EventType.FILES_CHANGED,
                        tool_call_id=tool_call_id or pending.approval_id,
                        iteration=0,
                        changed_files=changed,
                    ),
                )

        if pending.tool_name == "run_code":
            from excelmanus.security.code_policy import extract_excel_targets
            _rc_code = pending.arguments.get("code") or ""
            _has_cow = bool(structured.ui_meta.cow_mapping)
            _has_ast_write = any(
                t.operation == "write"
                for t in extract_excel_targets(_rc_code)
            )
            if record.changes or _has_cow or _has_ast_write:
                self._record_workspace_write_action()
            if on_event is not None:
                self._tool_dispatcher._emit_files_changed_from_audit(
                    self, on_event, pending.approval_id,
                    pending.arguments.get("code") or "",
                    record.changes,
                    0,
                    cow_mapping=structured.ui_meta.cow_mapping,
                )

        self._approval.clear_pending()
        result_text = structured.model_text or record.result_preview or f"已执行 `{pending.tool_name}`。"
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
    def max_context_tokens(self) -> int:
        """当前有效的上下文窗口大小（token 数）。切换模型时自动更新。"""
        return self._context_budget.max_tokens

    @property
    def context_budget(self) -> ContextBudget:
        """上下文预算管理器（供外部组件读取）。"""
        return self._context_budget

    @property
    def current_model(self) -> str:
        """当前使用的模型标识符。"""
        return self._active_model

    @property
    def current_model_name(self) -> str | None:
        """当前激活的模型 profile 短名称，None 表示使用默认配置。"""
        return self._active_model_name

    def sync_model_profiles(self, profiles: tuple["ModelProfile", ...]) -> None:
        """热更新可用模型档案列表（由 SessionManager 广播调用）。"""
        object.__setattr__(self._config, "models", profiles)

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

    async def _refresh_credential_if_needed(self, on_event: "EventCallback | None" = None) -> None:
        """LLM 调用前检查 OAuth token 是否需要刷新（借鉴 OpenClaw resolveApiKeyForProfile）。

        如果 CredentialResolver 返回了不同于当前 api_key 的凭证，说明 token 已被刷新，
        此时热更新 _client 和相关字段，确保后续 LLM 调用使用新凭证。
        同时通过 SSE 通知前端 token 状态变化。
        """
        # 安全解析：如果 _active_model 含 provider 前缀（如 openai-codex/gpt-6-astra），
        # 先剥离为实际模型 ID（gpt-6-astra），避免发送无效 model 到 API。
        from excelmanus.auth.providers.openai_codex import OpenAICodexProvider
        if OpenAICodexProvider.is_codex_profile_name(self._active_model):
            _real_model = OpenAICodexProvider.model_from_profile_name(self._active_model)
            if _real_model:
                logger.info(
                    "修正 _active_model 前缀: %s -> %s",
                    self._active_model, _real_model,
                )
                self._active_model = _real_model

        resolver = self._credential_resolver
        if resolver is None:
            return
        try:
            resolved = await resolver.resolve(self._active_model)
        except Exception:
            logger.debug("OAuth 凭证刷新检查失败", exc_info=True)
            # 刷新失败，通知前端
            self._emit(
                on_event,
                ToolCallEvent(
                    event_type=EventType.CREDENTIAL_EXPIRED,
                    pipeline_stage="credential_expired",
                    pipeline_message="OAuth token 已过期且刷新失败，请重新连接",
                ),
            )
            return
        if resolved is None or resolved.source not in ("oauth", "pool_oauth"):
            # 非池来源时清零，防止前一次 pool_oauth 残留导致误记账
            self._pool_account_id = None
            self._pool_profile_name = None
            return

        # 记录或清零池账号信息供 api 层台账使用
        if resolved.source == "pool_oauth" and resolved.pool_account_id:
            self._pool_account_id = resolved.pool_account_id
            self._pool_profile_name = resolved.pool_profile_name
        else:
            self._pool_account_id = None
            self._pool_profile_name = None

        next_api_key = resolved.api_key or self._active_api_key
        next_base_url = resolved.base_url or self._active_base_url
        next_protocol = resolved.protocol or self._active_protocol

        if (
            next_api_key == self._active_api_key
            and next_base_url == self._active_base_url
            and next_protocol == self._active_protocol
        ):
            return  # 运行时凭证与路由协议均未变化

        # 凭证已刷新，热更新客户端
        logger.info("OAuth token 已刷新，热更新 LLM 客户端 (provider=%s)", resolved.provider)
        self._active_api_key = next_api_key
        self._active_base_url = next_base_url
        self._active_protocol = next_protocol
        self._client = create_client(
            api_key=self._active_api_key,
            base_url=self._active_base_url,
            protocol=self._active_protocol,
            model=self._active_model,
        )
        self._sync_router_model_runtime()
        # 通知前端 token 已刷新
        self._emit(
            on_event,
            ToolCallEvent(
                event_type=EventType.CREDENTIAL_REFRESHED,
                pipeline_stage="credential_refreshed",
                pipeline_message=f"OAuth token 已自动刷新 (provider={resolved.provider})",
            ),
        )

    def switch_model(self, name: str) -> str:
        """切换到指定模型档案。返回切换结果描述。

        支持智能匹配：精确匹配 > 前缀匹配 > 包含匹配。
        """
        name = name.strip()
        if not name:
            return "请指定模型名称。用法：/model <名称>，/model list 查看可用模型。"

        # 切换回默认
        if name.lower() == "default":
            deprecated_msg = format_deprecated_model_message(self._config.model)
            if deprecated_msg:
                return f"默认模型已弃用。{deprecated_msg}"
            # W1: 委托给 LLMClientManager 统一管理客户端切换
            self._llm_clients.switch_active_model(
                model=self._config.model,
                api_key=self._config.api_key,
                base_url=self._config.base_url,
                protocol=self._config.protocol,
                name=None,
            )
            self._active_model_name = None
            self._active_profile = None
            self._sync_from_llm_clients()
            self._sync_router_model_runtime()
            self._model_capabilities = None
            self._context_budget.update_for_model(self._config.model)
            self._sync_context_window_consumers()
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

        deprecated_msg = format_deprecated_model_message(matched.model)
        if deprecated_msg:
            return f"模型 {matched.name!r} 使用了已弃用 Model ID。{deprecated_msg}"

        # W1: 委托给 LLMClientManager 统一管理客户端切换
        self._llm_clients.switch_active_model(
            model=matched.model,
            api_key=matched.api_key,
            base_url=matched.base_url,
            protocol=matched.protocol,
            name=matched.name,
        )
        self._active_model_name = matched.name
        self._active_profile = matched
        self._sync_from_llm_clients()
        self._sync_router_model_runtime()
        self._model_capabilities = None
        self._context_budget.update_for_model(matched.model)
        self._sync_context_window_consumers()
        desc = f"（{matched.description}）" if matched.description else ""
        return f"已切换到模型：{matched.name} → {matched.model}{desc}"

    def _sync_from_llm_clients(self) -> None:
        """从 LLMClientManager 同步活跃模型状态到引擎本地字段。"""
        mgr = self._llm_clients
        self._active_model = mgr.active_model
        self._active_api_key = mgr.active_api_key
        self._active_base_url = mgr.active_base_url
        self._active_protocol = mgr.active_protocol
        self._client = mgr.main_client

    def _sync_router_model_runtime(self) -> None:
        """在主模型切换后同步路由模型运行时（仅跟随模式）。"""
        if not self._router_follow_active_model:
            return
        self._router_client = self._client
        self._router_model = self._active_model

    async def _adapt_guidance_only_slash_route(
        self,
        *,
        route_result: SkillMatchResult,
        user_message: str,
        slash_command: str | None,
        raw_args: str,
    ) -> tuple[SkillMatchResult, str]:
        """斜杠命中且带任务文本时：保留技能说明，使用全量工具目录进入循环。"""
        if not slash_command or route_result.route_mode != "slash_direct":
            return route_result, user_message

        task_text = raw_args.strip()
        if not task_text:
            return route_result, user_message

        adapted = SkillMatchResult(
            skills_used=list(route_result.skills_used),
            route_mode="all_tools",
            system_contexts=list(route_result.system_contexts),
            parameterized=route_result.parameterized,
        )
        logger.info(
            "斜杠技能 %s 带任务文本，注入技能说明后进入循环: %s",
            slash_command,
            _summarize_text(task_text),
        )
        return adapted, task_text

    async def _route_skills(
        self,
        user_message: str,
        *,
        slash_command: str | None = None,
        raw_args: str | None = None,
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
            chat_mode=chat_mode,
            on_event=on_event,
            images=images,
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
