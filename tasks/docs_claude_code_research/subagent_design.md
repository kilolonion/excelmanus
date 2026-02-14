# ExcelManus Subagent 系统设计方案

> **历史文档声明（Skillpack 协议）**：本文为历史设计调研记录，可能包含已过时术语（如 `hint_direct`、`confident_direct`、`llm_confirm`、`fork_plan`、`Skillpack.context`）。现行规则请以 [`../../docs/skillpack_protocol.md`](../../docs/skillpack_protocol.md) 为准。

> **来源**：Claude Code 调研借鉴
> **创建日期**：2025-07-14
> **优先级**：P0（核心能力）
> **预估工期**：5-8 天
> **前置依赖**：Phase 1 Hook 生命周期引擎

---

## 一、Claude Code Subagent 调研摘要

### 1.1 架构概览

Claude Code 的子代理是**真正独立执行**的隔离环境，每个子代理拥有：
- 独立的对话上下文（不污染主会话）
- 独立的工具集（可限制/扩展）
- 独立的权限模式
- 独立的持久记忆
- 独立的 Hook 配置

### 1.2 内置子代理

| 子代理 | 模型 | 工具 | 用途 |
|--------|------|------|------|
| **Explore** | Haiku（快速小模型） | 只读工具（Read, Grep, Glob） | 文件发现、代码搜索、代码库探索 |
| **Plan** | 继承主模型 | 只读工具 | 代码库调研、规划 |
| **General-purpose** | 继承主模型 | 全部工具 | 复杂调研、多步操作、代码修改 |

### 1.3 自定义子代理配置格式

Claude Code 使用 Markdown frontmatter 定义子代理：

```yaml
---
name: db-reader
description: Execute read-only database queries
tools: Bash
disallowedTools: Write, Edit
permissionMode: default          # default | acceptEdits | dontAsk | plan | delegate
memory: user                     # user | project | local
skills:
  - api-conventions
  - error-handling-patterns
hooks:
  PreToolUse:
    - matcher: "Bash"
      hooks:
        - type: command
          command: "./scripts/validate-readonly-query.sh"
---
你是一个数据库查询代理，只允许执行 SELECT 查询...
```

### 1.4 关键特性

- **前台/后台执行**：
  - 前台：阻塞主对话，权限提示和澄清问题透传给用户
  - 后台：并发执行，启动前预申请所有权限，运行中自动拒绝未预批准的操作
- **自动委派**：LLM 根据子代理 `description` 自动选择合适的子代理执行
- **会话恢复**：子代理对话独立存储在 `~/.claude/projects/{project}/{sessionId}/subagents/`，可跨会话恢复
- **自动压缩**：子代理上下文独立压缩，不影响主会话
- **子代理嵌套**：通过 `tools: Task(worker, researcher)` 限制可调用的子代理类型
- **持久记忆**：子代理有独立的 `MEMORY.md`，启动时自动加载前 200 行

### 1.5 子代理生命周期事件

```
SubagentStart → (子代理内部 Agentic Loop) → SubagentStop
```

- `SubagentStart`：可触发 Hook（如初始化数据库连接）
- `SubagentStop`：可触发 Hook（如清理资源、生成摘要）

---

## 二、ExcelManus 现状分析

### 2.1 当前实现

```python
# engine.py 中的 fork 子代理相关代码
async def _run_fork_subagent_if_needed(self, ...) -> str | None:
    # fork_plan 已从 SkillMatchResult 中移除，子代理将在后续任务中
    # 改为通过 explore_data 元工具由 LLM 主动触发
    return None

async def _execute_fork_plan_loop(self, *, fork_plan, ...):
    # 存在但未真正实现
    ...
```

**现状**：
- `Skillpack.context` 支持 `"inline" | "fork"` 两种模式
- `SkillMatchResult` 中已移除 `fork_plan`
- `_run_fork_subagent_if_needed()` 直接返回 `None`
- `events.py` 已定义 `SUBAGENT_START` / `SUBAGENT_END` / `SUBAGENT_SUMMARY` 事件
- `config.py` 已有 `subagent_enabled`、`subagent_model`、`subagent_max_iterations` 等配置
- `engine.py` 已有 `_subagent_enabled` 会话级开关

### 2.2 已有基础设施（可复用）

