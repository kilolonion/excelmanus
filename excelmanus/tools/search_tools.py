"""并发搜索工具：通过多查询 fan-out 提升搜索覆盖面。

当 Exa MCP 可用时，`parallel_search` 将单次搜索请求拆分为多个查询变体，
并行调用 Exa 搜索，去重聚合后返回结果。
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import TYPE_CHECKING, Any

from excelmanus.logger import get_logger
from excelmanus.mcp.manager import format_tool_result
from excelmanus.tools.registry import ToolDef

if TYPE_CHECKING:
    from excelmanus.mcp.manager import MCPManager

logger = get_logger("tools.search")

# Exa MCP Server 名称
_EXA_SERVER_NAME = "exa"

# Exa 搜索工具的原始名称（不含 mcp_exa_ 前缀）
_EXA_SEARCH_TOOL_NAMES = ("web_search_exa", "search")

# 中文停用词（用于关键词提取）
# 单字停用词 + 多字符停用词/虚词
_CN_STOP_WORDS = frozenset(
    # 单字停用词
    list("的了吗呢啊吧呀哦嗯是在有不和与及或也都还又要被让给把对从")
    # 多字符停用词（代词、疑问词、指令词等）
    + [
        "什么", "怎么", "为什么", "哪个", "哪些",
        "我们", "你们", "他们", "她们", "它们",
        "这个", "那个", "这些", "那些",
        "者", "们",
        "帮", "搜", "搜索", "一下", "查", "查询", "找",
        "看", "看看", "看一下", "介绍", "说说", "讲讲",
        "请", "麻烦", "能否", "可以", "能不能", "有没有",
    ]
)

# 英文停用词
_EN_STOP_WORDS = frozenset(
    "a an the is are was were be been being "
    "do does did have has had having "
    "i me my we our you your he she it they them "
    "this that these those what which who whom how "
    "and or but not no nor so for to of in on at by "
    "search find help please tell about".split()
)


def _extract_keywords(query: str) -> str:
    """从查询中提取关键词（去除停用词和虚词）。"""
    # 分词：按空格和中文标点分割
    tokens = re.split(r'[\s,，。！？!?;；、·]+', query)
    # 中文按字符拆分单字停用词
    filtered = []
    for token in tokens:
        token = token.strip()
        if not token:
            continue
        low = token.lower()
        if low in _EN_STOP_WORDS or low in _CN_STOP_WORDS:
            continue
        # 对纯中文 token，逐字过滤停用字
        if re.match(r'^[\u4e00-\u9fff]+$', token) and len(token) > 1:
            cleaned = ''.join(c for c in token if c not in _CN_STOP_WORDS)
            if cleaned:
                filtered.append(cleaned)
        else:
            filtered.append(token)
    return ' '.join(filtered) if filtered else query


def _generate_query_variants(query: str, num_variants: int = 3) -> list[str]:
    """生成查询变体，不使用 LLM。

    策略：
    1. 原始查询
    2. 关键词提取版本（更精准）
    3. 扩展版本（附加"最新信息"等上下文）
    """
    variants = [query]

    if num_variants >= 2:
        keywords = _extract_keywords(query)
        if keywords != query and len(keywords) >= 2:
            variants.append(keywords)

    if num_variants >= 3:
        # 判断语言倾向
        has_chinese = bool(re.search(r'[\u4e00-\u9fff]', query))
        if has_chinese:
            variants.append(f"{query} 最新信息 概述")
        else:
            variants.append(f"{query} latest overview")

    # 去重并保持顺序
    seen: set[str] = set()
    unique: list[str] = []
    for v in variants:
        v_norm = v.strip()
        if v_norm and v_norm not in seen:
            seen.add(v_norm)
            unique.append(v_norm)

    return unique[:num_variants]


def _find_exa_search_tool(mcp_manager: "MCPManager") -> str | None:
    """在已连接的 Exa client 中查找搜索工具名称。"""
    client = mcp_manager._clients.get(_EXA_SERVER_NAME)
    if client is None:
        return None

    # 从已注册的 tool_scopes 中查找 Exa 搜索工具的原始名称
    for prefixed_name in mcp_manager.tool_scopes:
        if prefixed_name.startswith(f"mcp_{_EXA_SERVER_NAME}_"):
            # 提取原始工具名
            original = prefixed_name[len(f"mcp_{_EXA_SERVER_NAME}_"):]
            if any(known in original for known in _EXA_SEARCH_TOOL_NAMES):
                return original

    # 回退：直接检查 client 缓存的工具列表
    for tool in getattr(client, "_tools", []):
        tool_name = getattr(tool, "name", "")
        if any(known in tool_name for known in _EXA_SEARCH_TOOL_NAMES):
            return tool_name

    return None


def _deduplicate_results(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """按 URL 去重搜索结果。"""
    seen_urls: set[str] = set()
    unique: list[dict[str, Any]] = []
    for item in results:
        url = item.get("url", "")
        if url and url in seen_urls:
            continue
        if url:
            seen_urls.add(url)
        unique.append(item)
    return unique


def _parse_exa_results(raw_text: str) -> list[dict[str, Any]]:
    """解析 Exa 返回的结果文本为结构化列表。"""
    # Exa 返回的通常是 JSON 或纯文本格式
    try:
        parsed = json.loads(raw_text)
        if isinstance(parsed, list):
            return parsed
        if isinstance(parsed, dict):
            # 常见格式：{"results": [...]}
            for key in ("results", "data", "items", "webPages"):
                if key in parsed and isinstance(parsed[key], list):
                    return parsed[key]
            return [parsed]
    except (json.JSONDecodeError, TypeError):
        pass

    # 纯文本回退：将整个文本作为单条结果
    return [{"text": raw_text}] if raw_text.strip() else []


async def _parallel_search_impl(
    mcp_manager: "MCPManager",
    query: str,
    num_queries: int = 3,
) -> str:
    """并发搜索的核心实现。"""
    num_queries = max(1, min(num_queries, 5))
    client = mcp_manager._clients.get(_EXA_SERVER_NAME)
    if client is None:
        return json.dumps(
            {"error": "Exa 搜索服务不可用，请稍后重试或使用其他搜索工具"},
            ensure_ascii=False,
        )

    search_tool = _find_exa_search_tool(mcp_manager)
    if search_tool is None:
        return json.dumps(
            {"error": "未找到 Exa 搜索工具，Exa 可能尚未完成初始化"},
            ensure_ascii=False,
        )

    # 生成查询变体
    variants = _generate_query_variants(query, num_variants=num_queries)
    logger.info(
        "并发搜索: 原始查询=%r, 变体=%s",
        query, variants,
    )

    # 并发调用 Exa
    async def _search_one(q: str) -> tuple[str, str]:
        """执行单个搜索查询，返回 (query, result_text)。"""
        try:
            result = await asyncio.wait_for(
                client.call_tool(search_tool, {"query": q}),
                timeout=30,
            )
            return (q, format_tool_result(result))
        except asyncio.TimeoutError:
            logger.warning("搜索超时: query=%r", q)
            return (q, "")
        except Exception as exc:
            logger.warning("搜索失败: query=%r, error=%s", q, exc)
            return (q, "")

    raw_results = await asyncio.gather(
        *[_search_one(v) for v in variants],
        return_exceptions=True,
    )

    # 聚合结果
    all_items: list[dict[str, Any]] = []
    query_summaries: list[str] = []
    for r in raw_results:
        if isinstance(r, BaseException):
            logger.warning("搜索异常: %s", r)
            continue
        q, text = r  # type: ignore[misc]
        if text:
            items = _parse_exa_results(text)
            all_items.extend(items)
            query_summaries.append(f"'{q}': {len(items)} 条结果")

    # 去重
    unique_items = _deduplicate_results(all_items)

    output = {
        "query": query,
        "variants_used": variants,
        "total_results": len(unique_items),
        "query_summaries": query_summaries,
        "results": unique_items,
    }

    result_json = json.dumps(output, ensure_ascii=False, default=str)
    logger.info(
        "并发搜索完成: %d 个变体, %d 条原始结果, %d 条去重后",
        len(variants), len(all_items), len(unique_items),
    )
    return result_json


def get_tools(mcp_manager: "MCPManager") -> list[ToolDef]:
    """创建并发搜索工具，绑定 MCPManager 实例。

    Args:
        mcp_manager: MCP 管理器实例，用于访问 Exa client。

    Returns:
        工具定义列表（当前仅包含 parallel_search）。
    """

    def _sync_parallel_search(
        query: str,
        num_queries: int = 3,
    ) -> str:
        """并发搜索（同步包装）。"""
        import concurrent.futures

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop is not None and loop.is_running():
            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = pool.submit(
                    asyncio.run,
                    _parallel_search_impl(mcp_manager, query, num_queries),
                )
                return future.result()
        return asyncio.run(
            _parallel_search_impl(mcp_manager, query, num_queries),
        )

    async def _async_parallel_search(
        query: str,
        num_queries: int = 3,
    ) -> str:
        """并发搜索（异步版本，优先使用）。"""
        return await _parallel_search_impl(mcp_manager, query, num_queries)

    return [
        ToolDef(
            name="parallel_search",
            description=(
                "并发网页搜索：将搜索请求自动拆分为多个查询变体并行执行，"
                "去重聚合后返回更全面的搜索结果。适用于需要广泛覆盖的信息检索。"
                "对于简单精确搜索，可直接使用 mcp_exa_* 工具。"
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "搜索查询文本",
                    },
                    "num_queries": {
                        "type": "integer",
                        "description": "并发查询数量（1-5），默认 3",
                        "minimum": 1,
                        "maximum": 5,
                        "default": 3,
                    },
                },
                "required": ["query"],
            },
            func=_sync_parallel_search,
            async_func=_async_parallel_search,
            max_result_chars=8000,
            write_effect="none",
        ),
    ]
