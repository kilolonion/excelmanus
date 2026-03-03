# LLM 工具路由实现文档

> **负责人**：开发者 B
> **优先级**：P1（全新功能，依赖 chitchat 路由完成后集成）
> **预估工时**：3-5 天
> **前置依赖**：chitchat 快速通道已合入（开发者 A）

---

## 1. 背景与目标

当前工具过滤管线基于 `write_hint`（正则词法推断）和 `task_tags`（词法标签）做静态裁剪，
对模糊/复杂查询无法精准分类，导致 fallback 到 `all_tools`（暴露全部 ~25 个工具 schema，约 5K-8K tokens）。

**目标**：在 `router.py` 中引入 Qwen3.5-flash LLM 分类器，对非 chitchat 消息做工具域分类，
将工具 schema 暴露量从 ~25 个降至 ~8-15 个（节省 30-50% 的工具 schema tokens）。

### 1.1 基准测试结果（已验证）

| 指标 | 数值 |
| --- | --- |
| 模型 | qwen3.5-flash（thinking=off） |
| 准确率 | 97.4%（38/39） |
| P50 延迟 | 0.375s |
| 稳态延迟 | 0.29s ~ 0.44s |
| 首次冷启动 | ~6s（阿里云 serverless） |

基准测试脚本：`scripts/bench_tool_routing.py`

---

## 2. 分类标签体系

### 2.1 标签定义

| 标签 | 含义 | 暴露的域工具 | 暴露的元工具 |
| --- | --- | --- | --- |
| `data_read` | 读取/查看/分析/对比/筛选/统计 Excel 数据 | read_excel, inspect_excel_files, filter_data, compare_excel, scan_excel_snapshot, search_excel_values, list_sheets, focus_window, discover_file_relationships, memory_read_topic, introspect_capability | activate_skill, ask_user, finish_task |
| `data_write` | 修改/填充/写入/格式化/排序/合并 Excel 数据 | data_read 全部 + run_code, write_text_file, edit_text_file, copy_file, rename_file | 全部元工具 |
| `chart` | 创建图表/可视化 | data_read 全部 + create_excel_chart, run_code | 全部元工具 |
| `vision` | 图片识别/截图还原表格/OCR | data_read 全部 + read_image, rebuild_excel_from_spec, verify_excel_replica, extract_table_spec, run_code | 全部元工具 |
| `code` | 编写脚本/执行代码/shell/非 Excel 文件操作 | run_code, run_shell, write_text_file, edit_text_file, read_text_file, list_directory, copy_file, rename_file, delete_file, introspect_capability | 全部元工具 |
| `all_tools` | 复杂多步骤/跨域/无法确定 | 全部域工具 | 全部元工具 |

### 2.2 标签与现有 task_tags 的关系

LLM 分类器输出的标签**不替代** `task_tags`，而是作为一个**新的过滤维度**叠加到现有管线中：

```
router.py                    meta_tools.py
  │                              │
  ├─ write_hint (词法) ───────► write_hint 过滤（read_only 模式）
  ├─ task_tags (词法) ────────► TAG_EXCLUDED_TOOLS 过滤
  └─ route_tool_tags (LLM) ──► ROUTE_TOOL_SCOPE 过滤（新增）
```

三层过滤**依次执行**，效果叠加。

---

## 3. 架构设计

### 3.1 调用流程

```
用户消息
  │
  ▼
router.py: route()
  │
  ├── 斜杠命令 → 斜杠直连
  ├── chitchat 正则 → chitchat 快速通道（开发者 A）
  │
  └── 非斜杠非 chitchat
        │
        ├── 词法分类（write_hint + task_tags）── 同步，0ms
        │
        └── LLM 分类器（并行）── 异步，~350ms
              │
              ▼
        _classify_tool_route_llm()
              │
              ├── 成功 → route_tool_tags 写入 SkillMatchResult
              ├── 超时（>2s）→ fallback "all_tools"
              └── 异常 → fallback "all_tools"
              │
              ▼
        meta_tools.py: build_v5_tools_impl()
              │
              └── 新增：按 route_tool_tags 过滤域工具
```

### 3.2 并行化策略