| 模块 | 可复用点 |
|------|----------|
| `EventType.SUBAGENT_*` | 子代理事件类型已定义 |
| `ToolCallEvent.subagent_*` | 子代理事件字段已定义 |
| `ExcelManusConfig.subagent_*` | 子代理配置项已定义 |
| `AgentEngine._subagent_enabled` | 会话级开关已存在 |
| `ToolRegistry` | 工具注册表可创建受限视图 |
| `ConversationMemory` | 可为子代理创建独立实例 |
| `PersistentMemory` | 可为子代理创建独立作用域 |
| `ApprovalManager` | 子代理可共享或独立审批管理 |

---

## 三、详细设计

### 3.1 模块结构

```
excelmanus/subagent/
├── __init__.py              # 公开 API
├── models.py                # SubagentConfig, SubagentResult 数据模型
├── executor.py              # SubagentExecutor 核心执行器
├── registry.py              # SubagentRegistry 子代理注册与发现
├── builtin.py               # 内置子代理定义（Explorer, Analyst, Writer, Coder）
└── tool_filter.py           # 工具注册表受限视图
```

### 3.2 数据模型

```python
# excelmanus/subagent/models.py
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Literal

SubagentPermissionMode = Literal["default", "acceptEdits", "readOnly", "dontAsk"]
SubagentMemoryScope = Literal["user", "project"]


@dataclass(frozen=True)
class SubagentConfig:
    """子代理配置定义。"""
    name: str
    description: str
    # 模型配置
    model: str | None = None              # None = 继承主模型
    api_key: str | None = None            # None = 继承主配置
    base_url: str | None = None           # None = 继承主配置
    # 工具限制
    allowed_tools: list[str] = field(default_factory=list)    # 空 = 全部工具
    disallowed_tools: list[str] = field(default_factory=list)
    # 权限与安全
    permission_mode: SubagentPermissionMode = "default"
    # 执行限制
    max_iterations: int = 6
    max_consecutive_failures: int = 2
    # 技能预加载
    skills: list[str] = field(default_factory=list)
    # 持久记忆
    memory_scope: SubagentMemoryScope | None = None
    # Hook 配置（依赖 Phase 1）
    hooks: dict[str, Any] = field(default_factory=dict)
    # 来源标识
    source: Literal["builtin", "user", "project"] = "builtin"
    # 系统提示词（Markdown body 部分）
    system_prompt: str = ""


@dataclass
class SubagentResult:
    """子代理执行结果。"""
    success: bool
    summary: str                          # 返回给主对话的摘要
    iterations: int = 0
    tool_calls_count: int = 0
    error: str | None = None
    # 子代理产出的文件变更列表（供主代理审查）
    file_changes: list[str] = field(default_factory=list)
    # 原始对话历史（用于会话恢复）
    conversation_id: str = ""
```

### 3.3 工具注册表受限视图

```python
# excelmanus/subagent/tool_filter.py
from excelmanus.tools.registry import ToolRegistry


class FilteredToolRegistry:
    """工具注册表的受限视图，用于子代理的工具隔离。

    不复制工具实现，仅在 get_tools() / call() 时过滤。
    """

    def __init__(
        self,
        parent: ToolRegistry,
        allowed: list[str] | None = None,
        disallowed: list[str] | None = None,
    ) -> None:
        self._parent = parent
        self._allowed = set(allowed) if allowed else None
        self._disallowed = set(disallowed) if disallowed else set()

    def is_tool_available(self, name: str) -> bool:
        """检查工具是否在受限范围内。"""
        if name in self._disallowed:
            return False
        if self._allowed is not None and name not in self._allowed:
            return False
        return self._parent.has_tool(name)

    def get_tool_definitions(self) -> list[dict]:
        """返回过滤后的工具定义列表（供 LLM function calling）。"""
        all_defs = self._parent.get_tool_definitions()
        return [d for d in all_defs if self.is_tool_available(d["function"]["name"])]

    async def call(self, name: str, arguments: dict) -> str:
        """执行工具调用，不在范围内则抛出 ToolNotAllowedError。"""
        if not self.is_tool_available(name):
            from excelmanus.tools.registry import ToolNotAllowedError
            raise ToolNotAllowedError(name)
        return await self._parent.call(name, arguments)
```

