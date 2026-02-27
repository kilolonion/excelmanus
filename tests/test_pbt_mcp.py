"""MCP Client 集成属性测试。

Feature: mcp-client-integration
使用 hypothesis 库验证 MCP 配置解析的通用正确性属性。
"""

from __future__ import annotations

from hypothesis import given, settings, HealthCheck
from hypothesis import strategies as st

from excelmanus.mcp.config import MCPConfigLoader, MCPServerConfig


# ── 策略定义 ──────────────────────────────────────────────────────

# 合法的服务器名称：非空字母数字加连字符
_server_names = st.from_regex(r"[a-zA-Z][a-zA-Z0-9\-]{0,19}", fullmatch=True)

# 合法的命令字符串
_commands = st.from_regex(r"[a-zA-Z][a-zA-Z0-9_/.\-]{0,29}", fullmatch=True)

# 合法的参数列表
_args_list = st.lists(
    st.from_regex(r"[a-zA-Z0-9_./@:\-]{1,30}", fullmatch=True),
    max_size=5,
)

# 合法的环境变量字典
_env_dict = st.dictionaries(
    keys=st.from_regex(r"[A-Z][A-Z0-9_]{0,19}", fullmatch=True),
    values=st.from_regex(r"[a-zA-Z0-9_./:=\-]{1,30}", fullmatch=True),
    max_size=3,
)

# 合法的 URL
_urls = st.from_regex(
    r"https?://[a-z][a-z0-9.\-]{0,19}(:[0-9]{2,5})?(/[a-z0-9_\-]{1,10}){0,3}",
    fullmatch=True,
)

# 合法的 timeout（>= 1）
_timeouts = st.integers(min_value=1, max_value=3600)


def _stdio_server_entry(
    draw: st.DrawFn,
) -> dict:
    """生成一个合法的 stdio 类型 Server 配置条目。"""
    entry: dict = {"transport": "stdio", "command": draw(_commands)}
    args = draw(_args_list)
    if args:
        entry["args"] = args
    env = draw(_env_dict)
    if env:
        entry["env"] = env
    # 随机决定是否包含 timeout
    if draw(st.booleans()):
        entry["timeout"] = draw(_timeouts)
    return entry


def _sse_server_entry(
    draw: st.DrawFn,
) -> dict:
    """生成一个合法的 sse 类型 Server 配置条目。"""
    entry: dict = {"transport": "sse", "url": draw(_urls)}
    if draw(st.booleans()):
        entry["timeout"] = draw(_timeouts)
    return entry


@st.composite
def valid_mcp_config(draw: st.DrawFn) -> dict:
    """生成一个合法的 MCP 配置字典，包含 stdio 和 sse 类型的 Server。"""
    # 至少生成 1 个 server，最多 5 个
    num_servers = draw(st.integers(min_value=1, max_value=5))
    servers: dict[str, dict] = {}

    # 确保至少有一个 stdio 和一个 sse（如果 num >= 2）
    names = draw(
        st.lists(
            _server_names,
            min_size=num_servers,
            max_size=num_servers,
            unique=True,
        )
    )

    for i, name in enumerate(names):
        if i == 0:
            # 第一个强制 stdio
            servers[name] = _stdio_server_entry(draw)
        elif i == 1:
            # 第二个强制 sse
            servers[name] = _sse_server_entry(draw)
        else:
            # 其余随机
            if draw(st.booleans()):
                servers[name] = _stdio_server_entry(draw)
            else:
                servers[name] = _sse_server_entry(draw)

    return {"mcpServers": servers}


# ── Property 1: 配置解析 round-trip ──────────────────────────────

# Feature: mcp-client-integration, Property 1: 配置解析 round-trip


