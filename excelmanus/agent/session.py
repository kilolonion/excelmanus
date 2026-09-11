"""会话服务：workspace / MCP / rollback / 记忆 / 模型。控制面在 session_api，循环在 loop。"""

from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from collections.abc import Sequence
from pathlib import Path
from dataclasses import replace
from typing import TYPE_CHECKING, Any

import openai

from excelmanus.approval import AppliedApprovalRecord, ApprovalManager, PendingApproval
from excelmanus.compaction import CompactionManager
from excelmanus.context_budget import ContextBudget
from excelmanus.workspace import IsolatedWorkspace, SandboxEnv
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
from excelmanus.subagent import SubagentRegistry, SubagentResult
from excelmanus.task_list import TaskStore
from excelmanus.tools import task_tools
from excelmanus.tools.introspection_tools import register_introspection_tools
from excelmanus.engine_core.command_handler import CommandHandler
from excelmanus.engine_core.session_state import SessionState
from excelmanus.prompt.assemble import (
    all_tool_names as assemble_tool_names,
    build_stable_system_prompt,
    prepare_system_prompts_for_request,
)
from excelmanus.engine_core.subagent_orchestrator import SubagentOrchestrator
from excelmanus.engine_core.llm_caller import LLMCaller
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
    ApprovalResolver,
    QuestionResolver,
    _EFFORT_RATIOS,
)
from excelmanus.engine_utils import (
    _WRITE_EFFECT_VALUES,
    _summarize_text,
    build_mention_context_block,
)

if TYPE_CHECKING:
    from excelmanus.database import Database
    from excelmanus.engine_core.subagent_orchestrator import ParallelDelegateOutcome
    from excelmanus.memory_extractor import MemoryExtractor
    from excelmanus.persistent_memory import PersistentMemory

logger = get_logger("engine")


def _tool_access_from_chat_mode(chat_mode: str) -> str:
    """发给模型的 tools 数组不再随 plan/read 缩短。只读靠执行器拒绝写入。"""
    return "may_write"