### 3.4 核心执行器

```python
# excelmanus/subagent/executor.py
from __future__ import annotations

import asyncio
import uuid
from typing import Any

from excelmanus.config import ExcelManusConfig
from excelmanus.events import EventCallback, EventType, ToolCallEvent
from excelmanus.logger import get_logger
from excelmanus.memory import ConversationMemory
from excelmanus.subagent.models import SubagentConfig, SubagentResult
from excelmanus.subagent.tool_filter import FilteredToolRegistry

logger = get_logger("subagent.executor")
_SUMMARY_MAX_CHARS = 4000


class SubagentExecutor:
    """子代理执行器：创建独立的 AgentEngine 实例运行子任务。

    每次 run() 调用创建一个隔离的执行环境：
    - 独立的 ConversationMemory（不污染主会话）
    - 受限的 ToolRegistry（按配置过滤）
    - 独立的迭代计数和失败计数
    """

    def __init__(
        self,
        parent_config: ExcelManusConfig,
        parent_registry: Any,  # ToolRegistry
    ) -> None:
        self._parent_config = parent_config
        self._parent_registry = parent_registry

    async def run(
        self,
        config: SubagentConfig,
        prompt: str,
        *,
        parent_context: str = "",
        on_event: EventCallback | None = None,
    ) -> SubagentResult:
        """在独立上下文中执行子代理任务。

        Args:
            config: 子代理配置
            prompt: 用户任务描述
            parent_context: 主对话传递的上下文摘要
            on_event: 事件回调（透传给主会话渲染）

        Returns:
            SubagentResult 包含执行摘要和状态
        """
        conversation_id = str(uuid.uuid4())

        # 1. 发出 SubagentStart 事件
        if on_event:
            on_event(ToolCallEvent(
                event_type=EventType.SUBAGENT_START,
                tool_name=config.name,
                subagent_reason=config.description,
                subagent_tools=config.allowed_tools or [],
            ))

        try:
            # 2. 构建受限工具注册表
            filtered_registry = FilteredToolRegistry(
                parent=self._parent_registry,
                allowed=config.allowed_tools or None,
                disallowed=config.disallowed_tools or None,
            )

            # 3. 构建子代理专用配置
            sub_config = self._build_sub_config(config)

            # 4. 构建独立的对话记忆
            sub_memory = ConversationMemory(sub_config)
            if config.system_prompt:
                sub_memory.system_prompt = config.system_prompt
            if parent_context:
                sub_memory.system_prompt += f"\n\n## 主对话上下文\n{parent_context}"

            # 5. 加载子代理持久记忆（如配置）
            self._load_subagent_memory(config, sub_memory)

            # 6. 执行 Agentic Loop
            result = await self._run_loop(
                config=config,
                sub_config=sub_config,
                registry=filtered_registry,
                memory=sub_memory,
                prompt=prompt,
                on_event=on_event,
            )
            result.conversation_id = conversation_id
            return result

        except Exception as exc:
            logger.exception("子代理 %s 执行失败", config.name)
            return SubagentResult(
                success=False,
                summary=f"子代理 {config.name} 执行失败: {exc}",
                error=str(exc),
                conversation_id=conversation_id,
            )
        finally:
            # 7. 发出 SubagentStop 事件
            if on_event:
                on_event(ToolCallEvent(
                    event_type=EventType.SUBAGENT_END,
                    tool_name=config.name,
                ))

    def _build_sub_config(self, config: SubagentConfig) -> ExcelManusConfig:
        """基于子代理配置构建 ExcelManusConfig。"""
        from dataclasses import replace
        return replace(
            self._parent_config,
            model=config.model or self._parent_config.model,
            max_iterations=config.max_iterations,
            max_consecutive_failures=config.max_consecutive_failures,
            # 子代理禁用嵌套子代理（防止无限递归）
            subagent_enabled=False,
        )

    def _load_subagent_memory(
        self,
        config: SubagentConfig,
        memory: ConversationMemory,
    ) -> None:
        """加载子代理的持久记忆到 system prompt。"""
        if config.memory_scope is None:
            return
        from excelmanus.persistent_memory import PersistentMemory
        if config.memory_scope == "user":
            memory_dir = f"~/.excelmanus/agent-memory/{config.name}"
        else:  # project
            memory_dir = f".excelmanus/agent-memory/{config.name}"
        try:
            pm = PersistentMemory(memory_dir)
            core = pm.load_core()
            if core:
                memory.system_prompt += f"\n\n## 子代理记忆\n{core}"
        except Exception:
            logger.warning("加载子代理 %s 持久记忆失败", config.name)

    async def _run_loop(
        self,
        *,
        config: SubagentConfig,
        sub_config: ExcelManusConfig,
        registry: FilteredToolRegistry,
        memory: ConversationMemory,
        prompt: str,
        on_event: EventCallback | None,
    ) -> SubagentResult:
        """子代理的 Agentic Loop 核心。

        复用 AgentEngine 的 Tool Calling 循环逻辑，
        但使用受限的工具注册表和独立的对话记忆。
        """
        import openai

        client = openai.AsyncOpenAI(
            api_key=config.api_key or sub_config.api_key,
            base_url=config.base_url or sub_config.base_url,
        )
        model = config.model or sub_config.model

        # 初始化对话
        messages = [
            {"role": "system", "content": memory.system_prompt},
            {"role": "user", "content": prompt},
        ]
        tool_defs = registry.get_tool_definitions()

        iterations = 0
        tool_calls_count = 0
        consecutive_failures = 0
        last_reply = ""
        file_changes: list[str] = []

        while iterations < config.max_iterations:
            iterations += 1

            # LLM 调用
            response = await client.chat.completions.create(
                model=model,
                messages=messages,
                tools=tool_defs if tool_defs else openai.NOT_GIVEN,
            )
            choice = response.choices[0]
            message = choice.message

            # 无工具调用 → 结束循环
            if not message.tool_calls:
                last_reply = message.content or ""
                break

            # 处理工具调用
            messages.append(message.model_dump(exclude_none=True))
            for tc in message.tool_calls:
                tool_calls_count += 1
                tool_name = tc.function.name
                try:
                    import json
                    args = json.loads(tc.function.arguments or "{}")
                    result = await registry.call(tool_name, args)
                    consecutive_failures = 0

                    # 追踪文件变更
                    if tool_name in ("write_excel", "write_text_file", "create_chart"):
                        path = args.get("file_path") or args.get("output_path", "")
                        if path:
                            file_changes.append(path)

                except Exception as exc:
                    result = f"错误: {exc}"
                    consecutive_failures += 1

                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": str(result)[:_SUMMARY_MAX_CHARS],
                })

            if consecutive_failures >= config.max_consecutive_failures:
                last_reply = "子代理因连续失败过多而终止。"
                break

        # 生成摘要
        summary = last_reply[:_SUMMARY_MAX_CHARS] if last_reply else "子代理执行完成，无文本回复。"

        # 发出摘要事件
        if on_event:
            on_event(ToolCallEvent(
                event_type=EventType.SUBAGENT_SUMMARY,
                tool_name=config.name,
                subagent_summary=summary,
                subagent_success=True,
                total_iterations=iterations,
                total_tool_calls=tool_calls_count,
            ))

        return SubagentResult(
            success=True,
            summary=summary,
            iterations=iterations,
            tool_calls_count=tool_calls_count,
            file_changes=file_changes,
        )
```