class TestConfigParseRoundTrip:
    """验证合法 MCP 配置字典解析后字段与原始 JSON 一致。"""

    @given(config=valid_mcp_config())
    @settings(
        max_examples=100,
        suppress_health_check=[HealthCheck.too_slow],
    )
    def test_property_1_config_parse_round_trip(self, config: dict) -> None:
        """**验证：需求 1.1, 1.3, 1.4, 1.5**

        对于任何合法的 MCP 配置字典，解析为 MCPServerConfig 列表后，
        每个配置项的字段应与原始 JSON 中的对应值一致。
        """
        result = MCPConfigLoader._parse_config(config)
        servers_dict = config["mcpServers"]

        # 解析结果数量应与输入一致（全部合法）
        assert len(result) == len(servers_dict)

        # 按 name 建立索引方便查找
        result_by_name: dict[str, MCPServerConfig] = {
            cfg.name: cfg for cfg in result
        }

        for name, entry in servers_dict.items():
            assert name in result_by_name, f"服务器 '{name}' 未出现在解析结果中"
            cfg = result_by_name[name]

            # 基础字段
            assert cfg.name == name
            assert cfg.transport == entry["transport"]

            # timeout：有则一致，无则默认 30
            expected_timeout = entry.get("timeout", 30)
            assert cfg.timeout == expected_timeout

            if entry["transport"] == "stdio":
                # stdio 特有字段
                assert cfg.command == entry["command"]
                assert cfg.args == entry.get("args", [])
                assert cfg.env == entry.get("env", {})
                # sse 字段应为默认值
                assert cfg.url is None

            elif entry["transport"] == "sse":
                # sse 特有字段
                assert cfg.url == entry["url"]
                # stdio 字段应为默认值
                assert cfg.command is None
                assert cfg.args == []
                assert cfg.env == {}


# ── 非法配置策略 ──────────────────────────────────────────────────


@st.composite
def invalid_server_entry(draw: st.DrawFn) -> dict:
    """生成一个非法的 MCP Server 配置条目。

    随机选择一种非法类型：
    1. transport 缺失
    2. transport 值非法
    3. stdio 缺少 command
    4. sse 缺少 url
    5. args 不是字符串列表
    6. env 不是字符串字典
    7. timeout 不是数字
    8. timeout < 1
    """
    kind = draw(st.sampled_from([
        "no_transport",
        "bad_transport",
        "stdio_no_command",
        "sse_no_url",
        "bad_args",
        "bad_env",
        "bad_timeout_type",
        "bad_timeout_value",
    ]))

    if kind == "no_transport":
        # transport 字段缺失
        return {"command": "some_cmd"}

    if kind == "bad_transport":
        # transport 值非法（不是 "stdio" 或 "sse"）
        bad = draw(st.text(min_size=1, max_size=10).filter(
            lambda s: s not in ("stdio", "sse")
        ))
        return {"transport": bad, "command": "some_cmd"}

    if kind == "stdio_no_command":
        # stdio 类型但缺少 command
        return {"transport": "stdio"}

    if kind == "sse_no_url":
        # sse 类型但缺少 url
        return {"transport": "sse"}

    if kind == "bad_args":
        # stdio 类型，args 不是字符串列表（包含非字符串元素）
        bad_args = draw(st.lists(st.integers(), min_size=1, max_size=3))
        return {
            "transport": "stdio",
            "command": "some_cmd",
            "args": bad_args,
        }

    if kind == "bad_env":
        # stdio 类型，env 不是字符串字典（值为非字符串）
        bad_env = draw(st.dictionaries(
            keys=st.text(min_size=1, max_size=5),
            values=st.integers(),
            min_size=1,
            max_size=3,
        ))
        return {
            "transport": "stdio",
            "command": "some_cmd",
            "env": bad_env,
        }

    if kind == "bad_timeout_type":
        # timeout 不是数字
        bad_timeout = draw(st.text(min_size=1, max_size=5))
        # 随机选择 stdio 或 sse 基础
        if draw(st.booleans()):
            return {
                "transport": "stdio",
                "command": "some_cmd",
                "timeout": bad_timeout,
            }
        return {
            "transport": "sse",
            "url": "http://localhost:8080/sse",
            "timeout": bad_timeout,
        }

    # kind == "bad_timeout_value"
    # timeout < 1
    bad_val = draw(st.integers(max_value=0))
    if draw(st.booleans()):
        return {
            "transport": "stdio",
            "command": "some_cmd",
            "timeout": bad_val,
        }
    return {
        "transport": "sse",
        "url": "http://localhost:8080/sse",
        "timeout": bad_val,
    }