LLM 分类器调用应与**文件结构扫描**并行执行，不增加额外延迟：

```python
# router.py route() 方法中
async def route(self, user_message, ...):
    # ... chitchat 短路已处理 ...

    # 并行启动：LLM 分类 + 文件结构扫描
    llm_task = asyncio.create_task(
        self._classify_tool_route_llm(user_message)
    )

    # 文件结构扫描（已有逻辑）
    result = await self._build_all_tools_result(...)

    # 收割 LLM 分类结果
    route_tool_tags = await llm_task  # 或超时 fallback

    # 将 route_tool_tags 写入结果
    return replace(result, route_tool_tags=route_tool_tags)
```

---

## 4. 涉及文件与改动点

### 4.1 `excelmanus/tools/policy.py` — 新增路由工具域映射

在 `TAG_EXCLUDED_TOOLS` 之后新增：

```python
# ── 基于 LLM 路由标签的工具域映射 ────────────────────────
# route_tag → 该路由下允许暴露的域工具白名单。
# 不在白名单中的域工具将被隐藏（元工具不受影响）。
# "all_tools" 标签不做任何过滤。

_DATA_READ_TOOLS: frozenset[str] = frozenset({
    "read_excel", "inspect_excel_files", "filter_data", "compare_excel",
    "scan_excel_snapshot", "search_excel_values",
    "list_sheets", "focus_window",
    "discover_file_relationships", "memory_read_topic",
    "introspect_capability",
})

ROUTE_TOOL_SCOPE: dict[str, frozenset[str]] = {
    "data_read": _DATA_READ_TOOLS,
    "data_write": _DATA_READ_TOOLS | frozenset({
        "run_code", "write_text_file", "edit_text_file",
        "copy_file", "rename_file",
    }),
    "chart": _DATA_READ_TOOLS | frozenset({
        "create_excel_chart", "run_code",
    }),
    "vision": _DATA_READ_TOOLS | frozenset({
        "read_image", "rebuild_excel_from_spec",
        "verify_excel_replica", "extract_table_spec", "run_code",
    }),
    "code": frozenset({
        "run_code", "run_shell",
        "write_text_file", "edit_text_file", "read_text_file",
        "list_directory", "copy_file", "rename_file", "delete_file",
        "introspect_capability",
    }),
    # "all_tools" 不在此映射中 → 不做过滤
}
```

### 4.2 `excelmanus/skillpacks/models.py` — 扩展 SkillMatchResult

新增字段：

```python
@dataclass(frozen=True)
class SkillMatchResult:
    # ... 已有字段 ...
    route_tool_tags: tuple[str, ...] = ()  # LLM 分类器输出的工具路由标签
```

### 4.3 `excelmanus/skillpacks/router.py` — 新增 LLM 分类器

#### 4.3.1 新增配置读取

在 `__init__` 中读取 AUX 模型配置：

```python
def __init__(self, config: ExcelManusConfig, loader: SkillpackLoader):
    self._config = config
    self._loader = loader
    # ... 已有代码 ...

    # LLM 工具路由分类器配置
    self._route_llm_enabled = (
        config.aux_enabled
        and config.aux_api_key
        and config.aux_base_url
        and config.aux_model
    )
    self._route_llm_client: AsyncOpenAI | None = None
    if self._route_llm_enabled:
        from openai import AsyncOpenAI
        self._route_llm_client = AsyncOpenAI(
            api_key=config.aux_api_key,
            base_url=config.aux_base_url,
        )
```

#### 4.3.2 新增分类方法