### 3.5 内置子代理定义

```python
# excelmanus/subagent/builtin.py
from excelmanus.subagent.models import SubagentConfig

# 只读工具集
_READ_ONLY_TOOLS = [
    "read_excel", "list_sheets", "get_file_info",
    "analyze_data", "filter_data",
    "search_files", "list_directory", "read_text_file",
    "read_cell_styles",
]

# 分析工具集
_ANALYSIS_TOOLS = _READ_ONLY_TOOLS + [
    "execute_code",  # 允许执行分析代码
]

# 写入工具集（全部工具）
_WRITE_TOOLS = _READ_ONLY_TOOLS + [
    "write_excel", "batch_write_excel",
    "set_cell_styles", "batch_set_styles",
    "create_chart",
    "write_text_file",
    "execute_code",
]


EXPLORER = SubagentConfig(
    name="explorer",
    description="探索 Excel 文件结构和数据概览。适用于：了解文件有哪些 sheet、列名、数据量、数据类型等基本信息。使用快速小模型，只读操作。",
    allowed_tools=_READ_ONLY_TOOLS,
    permission_mode="readOnly",
    max_iterations=4,
    source="builtin",
    system_prompt=(
        "你是一个 Excel 数据探索助手。你的任务是快速了解 Excel 文件的结构和内容概览。\n"
        "请执行以下步骤：\n"
        "1. 列出所有 sheet 名称\n"
        "2. 读取每个 sheet 的前几行，了解列名和数据类型\n"
        "3. 获取文件基本信息（大小、行数等）\n"
        "4. 生成简洁的结构摘要\n\n"
        "只使用只读工具，不要修改任何数据。"
    ),
)

ANALYST = SubagentConfig(
    name="analyst",
    description="深度分析 Excel 数据，执行统计计算、数据筛选和趋势分析。适用于：需要计算汇总、筛选条件、数据透视等分析任务。",
    allowed_tools=_ANALYSIS_TOOLS,
    permission_mode="default",
    max_iterations=8,
    source="builtin",
    system_prompt=(
        "你是一个 Excel 数据分析专家。你的任务是对数据进行深度分析。\n"
        "分析时请注意：\n"
        "- 先了解数据结构，再执行分析\n"
        "- 使用 analyze_data 和 filter_data 进行统计和筛选\n"
        "- 必要时使用 execute_code 执行复杂计算\n"
        "- 分析结果要包含具体数字和结论\n"
        "- 如发现数据质量问题（空值、异常值），主动报告"
    ),
)

WRITER = SubagentConfig(
    name="writer",
    description="执行 Excel 数据写入和格式化操作。适用于：创建新表、写入数据、设置样式、生成图表等修改操作。",
    allowed_tools=_WRITE_TOOLS,
    permission_mode="default",
    max_iterations=10,
    source="builtin",
    system_prompt=(
        "你是一个 Excel 数据写入和格式化专家。你的任务是按要求修改 Excel 文件。\n"
        "操作时请注意：\n"
        "- 写入前先读取目标区域，避免覆盖重要数据\n"
        "- 使用 batch_write_excel 批量写入以提高效率\n"
        "- 格式化时使用 batch_set_styles 批量设置样式\n"
        "- 创建图表时选择合适的图表类型\n"
        "- 操作完成后验证结果"
    ),
)

CODER = SubagentConfig(
    name="coder",
    description="使用 Python 代码处理复杂的 Excel 操作。适用于：批量数据处理、复杂公式计算、数据清洗、自定义图表等需要编程的场景。",
    allowed_tools=["execute_code", "read_excel", "list_sheets", "get_file_info"],
    permission_mode="default",
    max_iterations=6,
    source="builtin",
    system_prompt=(
        "你是一个 Python 编程专家，擅长使用 openpyxl 和 pandas 处理 Excel 数据。\n"
        "编码时请注意：\n"
        "- 优先使用 pandas 进行数据处理\n"
        "- 使用 openpyxl 进行格式化和图表操作\n"
        "- 代码要有错误处理\n"
        "- 处理大文件时注意内存使用\n"
        "- 操作完成后打印结果摘要"
    ),
)

# 内置子代理注册表
BUILTIN_SUBAGENTS: dict[str, SubagentConfig] = {
    "explorer": EXPLORER,
    "analyst": ANALYST,
    "writer": WRITER,
    "coder": CODER,
}
```