def _ui_tool_access_from_chat_mode(chat_mode: str) -> str:
    """回显给 UI / ChatResult：read 仍标记只读，plan 与 write 相同。"""
    if chat_mode == "read":
        return "read_only"
    return "may_write"


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
        role: str = "host",
    ) -> None:
        # ── 核心组件初始化（必须在所有 property 代理字段赋值之前）──
        self._session_id: str | None = None
        self._history_snapshot_index: int = 0
        self._state = SessionState()
        self._session_role = role if role in {"host", "child"} else "host"
        self._is_host_session = self._session_role == "host"
        # ── LLM 客户端（仅激活模型） ──
        from excelmanus.engine_core.llm_client_manager import LLMClientManager
        self._llm_clients = LLMClientManager(config)
        self._client = self._llm_clients.client
        self._config = config
        # ── 视觉能力推断：图片只交给激活模型 ──
        self._is_vision_capable = self._infer_vision_capable(config, database)
        logger.info(
            "视觉模式: vision=%s",
            self._is_vision_capable,
        )
        # ── 首次使用关键词推断时，自动触发后台 probe 以获取 ground truth ──
        if self._is_host_session and config.main_model_vision == "auto" and database is not None:
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
            "model": str(config.model or ""),
        }
        for _var_key, _var_val in self._runtime_vars.items():
            self._memory.system_prompt = self._memory.system_prompt.replace(
                f"{{{{{_var_key}}}}}", _var_val
            )
        self._last_route_result = SkillMatchResult(
            skills_used=[],
            route_mode="all_tools",
            system_contexts=[],
        )
        # 任务清单存储：单会话内存级，闭包注入避免全局状态污染
        self._task_store = TaskStore()
        if self._is_host_session:
            self._registry.register_tools(task_tools.get_tools(self._task_store))
            # 计划文档工具：绑定 TaskStore + workspace，write_plan 一次调用生成文档+TaskList
            from excelmanus.plan_mode import request_exit_plan_approval
            from excelmanus.tools import plan_tools
            self._registry.register_tools(
                plan_tools.get_tools(
                    self._task_store,
                    config.workspace_root,
                    is_plan_active=lambda: bool(
                        getattr(self, "_plan_active", False)
                        or getattr(self, "_current_chat_mode", "write") == "plan"
                    ),
                    on_exit_submitted=lambda plan: request_exit_plan_approval(self, plan),
                )
            )
            register_introspection_tools(self._registry)
        # 会话级权限控制：从持久化配置读取，继承上次设置
        self._full_access_enabled: bool = (
            self._load_persisted_full_access(database) if self._is_host_session else False
        )
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
        self._tools_cache_key: tuple[str, str, frozenset[str], bool, str] | None = None
        _cache_key = (config.model, config.base_url)
        self._system_mode_cache_key = _cache_key
        self._system_mode_fallback: str | None = type(self)._system_mode_fallback_cache.get(_cache_key)
        # ── 状态变量由 self._state 统一管理 ──
        # self._state 在 __init__ 顶部初始化，以下属性通过 @property 代理访问：
        # _session_turn, _last_iteration_count, _last_tool_call_count,
        # _last_success_count, _last_failure_count,
        # _has_write_tool_call, _turn_diagnostics, _session_diagnostics
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
            )
        # ── FileRegistry（别名索引，不是版本权威）────
        self._file_registry: Any = None
        if database is not None:
            try:
                from excelmanus.file_registry import get_shared_file_registry
                self._file_registry = get_shared_file_registry(
                    database, self._config.workspace_root,
                )
            except Exception:
                logger.warning("FileRegistry 初始化失败", exc_info=True)
        self._transaction = None
        # 将 registry 共享给 ApprovalManager / SessionState
        self._approval._file_registry = self._file_registry
        self._state._file_registry = self._file_registry
        self._sandbox_env: SandboxEnv = self._workspace.create_sandbox_env()
        # 会话级 FileAccessGuard，绑定到当前引擎的工作区根目录。
        from excelmanus.security import FileAccessGuard as _FAG
        self._file_access_guard = _FAG(str(self._workspace.root_dir))
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
        self._turn_dirty_files: set[str] = set()  # 当前轮次被写的文件路径
        self._bench_mode: bool = False
        self._mention_contexts: list[ResolvedMention] | None = None
        self._current_chat_mode: str = "write"
        self._plan_active: bool = False
        self._pending_plan_exit: str | None = None
        self._last_compact_failed: bool = False
        self._skill_catalog_digest: str | None = None
        self._present_as: str = "native"
        self._prompt_user_contexts: list[str] = []
        self._prompt_tool_snapshot: list[Any] | None = None
        self._prompt_last_assembly: Any = None

        # ── 上下文自动压缩（Compaction）──────────────────────
        self._compaction_manager = CompactionManager(config)
        # 缓存最近一次循环中构建的 system_msgs，
        # 供 get_compaction_status / /compact 命令使用更准确的 token 计数。
        self._last_system_msgs: list[dict] | None = None

        # ── 提示词加载（md → 注册表）─────────────────────────
        self._prompt_composer: Any = None
        if self._is_host_session:
            try:
                from excelmanus.prompt.load import PromptComposer as _PC
                _prompts_dir = Path(__file__).resolve().parent.parent / "prompts"
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
        if self._is_host_session and config.embedding_enabled:
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
        if self._is_host_session and persistent_memory is not None and self._embedding_client is not None:
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
        if self._is_host_session and self._embedding_client is not None:
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
        if self._is_host_session and persistent_memory is not None and config.memory_expire_days > 0:
            try:
                persistent_memory.cleanup_expired(config.memory_expire_days)
            except Exception:
                logger.debug("记忆过期清理失败，已跳过", exc_info=True)
        self._session_summary_store: Any = None  # 由 SessionManager 注入

        # ── 用户自定义规则 ─────────────────────────────────
        self._rules_manager: Any = None  # 类型：RulesManager | None
        if self._is_host_session:
            try:
                from excelmanus.rules import RulesManager as _RM
                from excelmanus.stores.rules_store import RulesStore as _RS
                _rules_db_store = _RS(database) if database is not None else None
                self._rules_manager = _RM(db_store=_rules_db_store)
            except Exception:
                logger.debug("RulesManager 初始化失败", exc_info=True)

        # ── Playbook 存储（仅供 /playbook 命令查阅，默认路径不再自动注入）──
        self._playbook_store: Any = None
        if self._is_host_session and config.playbook_enabled:
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

        # 插话 / guide 走 Driver inbox（next-turn / next-step）。
        # ── 渠道上下文（Bot 渠道提示词注入） ──
        self._channel_context: str | None = None

        # ── /tools 与 /reasoning 展示开关（仅会话级，不持久化） ──
        self._show_tool_calls: bool = False
        self._show_reasoning: bool = False

        # ── 解耦组件延迟初始化 ──────────────────────────────
        self._tool_dispatcher = ToolDispatcher(self)
        from excelmanus.tools.runtime import ToolRuntime
        self._tool_runtime = ToolRuntime(self._tool_dispatcher, engine=self)
        self._tool_dispatcher._runtime = self._tool_runtime
        self._subagent_orchestrator = SubagentOrchestrator(self)
        self._command_handler = CommandHandler(self)
        self._llm_caller = LLMCaller(self)
        self._skill_resolver = SkillResolver(self)
        self._meta_tool_builder = MetaToolBuilder(self)
        self._bind_prompt_registry_runtime()
        self._interaction_handler = InteractionHandler(self)
        from excelmanus.agent.driver import Driver
        from excelmanus.agent.seams import attach_wave_d
        self._driver = Driver(self)
        attach_wave_d(self)

    def _schedule_background_probe(self, config: "ExcelManusConfig", db: "Database | None") -> None:
        """后台触发 probe 检测当前模型视觉能力，结果缓存到 DB 供下次使用。"""
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
        """推断当前模型是否支持视觉输入。

        优先级：手动覆盖 > 关键词+probe 交叉验证 > 关键词推断。
        对已知视觉模型（关键词匹配），probe=False 不覆盖关键词推断，
        避免 Codex 等 backend-api 的 probe 误判导致图片被拦截。
        """
        from excelmanus.vision_capability import infer_vision_capable

        probe: bool | None = None
        if db is not None:
            try:
                from excelmanus.model_probe import load_capabilities
                caps = load_capabilities(db, config.model, config.base_url)
                if caps is not None:
                    probe = caps.supports_vision
            except Exception:
                logger.debug("加载 probe 视觉检测结果失败，回退到关键词推断", exc_info=True)
        return infer_vision_capable(
            config.model,
            override=config.main_model_vision,
            probe=probe,
        )

    def _refresh_vision_capability(self) -> None:
        """按当前活跃模型重新推断视觉能力（切换模型后调用）。"""
        from excelmanus.vision_capability import infer_vision_capable

        model = self._active_model or self._config.model
        base_url = self._active_base_url or self._config.base_url
        probe: bool | None = None
        caps = getattr(self, "_model_capabilities", None)
        if caps is not None:
            probe = getattr(caps, "supports_vision", None)
        elif self._database is not None:
            try:
                from excelmanus.model_probe import load_capabilities
                cached = load_capabilities(self._database, model, base_url)
                if cached is not None:
                    probe = cached.supports_vision
            except Exception:
                logger.debug("刷新视觉能力时加载 probe 失败", exc_info=True)
        self._is_vision_capable = infer_vision_capable(
            model,
            override=self._config.main_model_vision,
            probe=probe,
        )
        logger.info("视觉模式已刷新: model=%s vision=%s", model, self._is_vision_capable)

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
        keep_target: bool = True,
    ) -> dict:
        """回退对话到第 turn_index 个用户轮次。不改磁盘。

        Args:
            turn_index: 目标用户轮次索引（0-indexed）。
            keep_target: 是否保留目标用户消息。为 False 时连同目标消息
                一起移除（用于编辑重发场景，避免后续 chat() 重复添加）。

        Returns:
            {removed_messages, turn_index}
        """
        removed = self._memory.rollback_to_user_turn(turn_index, keep_target=keep_target)

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
        # 图片追踪：清理已移除消息相关的图片状态
        self._memory.reset_image_tracking()

        return {
            "removed_messages": removed,
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
        """仅在显式 extract 时构造；默认引擎路径不创建额外 LLM 客户端。"""
        if self._memory_extractor is not None:
            return self._memory_extractor
        if self._persistent_memory is None or not self._config.memory_enabled:
            return None
        try:
            from excelmanus.memory_extractor import MemoryExtractor
            from excelmanus.providers import create_client

            client = create_client(
                api_key=self._active_api_key,
                base_url=self._active_base_url,
                protocol=self._active_protocol,
            )
            extractor = MemoryExtractor(client=client, model=self._active_model)
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

        client = self._client
        model = self._config.memory_maintenance_model or self._active_model
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
        # 注册重试回调：Exa 后台重试成功后补注册 parallel_search + 确认白名单
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

    async def warmup_prompt_cache(self) -> None:
        """异步预热 Anthropic prompt cache（fire-and-forget）。

        仅对 ClaudeClient 实例生效。发送一个只含稳定 system prompt 前缀
        + 最小 user 消息的请求（max_tokens=1），使稳定前缀进入 cache。
        后续真实请求即可 cache HIT，大幅降低首次 TTFT。
        """
        from excelmanus.providers.claude import ClaudeClient
        if not isinstance(self._client, ClaudeClient):
            return
        stable_prompt = self._build_stable_system_prompt()
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
        from excelmanus.agent.session_api import push_guide_message as _impl
        return _impl(self, message)


    def drain_guide_messages(self) -> list[str]:
        from excelmanus.agent.session_api import drain_guide_messages as _impl
        return _impl(self)


    def push_interrupt_message(self, message: str) -> None:
        from excelmanus.agent.session_api import push_interrupt_message as _impl
        return _impl(self, message)


    def drain_interrupt_messages(self) -> list[str]:
        from excelmanus.agent.session_api import drain_interrupt_messages as _impl
        return _impl(self)


    def steer(self, message: str):
        from excelmanus.agent.session_api import steer as _impl
        return _impl(self, message)


    def inject(self, message: str):
        from excelmanus.agent.session_api import inject as _impl
        return _impl(self, message)


    @property
    def message_snapshot_index(self) -> int:
        """已持久化的消息快照索引。"""
        return self._history_snapshot_index

    def set_message_snapshot_index(self, index: int) -> None:
        """设置已持久化的消息快照索引。"""
        self._history_snapshot_index = index

    # ── Session snapshot 持久化（循环计数 / 任务列表，不是文件检查点）──

    def save_session_snapshot(self) -> None:
        """保存当前 SessionState + TaskStore 状态到数据库。"""
        if self._checkpoint_store is None or self._session_id is None:
            return
        try:
            from excelmanus.tools.runtime import present_as_of

            self._state.present_as = present_as_of(self)
            self._checkpoint_store.save_session_snapshot(
                session_id=self._session_id,
                state_dict=self._state.to_dict(),
                task_list_dict=self._task_store.to_dict(),
                turn_number=self._state.session_turn,
            )
        except Exception:
            logger.debug("save_session_snapshot 失败", exc_info=True)

    save_checkpoint = save_session_snapshot

    def restore_session_snapshot(self) -> bool:
        """从数据库恢复最新会话快照，返回是否成功恢复。"""
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
            from excelmanus.tools.runtime import normalize_present_as

            self._present_as = normalize_present_as(
                restored_state.present_as,
                chat_mode=getattr(self, "_current_chat_mode", "write"),
            )
            self._tools_cache = None

            from excelmanus.task_list import TaskStore
            restored_store = TaskStore.from_dict(cp["task_list_dict"])
            # 迁移任务清单到现有 _task_store（保持工具引用有效）
            self._task_store._task_list = restored_store._task_list
            self._task_store._plan_file_path = restored_store._plan_file_path
            logger.info(
                "session snapshot 恢复成功: session=%s turn=%s",
                self._session_id, cp["turn_number"],
            )
            return True
        except Exception:
            logger.debug("restore_session_snapshot 失败", exc_info=True)
            return False

    restore_checkpoint = restore_session_snapshot

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

    @property
    def execution_policy(self):
        from excelmanus.security.policy import resolve_execution_policy
        return resolve_execution_policy(self)

    @property
    def approval_policy(self):
        from excelmanus.security.policy import resolve_approval_policy
        return resolve_approval_policy(self)

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
    def workspace(self) -> IsolatedWorkspace:
        return self._workspace

    @property
    def file_version_manager(self) -> Any:
        return None

    @property
    def transaction(self) -> None:
        return None

    @transaction.setter
    def transaction(self, value: Any) -> None:
        return

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
        """当前模型是否支持视觉（Protocol: VisionContext）。"""
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

    def _bind_prompt_registry_runtime(self) -> None:
        """PromptRegistry 持有坍缩后的工具快照；schema 仍由 ToolRegistry 生成。"""
        composer = getattr(self, "_prompt_composer", None)
        registry = getattr(composer, "registry", None) if composer is not None else None
        if registry is None or getattr(registry, "_excelmanus_runtime_bound", False):
            return

        def _tools_snapshot() -> list[Any]:
            if getattr(self, "_collecting_prompt_tools", False):
                return []
            self._collecting_prompt_tools = True
            try:
                builder = getattr(self, "_meta_tool_builder", None)
                if builder is None:
                    return []
                return list(builder.build_v5_tools(tool_access="may_write"))
            finally:
                self._collecting_prompt_tools = False

        def _request_contexts(_ctx: Any = None) -> str:
            parts = getattr(self, "_prompt_user_contexts", None) or []
            return "\n\n".join(part for part in parts if isinstance(part, str) and part.strip())

        registry.tools(_tools_snapshot)
        registry.context("request-dynamic", 50, _request_contexts)
        registry._excelmanus_runtime_bound = True

    def _all_tool_names(self) -> list[str]:
        return assemble_tool_names(self)

    def _build_stable_system_prompt(self) -> str:
        return build_stable_system_prompt(self)

    def _prepare_system_prompts_for_request(
        self,
        skill_contexts: list[str] | None = None,
    ) -> tuple[list[str], str | None]:
        return prepare_system_prompts_for_request(self, skill_contexts)

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
        driver = getattr(self, "_driver", None)
        if driver is not None:
            if not event.turn_id:
                event.turn_id = driver.turn_id
            if not event.step_id:
                event.step_id = driver.step_id
        if on_event is None:
            return
        try:
            on_event(event)
        except Exception as exc:
            logger.warning("事件回调异常: %s", exc)

    async def followup(
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
        present_as: str | None = None,
    ) -> ChatResult:
        from excelmanus.agent.session_api import followup as _impl
        return await _impl(
            self,
            user_message,
            on_event=on_event,
            slash_command=slash_command,
            raw_args=raw_args,
            mention_contexts=mention_contexts,
            images=images,
            approval_resolver=approval_resolver,
            question_resolver=question_resolver,
            chat_mode=chat_mode,
            channel=channel,
            present_as=present_as,
        )


    async def _apply_claimed_followup(self, item: Any) -> ChatResult | None:
        from excelmanus.agent.session_api import apply_claimed_followup as _impl
        return await _impl(self, item)


    def _emit_short_circuit_summary(
        self,
        on_event: EventCallback | None,
        chat_start: float,
    ) -> None:
        from excelmanus.agent.session_api import emit_short_circuit_summary as _impl
        return _impl(self, on_event, chat_start)


    def _finalize_driver_turn(
        self,
        chat_result: ChatResult,
        *,
        on_event: EventCallback | None,
        chat_start: float,
    ) -> None:
        from excelmanus.agent.session_api import finalize_driver_turn as _impl
        return _impl(self, chat_result, on_event=on_event, chat_start=chat_start)


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
        for tool_name in self._all_tool_names():
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

    async def run_subagent(
        self,
        *,
        agent_name: str,
        prompt: str,
        on_event: EventCallback | None = None,
    ) -> SubagentResult:
        """启动子 Driver，等结束。"""
        from excelmanus.subagent.child import start_child_driver

        return await start_child_driver(
            self,
            task=prompt,
            agent_name=agent_name,
            on_event=on_event,
        )

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
        """单个工具调用：委托给 ToolRuntime.execute()（阶段 1–9）。"""
        return await self._tool_runtime.execute(
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
        共享逻辑：同时被 _handle_accept_command 和循环内联审批使用。
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
            from excelmanus.workspace.identity import collect_public_identities
            from excelmanus.events import mutations_from_identities
            changed: list[str] = list(structured.ui_meta.files or [])
            if structured.ui_meta.text_diff:
                fp = structured.ui_meta.text_diff.get("file_path")
                if isinstance(fp, str) and fp and fp not in changed:
                    changed.append(fp)
            if isinstance(structured.value, dict):
                for item in structured.value.get("published") or []:
                    if not isinstance(item, dict):
                        continue
                    path = str(item.get("path") or "").strip()
                    if item.get("status") == "committed" and path and path not in changed:
                        changed.append(path)
            changed = collect_public_identities(changed, self._workspace.root_dir)
            for ident in changed:
                self._state.record_affected_file(ident)
            if changed:
                self._emit(
                    on_event,
                    ToolCallEvent(
                        event_type=EventType.FILES_CHANGED,
                        tool_call_id=tool_call_id or pending.approval_id,
                        iteration=0,
                        changed_files=changed,
                        mutations=mutations_from_identities(changed),
                    ),
                )

        if pending.tool_name == "run_code":
            _has_published = False
            if isinstance(structured.value, dict):
                _items = structured.value.get("published") or []
                _has_published = isinstance(_items, list) and any(
                    isinstance(i, dict) and i.get("status") == "committed" and i.get("path")
                    for i in _items
                )
            if record.changes or _has_published:
                self._record_workspace_write_action()
            extra: list[str] = []
            if isinstance(structured.value, dict):
                for item in structured.value.get("published") or []:
                    if isinstance(item, dict) and item.get("status") == "committed" and item.get("path"):
                        extra.append(str(item["path"]))
            self._tool_dispatcher._record_files_from_run_code(self, extra_changed_paths=extra or None)

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
        self._refresh_vision_capability()

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
        """当前激活的模型 profile 短名称。"""
        return self._active_model_name

    def sync_model_profiles(self, profiles: tuple["ModelProfile", ...]) -> None:
        """热更新可用模型档案列表（由 SessionManager 广播调用）。"""
        object.__setattr__(self._config, "models", profiles)

    def list_models(self) -> list[dict[str, str]]:
        """列出所有可用模型档案，含当前激活标记。"""
        result: list[dict[str, str]] = []
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
        """返回所有可用模型短名称列表。"""
        return [p.name for p in self._config.models]

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
            return f"未找到模型 {name!r}。可用模型：{available}"

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
        self._model_capabilities = None
        self._context_budget.update_for_model(matched.model)
        self._sync_context_window_consumers()
        self._refresh_vision_capability()
        desc = f"（{matched.description}）" if matched.description else ""
        return f"已切换到模型：{matched.name} → {matched.model}{desc}"

    def _sync_from_llm_clients(self) -> None:
        """从 LLMClientManager 同步活跃模型状态到引擎本地字段。"""
        mgr = self._llm_clients
        self._active_model = mgr.active_model
        self._active_api_key = mgr.active_api_key
        self._active_base_url = mgr.active_base_url
        self._active_protocol = mgr.active_protocol
        self._client = mgr.client

    async def _adapt_guidance_only_slash_route(
        self,
        *,
        route_result: SkillMatchResult,
        user_message: str,
        slash_command: str | None,
        raw_args: str,
    ) -> tuple[SkillMatchResult, str]:
        from excelmanus.agent.session_api import adapt_guidance_only_slash_route as _impl
        return await _impl(
            self,
            route_result=route_result,
            user_message=user_message,
            slash_command=slash_command,
            raw_args=raw_args,
        )


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
        from excelmanus.agent.session_api import route_skills as _impl
        return await _impl(
            self,
            user_message,
            slash_command=slash_command,
            raw_args=raw_args,
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