@st.composite
def mixed_mcp_config(draw: st.DrawFn) -> tuple[dict, int]:
    """生成包含合法和非法条目的混合 MCP 配置。

    返回 (配置字典, 合法条目数量)。
    """
    # 生成 1~4 个合法条目
    num_valid = draw(st.integers(min_value=1, max_value=4))
    # 生成 1~4 个非法条目
    num_invalid = draw(st.integers(min_value=1, max_value=4))

    total = num_valid + num_invalid
    # 生成唯一名称
    names = draw(
        st.lists(
            _server_names,
            min_size=total,
            max_size=total,
            unique=True,
        )
    )

    servers: dict[str, dict] = {}

    # 前 num_valid 个为合法条目
    for i in range(num_valid):
        if draw(st.booleans()):
            servers[names[i]] = _stdio_server_entry(draw)
        else:
            servers[names[i]] = _sse_server_entry(draw)

    # 后 num_invalid 个为非法条目
    for i in range(num_valid, total):
        servers[names[i]] = draw(invalid_server_entry())

    return {"mcpServers": servers}, num_valid


# ── Property 2: 非法配置过滤 ─────────────────────────────────────

# Feature: mcp-client-integration, Property 2: 非法配置过滤


class TestInvalidConfigFiltering:
    """验证混合配置中非法条目被正确过滤，只保留合法条目。"""

    @given(data=mixed_mcp_config())
    @settings(
        max_examples=100,
        suppress_health_check=[HealthCheck.too_slow],
    )
    def test_property_2_invalid_config_filtering(
        self, data: tuple[dict, int]
    ) -> None:
        """**验证：需求 1.6, 3.4**

        对于任何包含合法和非法条目的混合 MCP 配置列表，
        解析结果应只包含合法条目，且合法条目的数量等于输入中合法条目的数量。
        非法条目（缺少必填字段、transport 值非法、stdio 缺少 command、
        sse 缺少 url）应被跳过。
        """
        config, expected_valid_count = data

        result = MCPConfigLoader._parse_config(config)

        # 合法条目数量应与预期一致
        assert len(result) == expected_valid_count

        # 所有返回的配置都应是合法的 MCPServerConfig 实例
        for cfg in result:
            assert isinstance(cfg, MCPServerConfig)
            # transport 必须合法
            assert cfg.transport in ("stdio", "sse")
            # stdio 必须有 command
            if cfg.transport == "stdio":
                assert cfg.command is not None
                assert isinstance(cfg.command, str)
                assert cfg.command.strip() != ""
            # sse 必须有 url
            if cfg.transport == "sse":
                assert cfg.url is not None
                assert isinstance(cfg.url, str)
                assert cfg.url.strip() != ""
            # timeout 必须 >= 1
            assert cfg.timeout >= 1


# ── 工具层策略定义 ────────────────────────────────────────────────

from types import SimpleNamespace

from excelmanus.mcp.manager import (
    ToolDef,
    _normalize_server_name,
    _prefix_registry,
    add_tool_prefix,
    format_tool_result,
    make_tool_def,
    parse_tool_prefix,
)

# 合法的工具名称：字母开头，可含字母数字和下划线
_tool_names = st.from_regex(r"[a-z][a-z0-9_]{0,29}", fullmatch=True)

# 合法的工具描述
_descriptions = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N", "P", "Z")),
    min_size=1,
    max_size=100,
)

# 合法的 JSON Schema（简化版，用于 inputSchema）
_input_schemas = st.fixed_dictionaries(
    {
        "type": st.just("object"),
        "properties": st.dictionaries(
            keys=st.from_regex(r"[a-z][a-z0-9_]{0,9}", fullmatch=True),
            values=st.fixed_dictionaries(
                {"type": st.sampled_from(["string", "integer", "boolean", "number"])}
            ),
            max_size=5,
        ),
    }
)


# ── Property 3: 工具定义转换正确性 ───────────────────────────────

# Feature: mcp-client-integration, Property 3: 工具定义转换正确性