### 3.6 子代理注册与发现

```python
# excelmanus/subagent/registry.py
from __future__ import annotations

from pathlib import Path
from typing import Any

from excelmanus.logger import get_logger
from excelmanus.subagent.builtin import BUILTIN_SUBAGENTS
from excelmanus.subagent.models import SubagentConfig

logger = get_logger("subagent.registry")


class SubagentRegistry:
    """子代理注册表：管理内置和自定义子代理的发现与加载。

    加载优先级：project > user > builtin（与 Skillpack 一致）
    """

    def __init__(
        self,
        user_agents_dir: str = "~/.excelmanus/agents",
        project_agents_dir: str = ".excelmanus/agents",
    ) -> None:
        self._agents: dict[str, SubagentConfig] = {}
        self._user_dir = Path(user_agents_dir).expanduser()
        self._project_dir = Path(project_agents_dir)

    def load_all(self) -> dict[str, SubagentConfig]:
        """加载所有子代理：builtin → user → project（后者覆盖前者）。"""
        self._agents.clear()

        # 1. 内置子代理
        self._agents.update(BUILTIN_SUBAGENTS)

        # 2. 用户级子代理
        self._load_from_dir(self._user_dir, source="user")

        # 3. 项目级子代理
        self._load_from_dir(self._project_dir, source="project")

        logger.info("已加载 %d 个子代理: %s", len(self._agents), list(self._agents.keys()))
        return dict(self._agents)

    def get(self, name: str) -> SubagentConfig | None:
        """按名称获取子代理配置。"""
        if not self._agents:
            self.load_all()
        return self._agents.get(name)

    def list_all(self) -> list[SubagentConfig]:
        """返回所有已注册的子代理。"""
        if not self._agents:
            self.load_all()
        return list(self._agents.values())

    def build_catalog(self) -> str:
        """生成子代理目录摘要（注入 system prompt 供 LLM 选择）。"""
        agents = self.list_all()
        if not agents:
            return ""
        lines = ["可用子代理：\n"]
        for agent in sorted(agents, key=lambda a: a.name):
            lines.append(f"- **{agent.name}**：{agent.description}")
        return "\n".join(lines)

    def _load_from_dir(self, dir_path: Path, source: str) -> None:
        """从目录加载子代理定义文件（.md 格式）。"""
        if not dir_path.exists():
            return
        for md_file in sorted(dir_path.glob("*.md")):
            try:
                config = self._parse_agent_file(md_file, source)
                self._agents[config.name] = config
                logger.debug("加载子代理 %s (来源: %s)", config.name, source)
            except Exception:
                logger.warning("解析子代理文件失败: %s", md_file, exc_info=True)

    @staticmethod
    def _parse_agent_file(path: Path, source: str) -> SubagentConfig:
        """解析子代理 Markdown 文件。

        格式与 Skillpack 的 SKILL.md 一致：YAML frontmatter + Markdown body。
        """
        # 复用 SkillpackLoader 的 frontmatter 解析逻辑
        import re
        content = path.read_text(encoding="utf-8")
        fm_match = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", content, re.DOTALL)
        if not fm_match:
            raise ValueError(f"缺少 YAML frontmatter: {path}")

        # 简化的 YAML 解析（复用 SkillpackLoader 的解析器）
        from excelmanus.skillpacks.loader import SkillpackLoader
        meta = SkillpackLoader._parse_frontmatter_yaml(fm_match.group(1))
        body = fm_match.group(2).strip()

        name = meta.get("name") or path.stem
        return SubagentConfig(
            name=name,
            description=meta.get("description", ""),
            model=meta.get("model"),
            allowed_tools=meta.get("tools", []) if isinstance(meta.get("tools"), list) else
                          [t.strip() for t in meta.get("tools", "").split(",") if t.strip()],
            disallowed_tools=meta.get("disallowedTools", []) if isinstance(meta.get("disallowedTools"), list) else
                             [t.strip() for t in meta.get("disallowedTools", "").split(",") if t.strip()],
            permission_mode=meta.get("permissionMode", "default"),
            max_iterations=int(meta.get("max_iterations", 6)),
            skills=meta.get("skills", []),
            memory_scope=meta.get("memory"),
            hooks=meta.get("hooks", {}),
            source=source,
            system_prompt=body,
        )
```