```python
_TOOL_ROUTE_PROMPT = """\
你是一个任务分类器。根据用户消息，判断需要哪类工具。只输出一个标签，不要输出任何其他内容。

标签定义：
- data_read: 读取、查看、分析、对比、筛选、统计Excel/CSV数据（不修改文件）
- data_write: 修改、填充、写入、格式化、排序、合并、替换Excel数据
- chart: 创建图表、画图、可视化（柱状图、饼图、折线图等）
- vision: 图片识别、截图还原表格、OCR相关
- code: 编写Python脚本、执行代码、shell命令、非Excel文件操作
- all_tools: 复杂多步骤任务、跨多种能力的任务、或无法确定类型

用户消息: {message}
标签:"""

_VALID_ROUTE_TAGS = frozenset({
    "data_read", "data_write", "chart", "vision", "code", "all_tools",
})

async def _classify_tool_route_llm(
    self,
    user_message: str,
    *,
    timeout: float = 2.0,
) -> tuple[str, ...]:
    """调用 LLM 分类器推断工具路由标签。

    返回:
        tuple[str, ...]: 路由标签元组，如 ("data_read",)
        超时或异常时返回 ("all_tools",) 作为安全 fallback。
    """
    if not self._route_llm_client:
        return ("all_tools",)

    try:
        resp = await asyncio.wait_for(
            self._route_llm_client.chat.completions.create(
                model=self._config.aux_model,
                messages=[
                    {"role": "user", "content": _TOOL_ROUTE_PROMPT.format(
                        message=user_message[:500],  # 截断过长消息
                    )},
                ],
                max_tokens=20,
                temperature=0,
                extra_body={"enable_thinking": False},
            ),
            timeout=timeout,
        )
        raw = (resp.choices[0].message.content or "").strip().lower()
        label = raw.split("\n")[0].strip().strip("`\"'")

        if label in _VALID_ROUTE_TAGS:
            logger.debug("LLM 工具路由: %s → %s", user_message[:30], label)
            return (label,)
        else:
            logger.warning("LLM 工具路由: 无效标签 '%s'，fallback all_tools", label)
            return ("all_tools",)

    except asyncio.TimeoutError:
        logger.warning("LLM 工具路由: 超时 (%.1fs)，fallback all_tools", timeout)
        return ("all_tools",)
    except Exception as exc:
        logger.warning("LLM 工具路由: 异常 %s，fallback all_tools", exc)
        return ("all_tools",)
```

#### 4.3.3 修改 route() 方法

在第 261 行（非斜杠非 chitchat 路径）中集成 LLM 分类器：

```python
# ── 2. 非斜杠消息 ──
classified_hint = write_hint or self._MODE_TO_HINT.get(chat_mode, "may_write")
lexical_tags = list(self._classify_task_tags_lexical(user_message))

# 并行启动 LLM 工具路由分类
_llm_route_task: asyncio.Task | None = None
if self._route_llm_enabled:
    _llm_route_task = asyncio.create_task(
        self._classify_tool_route_llm(user_message)
    )

# ... 已有 lexical_tags 处理逻辑 ...

# 构建基础结果
result = await self._build_all_tools_result(
    user_message=user_message,
    candidate_file_paths=candidate_file_paths,
    write_hint=classified_hint,
    task_tags=deduped_tags,
)

# 收割 LLM 分类结果
route_tool_tags: tuple[str, ...] = ()
if _llm_route_task is not None:
    route_tool_tags = await _llm_route_task

# 图片附件时强制包含 vision
if images and "vision" not in route_tool_tags:
    route_tool_tags = ("vision",)
    logger.debug("检测到图片附件，强制 route_tool_tags=vision")

return replace(result, route_tool_tags=route_tool_tags)
```

### 4.4 `excelmanus/engine_core/meta_tools.py` — 新增路由过滤层

在 `build_v5_tools_impl()` 中，在 `task_tags` 过滤之后新增：

```python
def build_v5_tools_impl(self, *, write_hint="unknown", task_tags=(), route_tool_tags=()):
    # ... 已有 write_hint 过滤 ...
    # ... 已有 task_tags 过滤 ...

    # 基于 LLM 路由标签的域工具白名单过滤
    if route_tool_tags:
        from excelmanus.tools.policy import ROUTE_TOOL_SCOPE
        # 合并多标签的白名单（取并集）
        allowed: set[str] = set()
        _has_all = False
        for tag in route_tool_tags:
            scope = ROUTE_TOOL_SCOPE.get(tag)
            if scope is not None:
                allowed |= scope
            else:
                # "all_tools" 或未知标签 → 不做过滤
                _has_all = True
                break

        if not _has_all and allowed:
            filtered_domain = [
                s for s in filtered_domain
                if s.get("function", {}).get("name", "") in allowed
            ]
            logger.debug(
                "LLM 路由过滤: tags=%s, 保留 %d 个域工具",
                route_tool_tags, len(filtered_domain),
            )

    return meta_schemas + filtered_domain