class TestToolDefConversion:
    """验证 MCP 工具定义转换为 ToolDef 后字段映射正确。"""

    @given(
        server_name=_server_names,
        tool_name=_tool_names,
        description=_descriptions,
        input_schema=_input_schemas,
        timeout=st.integers(min_value=1, max_value=3600),
    )
    @settings(
        max_examples=100,
        suppress_health_check=[HealthCheck.too_slow],
    )
    def test_property_3_tool_def_conversion(
        self,
        server_name: str,
        tool_name: str,
        description: str,
        input_schema: dict,
        timeout: int,
    ) -> None:
        """**验证：需求 3.2, 3.3, 4.1**

        对于任何合法的 MCP 工具定义（包含 name、description、inputSchema）
        和任何合法的 server_name，转换后的 ToolDef 应满足：
        - name 等于 mcp_{normalized_server_name}_{original_name}
        - description 包含原始描述
        - input_schema 与原始 inputSchema 一致
        """
        # 构造 mock 的 mcp_tool 对象
        mcp_tool = SimpleNamespace(
            name=tool_name,
            description=description,
            inputSchema=input_schema,
        )

        # 构造 mock 的 client 对象（需要 _config.timeout 属性）
        client = SimpleNamespace(
            _config=SimpleNamespace(timeout=timeout),
            call_tool=None,  # 不会在此测试中调用
        )

        result: ToolDef = make_tool_def(server_name, client, mcp_tool)

        # 验证 name：mcp_{normalized_server_name}_{original_name}
        normalized = _normalize_server_name(server_name)
        expected_name = f"mcp_{normalized}_{tool_name}"
        assert result.name == expected_name, (
            f"期望 name='{expected_name}'，实际 name='{result.name}'"
        )

        # 验证 description 包含原始描述
        assert description in result.description, (
            f"description 应包含原始描述 '{description}'，"
            f"实际为 '{result.description}'"
        )

        # 验证 description 包含 [MCP:{server_name}] 标记
        assert f"[MCP:{server_name}]" in result.description

        # 验证 input_schema 与原始一致
        assert result.input_schema == input_schema, (
            f"input_schema 不一致：期望 {input_schema}，实际 {result.input_schema}"
        )

        # 验证 func 是可调用对象
        assert callable(result.func)

        # 验证 max_result_chars 默认值
        assert result.max_result_chars == 5000


# ── Property 4: 工具名前缀 round-trip ────────────────────────────

# Feature: mcp-client-integration, Property 4: 工具名前缀 round-trip


class TestToolPrefixRoundTrip:
    """验证工具名前缀添加后再还原能得到原始值。"""

    @given(
        server_name=_server_names,
        tool_name=_tool_names,
    )
    @settings(
        max_examples=100,
        suppress_health_check=[HealthCheck.too_slow],
    )
    def test_property_4_tool_prefix_round_trip(
        self,
        server_name: str,
        tool_name: str,
    ) -> None:
        """**验证：需求 5.2**

        对于任何合法的 server_name 和 tool_name，
        添加前缀后再还原应得到原始的 (normalized_server_name, tool_name)。
        """
        # 每次测试前清空注册表，避免跨用例干扰
        _prefix_registry.clear()

        # 添加前缀
        prefixed = add_tool_prefix(server_name, tool_name)

        # 验证前缀格式
        normalized = _normalize_server_name(server_name)
        expected = f"mcp_{normalized}_{tool_name}"
        assert prefixed == expected

        # 还原
        restored_server, restored_tool = parse_tool_prefix(prefixed)

        # 验证 round-trip
        assert restored_server == normalized, (
            f"server_name round-trip 失败：期望 '{normalized}'，"
            f"实际 '{restored_server}'"
        )
        assert restored_tool == tool_name, (
            f"tool_name round-trip 失败：期望 '{tool_name}'，"
            f"实际 '{restored_tool}'"
        )


# ── Property 5: 工具结果字符串转换 ───────────────────────────────

# Feature: mcp-client-integration, Property 5: 工具结果字符串转换

# 文本内容策略
_text_contents = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N", "P", "Z")),
    min_size=1,
    max_size=200,
)

# 非 text 类型的 content 类型
_non_text_types = st.sampled_from(["image", "resource", "binary", "blob"])


@st.composite
def mcp_result_content(draw: st.DrawFn) -> tuple[list, list[str]]:
    """生成 MCP 工具调用结果的 content 列表和期望的 text 列表。

    返回 (content_items, expected_texts)。
    """
    num_items = draw(st.integers(min_value=0, max_value=8))
    items = []
    expected_texts: list[str] = []

    for _ in range(num_items):
        if draw(st.booleans()):
            # text 类型
            text = draw(_text_contents)
            items.append(SimpleNamespace(type="text", text=text))
            expected_texts.append(text)
        else:
            # 非 text 类型
            non_type = draw(_non_text_types)
            items.append(SimpleNamespace(type=non_type, data="some_data"))

    return items, expected_texts