### 3.7 与 AgentEngine 集成

在 `AgentEngine` 中新增子代理调用入口：

```python
# engine.py 中新增的方法

class AgentEngine:
    def __init__(self, ...):
        ...
        # 子代理基础设施
        self._subagent_registry = SubagentRegistry()
        self._subagent_executor = SubagentExecutor(
            parent_config=config,
            parent_registry=registry,
        )

    async def run_subagent(
        self,
        agent_name: str,
        prompt: str,
        *,
        on_event: EventCallback | None = None,
    ) -> SubagentResult:
        """运行指定子代理。供 explore_data 等元工具调用。"""
        config = self._subagent_registry.get(agent_name)
        if config is None:
            return SubagentResult(
                success=False,
                summary=f"未找到子代理: {agent_name}",
                error=f"SubagentNotFound: {agent_name}",
            )

        # 构建主对话上下文摘要
        parent_context = self._build_parent_context_summary()

        return await self._subagent_executor.run(
            config=config,
            prompt=prompt,
            parent_context=parent_context,
            on_event=on_event,
        )

    def _build_parent_context_summary(self) -> str:
        """从主对话历史中提取关键上下文供子代理参考。"""
        messages = self._memory.get_messages()
        # 提取最近的用户消息和工具调用结果
        summary_parts = []
        for msg in messages[-6:]:  # 最近 6 条消息
            role = msg.get("role", "")
            content = msg.get("content", "")
            if role == "user" and content:
                summary_parts.append(f"用户: {content[:200]}")
            elif role == "assistant" and content:
                summary_parts.append(f"助手: {content[:200]}")
        return "\n".join(summary_parts) if summary_parts else ""
```