```

同时更新 `build_v5_tools()` 的签名和缓存 key：

```python
def build_v5_tools(self, *, write_hint="unknown", task_tags=(), route_tool_tags=()):
    cache_key = (
        write_hint,
        ...,  # 已有字段
        route_tool_tags,  # 新增
    )
    # ...
    tools = self.build_v5_tools_impl(
        write_hint=write_hint,
        task_tags=task_tags,
        route_tool_tags=route_tool_tags,
    )
```

### 4.5 `excelmanus/engine.py` — 传递 route_tool_tags

在 `_tool_calling_loop()` 中，将 `route_tool_tags` 从 `route_result` 传给 `build_v5_tools`：

```python
# 原代码（约第 3765 行）：
tools = self._meta_tool_builder.build_v5_tools(
    write_hint=write_hint,
    task_tags=_task_tags,
)

# 改为：
_route_tool_tags = tuple(
    getattr(current_route_result, "route_tool_tags", ()) or ()
)
tools = self._meta_tool_builder.build_v5_tools(
    write_hint=write_hint,
    task_tags=_task_tags,
    route_tool_tags=_route_tool_tags,
)
```

---

## 5. 配置项

复用已有的 AUX 模型配置，**无需新增环境变量**：

| 环境变量 | 说明 | 路由分类器用途 |
| --- | --- | --- |
| `EXCELMANUS_AUX_ENABLED` | AUX 开关 | 控制是否启用 LLM 路由分类 |
| `EXCELMANUS_AUX_API_KEY` | AUX API Key | 分类器认证 |
| `EXCELMANUS_AUX_BASE_URL` | AUX Base URL | 分类器端点 |
| `EXCELMANUS_AUX_MODEL` | AUX 模型名 | 分类器模型（推荐 qwen3.5-flash） |

**可选新增**（非必须，硬编码默认值即可）：

| 环境变量 | 默认值 | 说明 |
| --- | --- | --- |
| `EXCELMANUS_TOOL_ROUTE_ENABLED` | `true` | 独立开关，允许单独关闭路由分类 |
| `EXCELMANUS_TOOL_ROUTE_TIMEOUT` | `2.0` | 分类器超时秒数 |

---

## 6. 安全策略

### 6.1 Fallback 优先

任何异常情况一律 fallback 到 `all_tools`，**绝不丢失能力**：

| 场景 | 行为 |
| --- | --- |
| AUX 未配置 | 跳过分类，route_tool_tags=() → 不过滤 |
| LLM 返回无效标签 | fallback ("all_tools",) → 不过滤 |
| LLM 超时（>2s） | fallback ("all_tools",) → 不过滤 |
| LLM 网络异常 | fallback ("all_tools",) → 不过滤 |
| 图片附件 | 强制 ("vision",) → 暴露视觉工具 |

### 6.2 introspect_capability 安全阀

`introspect_capability` 工具在**所有路由标签**的白名单中都保留，
作为 LLM 发现被隐藏工具的安全阀。即使路由分类不准确，LLM 仍可通过
introspect_capability 查询并请求激活被隐藏的工具。

### 6.3 消息截断

分类器输入的用户消息截断到 500 字符，防止超长消息导致分类器延迟上升或 token 浪费。

---

## 7. 测试计划

### 7.1 单元测试

文件：`tests/test_tool_routing.py`（新建）

| 测试类 | 测试内容 |
| --- | --- |
| `TestClassifyToolRouteLlm` | 分类方法：正常返回、无效标签 fallback、超时 fallback、异常 fallback |
| `TestRouteToolScope` | policy.py ROUTE_TOOL_SCOPE 映射完整性验证 |
| `TestBuildV5ToolsWithRouteTag` | meta_tools 按 route_tool_tags 过滤 |
| `TestRouterIntegration` | route() 方法并行调用 LLM 分类器 |
| `TestFallbackSafety` | AUX 未配置时不过滤；异常时不过滤 |
| `TestImageForceVision` | 图片附件时强制 vision 标签 |

### 7.2 集成测试

```bash
# 使用真实 AUX API 运行基准测试（需网络）
python scripts/bench_tool_routing.py