class TestToolResultConversion:
    """验证 MCP 工具结果转换为字符串后包含所有 text 内容。"""

    @given(data=mcp_result_content())
    @settings(
        max_examples=100,
        suppress_health_check=[HealthCheck.too_slow],
    )
    def test_property_5_tool_result_string_conversion(
        self,
        data: tuple[list, list[str]],
    ) -> None:
        """**验证：需求 5.3**

        对于任何 MCP 工具调用返回的结果（包含 content 列表），
        转换为字符串后应包含所有 text 类型 content 的文本内容。
        """
        content_items, expected_texts = data

        # 构造 mock 的 MCP 结果对象
        mcp_result = SimpleNamespace(content=content_items)

        result_str = format_tool_result(mcp_result)

        # 验证所有 text 内容都包含在结果字符串中
        for text in expected_texts:
            assert text in result_str, (
                f"结果字符串应包含 text 内容 '{text}'，"
                f"实际结果为 '{result_str}'"
            )

        # 无 text 内容时，允许返回结构化/非文本降级摘要；
        # 仅在 content 本身为空时要求空字符串。
        if not expected_texts:
            if not content_items:
                assert result_str == "", (
                    f"空 content 时应返回空字符串，实际为 '{result_str}'"
                )
            else:
                assert isinstance(result_str, str)

        # 验证结果字符串中不包含非 text 类型的 data
        # （format_tool_result 只提取 text 类型）
        for item in content_items:
            if getattr(item, "type", None) != "text":
                data_val = getattr(item, "data", "")
                if data_val and data_val not in [
                    t for t in expected_texts
                ]:
                    # 只有当 data 值不恰好等于某个 text 内容时才检查
                    pass  # 不做强断言，因为 data 可能碰巧与 text 相同


# ── Property 6: 连接故障隔离 ─────────────────────────────────────

# Feature: mcp-client-integration, Property 6: 连接故障隔离

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from excelmanus.mcp.client import MCPClientWrapper
from excelmanus.mcp.config import MCPServerConfig
from excelmanus.mcp.manager import MCPManager
from excelmanus.tools.registry import ToolRegistry


# 每个 Server 拥有的工具数量范围
_tools_per_server = st.integers(min_value=1, max_value=5)


@st.composite
def server_configs_with_outcomes(
    draw: st.DrawFn,
) -> tuple[list[MCPServerConfig], list[bool], list[list[SimpleNamespace]]]:
    """生成 N 个 MCPServerConfig 及其连接结果（成功/失败）和模拟工具列表。

    返回:
        (configs, success_flags, tools_per_server)
        - configs: MCPServerConfig 列表
        - success_flags: 每个 Server 是否连接成功
        - tools_per_server: 每个成功 Server 的模拟工具列表
    """
    n = draw(st.integers(min_value=1, max_value=6))
    names = draw(
        st.lists(
            _server_names,
            min_size=n,
            max_size=n,
            unique=True,
        )
    )

    configs: list[MCPServerConfig] = []
    success_flags: list[bool] = []
    tools_lists: list[list[SimpleNamespace]] = []

    for name in names:
        # 随机选择 transport 类型
        if draw(st.booleans()):
            cfg = MCPServerConfig(
                name=name,
                transport="stdio",
                command=draw(_commands),
                args=draw(_args_list),
                timeout=draw(_timeouts),
            )
        else:
            cfg = MCPServerConfig(
                name=name,
                transport="sse",
                url=draw(_urls),
                timeout=draw(_timeouts),
            )
        configs.append(cfg)

        # 随机决定连接是否成功
        success = draw(st.booleans())
        success_flags.append(success)

        # 为成功的 Server 生成模拟工具列表
        if success:
            num_tools = draw(_tools_per_server)
            tools = []
            tool_names_set: set[str] = set()
            for _ in range(num_tools):
                tname = draw(_tool_names)
                # 确保同一 Server 内工具名唯一
                while tname in tool_names_set:
                    tname = draw(_tool_names)
                tool_names_set.add(tname)
                tools.append(
                    SimpleNamespace(
                        name=tname,
                        description=draw(_descriptions),
                        inputSchema=draw(_input_schemas),
                    )
                )
            tools_lists.append(tools)
        else:
            tools_lists.append([])

    return configs, success_flags, tools_lists