### 3.8 元工具：explore_data 升级

将现有的 `explore_data` 元工具升级为子代理调度器：

```python
# excelmanus/tools/subagent_tools.py

async def delegate_to_subagent(
    agent_name: str,
    task: str,
) -> str:
    """将任务委派给指定子代理执行。

    Args:
        agent_name: 子代理名称（explorer / analyst / writer / coder）
        task: 任务描述

    Returns:
        子代理执行结果摘要
    """
    # 由 AgentEngine 注入实际的执行逻辑
    ...


async def list_subagents() -> str:
    """列出所有可用的子代理及其描述。"""
    ...
```

---

## 四、执行计划

### Step 1：数据模型与工具过滤（1 天）
- [ ] 创建 `excelmanus/subagent/` 包
- [ ] 实现 `models.py`（SubagentConfig, SubagentResult）
- [ ] 实现 `tool_filter.py`（FilteredToolRegistry）
- [ ] 单元测试

### Step 2：内置子代理定义（0.5 天）
- [ ] 实现 `builtin.py`（Explorer, Analyst, Writer, Coder）
- [ ] 验证工具集合的合理性

### Step 3：子代理注册表（1 天）
- [ ] 实现 `registry.py`（SubagentRegistry）
- [ ] 支持从 .md 文件加载自定义子代理
- [ ] 复用 SkillpackLoader 的 frontmatter 解析
- [ ] 单元测试

### Step 4：核心执行器（2-3 天）
- [ ] 实现 `executor.py`（SubagentExecutor）
- [ ] 独立 ConversationMemory 创建
- [ ] Agentic Loop 核心逻辑
- [ ] 持久记忆加载
- [ ] 事件发射（SubagentStart / SubagentEnd / SubagentSummary）
- [ ] 集成测试

### Step 5：AgentEngine 集成（1-2 天）
- [ ] `AgentEngine` 中注入 SubagentRegistry 和 SubagentExecutor
- [ ] 实现 `run_subagent()` 方法
- [ ] 升级 `explore_data` 元工具为子代理调度器
- [ ] 新增 `delegate_to_subagent` 和 `list_subagents` 工具
- [ ] 端到端测试

### Step 6：CLI 集成（0.5 天）
- [ ] `/subagent` 命令支持列出和手动调用子代理
- [ ] 子代理执行过程的终端渲染

---

## 五、风险与缓解

| 风险 | 影响 | 缓解措施 |
|------|------|----------|
| 子代理无限递归 | 系统崩溃 | `subagent_enabled=False` 禁止嵌套 |
| 子代理 Token 消耗过大 | 成本失控 | `max_iterations` 限制 + 结果截断 |
| 工具过滤遗漏 | 安全风险 | FilteredToolRegistry 白名单模式 |
| 子代理与主对话上下文不一致 | 结果不准确 | `parent_context` 摘要传递 |
| 并发子代理资源竞争 | 文件冲突 | 初期仅支持前台（串行）执行 |

---

## 六、后续演进

1. **Phase 5 集成**：子代理持久记忆（`memory_scope` 字段已预留）
2. **Phase 1 集成**：Hook 引擎就绪后，子代理的 `hooks` 配置生效
3. **后台执行**：支持 `asyncio.create_task()` 并发运行子代理
4. **子代理嵌套**：放开 `subagent_enabled` 限制，支持有限深度的嵌套
5. **会话恢复**：子代理对话历史持久化，支持跨会话恢复