# 单元测试
pytest tests/test_tool_routing.py -v

# 回归测试
pytest tests/test_tiered_routing.py tests/test_router_write_hint.py -v
```

### 7.3 Mock 测试模板

```python
@pytest.mark.asyncio
async def test_classify_returns_valid_label():
    """LLM 返回有效标签时正常传递。"""
    router = _make_router_with_aux()
    with patch.object(router, "_route_llm_client") as mock_client:
        mock_resp = MagicMock()
        mock_resp.choices = [MagicMock(message=MagicMock(content="data_read"))]
        mock_client.chat.completions.create = AsyncMock(return_value=mock_resp)

        result = await router._classify_tool_route_llm("帮我看一下这个表格")
        assert result == ("data_read",)


@pytest.mark.asyncio
async def test_classify_timeout_fallback():
    """LLM 超时时 fallback 到 all_tools。"""
    router = _make_router_with_aux()
    with patch.object(router, "_route_llm_client") as mock_client:
        mock_client.chat.completions.create = AsyncMock(
            side_effect=asyncio.TimeoutError()
        )
        result = await router._classify_tool_route_llm("帮我看一下这个表格")
        assert result == ("all_tools",)
```

---

## 8. Token 节省估算

| 场景 | 当前 tools tokens | 路由后 tools tokens | 节省 |
| --- | --- | --- | --- |
| data_read（只读分析） | ~6000 | ~2500 | ~58% |
| chart（画图） | ~6000 | ~3000 | ~50% |
| code（脚本执行） | ~6000 | ~3500 | ~42% |
| vision（图片还原） | ~6000 | ~3500 | ~42% |
| all_tools（复杂任务） | ~6000 | ~6000 | 0% |

配合 `write_hint=read_only` 双重过滤，data_read 场景总节省可达 **60-70%**。

---

## 9. 实现步骤（建议顺序）

1. **policy.py**：新增 `ROUTE_TOOL_SCOPE` 映射 + 完整性断言
2. **models.py**：`SkillMatchResult` 新增 `route_tool_tags` 字段
3. **router.py**：实现 `_classify_tool_route_llm()` + 集成到 `route()`
4. **meta_tools.py**：`build_v5_tools_impl()` 新增路由过滤层
5. **engine.py**：传递 `route_tool_tags` 到 `build_v5_tools()`
6. **测试**：编写 `tests/test_tool_routing.py`
7. **集成验证**：运行 bench 脚本 + 全量测试套件

---

## 10. 风险与缓解

| 风险 | 影响 | 缓解措施 |
| --- | --- | --- |
| 分类错误导致缺少必要工具 | LLM 无法完成任务 | fallback all_tools + introspect_capability 安全阀 |
| 分类延迟高 | 增加首轮响应时间 | 与文件扫描并行 + 2s 超时 |
| AUX 模型不可用 | 分类器失效 | 自动 fallback，不影响核心功能 |
| 多标签冲突 | 白名单合并可能过宽 | 取并集，宁可多暴露不遗漏 |
| Qwen3.5-flash 模型更新 | prompt 格式不兼容 | 分类器 prompt 独立维护，易于调整 |

---

## 11. 与 chitchat 路由的接口约定

| 维度 | chitchat（开发者 A） | 工具路由（开发者 B） |
| --- | --- | --- |
| 触发条件 | `_CHITCHAT_RE` 正则匹配 | 非 chitchat 的所有消息 |
| 执行顺序 | 先执行（同步，0ms） | 后执行（异步，~350ms） |
| route_mode | `"chitchat"` | `"all_tools"`（不变） |
| 新增字段 | 无 | `route_tool_tags` |
| tools 行为 | `tools = []` | 按 route_tool_tags 过滤 |
| 互不干扰 | chitchat 已 return，不会进入 LLM 分类 | LLM 分类只处理非 chitchat 消息 |

两个功能在 `router.py:route()` 中是**串行互斥**的：先 chitchat 判断，不命中才进入 LLM 分类。
`engine.py` 和 `meta_tools.py` 中的改动也互不冲突。