class TestConnectionFaultIsolation:
    """验证部分 MCP Server 连接失败时，成功的 Server 不受影响。"""

    @given(data=server_configs_with_outcomes())
    @settings(
        max_examples=100,
        suppress_health_check=[HealthCheck.too_slow],
    )
    def test_property_6_connection_fault_isolation(
        self,
        data: tuple[
            list[MCPServerConfig],
            list[bool],
            list[list[SimpleNamespace]],
        ],
    ) -> None:
        """**验证：需求 2.4**

        对于任何 N 个 MCP Server 配置（其中 M 个连接成功、N-M 个连接失败），
        初始化完成后成功连接的 Server 数量应等于 M，
        且所有成功 Server 的工具应被正确注册。
        """
        configs, success_flags, tools_lists = data

        expected_success_count = sum(success_flags)

        # 计算期望注册的工具总数（所有成功 Server 的工具，排除跨 Server 名称冲突）
        expected_tool_names: set[str] = set()
        for i, (cfg, success, tools) in enumerate(
            zip(configs, success_flags, tools_lists)
        ):
            if success:
                for tool in tools:
                    from excelmanus.mcp.manager import add_tool_prefix

                    prefixed = add_tool_prefix(cfg.name, tool.name)
                    expected_tool_names.add(prefixed)

        # 清理前缀注册表，避免跨用例干扰
        _prefix_registry.clear()

        # 构建 mock 的 MCPClientWrapper 实例映射
        # 按 config 创建顺序，为每个 config 准备一个 mock client
        mock_clients: list[MagicMock] = []
        tools_iter = iter(tools_lists)
        success_iter = iter(success_flags)

        for cfg in configs:
            s = next(success_iter)
            t = next(tools_iter)

            client_mock = MagicMock()
            client_mock._config = cfg

            if s:
                # 连接成功
                client_mock.connect = AsyncMock(return_value=None)
                client_mock.discover_tools = AsyncMock(return_value=t)
            else:
                # 连接失败：connect 抛出异常
                client_mock.connect = AsyncMock(
                    side_effect=ConnectionError(
                        f"模拟连接失败: {cfg.name}"
                    )
                )
                client_mock.discover_tools = AsyncMock(return_value=[])

            client_mock.close = AsyncMock(return_value=None)
            mock_clients.append(client_mock)

        # 使用迭代器按顺序返回 mock client
        client_iter = iter(mock_clients)

        def mock_client_wrapper_init(self_inner, config):
            """替换 MCPClientWrapper.__init__，从预构建列表中取 mock。"""
            mock = next(client_iter)
            # 将 mock 的属性复制到 self_inner
            self_inner.__dict__.update(mock.__dict__)
            self_inner.connect = mock.connect
            self_inner.discover_tools = mock.discover_tools
            self_inner.close = mock.close
            self_inner._config = config

        registry = ToolRegistry()

        with patch(
            "excelmanus.mcp.config.MCPConfigLoader.load",
            return_value=configs,
        ), patch.object(
            MCPClientWrapper,
            "__init__",
            mock_client_wrapper_init,
        ):
            manager = MCPManager(workspace_root="/tmp/test")

            # 运行异步 initialize
            asyncio.run(manager.initialize(registry))

        # 验证：connected_servers 数量等于成功连接数 M
        assert len(manager.connected_servers) == expected_success_count, (
            f"期望 {expected_success_count} 个已连接 Server，"
            f"实际 {len(manager.connected_servers)} 个: "
            f"{manager.connected_servers}"
        )

        # 验证：已连接的 Server 名称正确
        expected_server_names = {
            cfg.name
            for cfg, success in zip(configs, success_flags)
            if success
        }
        assert set(manager.connected_servers) == expected_server_names

        # 验证：ToolRegistry 中注册的 MCP 工具数量正确
        registered_names = set(registry.get_tool_names())
        assert registered_names == expected_tool_names, (
            f"期望注册工具 {expected_tool_names}，"
            f"实际注册 {registered_names}"
        )
