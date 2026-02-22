"""security 模块单一策略清单一致性测试。"""

from __future__ import annotations

from excelmanus.security import code_policy
from excelmanus.security.code_policy import CodePolicyEngine, CodeRiskTier
from excelmanus.security.module_manifest import (
    MODULE_ROOT_ALIASES,
    NETWORK_MODULES,
    RAW_SOCKET_MODULE_BLOCKED_CALLS,
    SOCKET_CONSTRUCTOR_NAMES,
    SOCKET_MODULE_BLOCKED_CALLS,
)
from excelmanus.security.sandbox_hook import generate_wrapper_script


def test_code_policy_uses_shared_network_manifest() -> None:
    """静态分析应使用共享的 NETWORK 清单，避免多处定义漂移。"""
    assert code_policy._NETWORK_MODULES is NETWORK_MODULES


def test_network_aliases_are_classified_as_yellow() -> None:
    """凡映射到 NETWORK 根模块的别名都应被判定为 NETWORK/YELLOW。"""
    engine = CodePolicyEngine()
    for alias, canonical in MODULE_ROOT_ALIASES.items():
        if canonical not in NETWORK_MODULES:
            continue
        result = engine.analyze(f"import {alias}\n")
        assert result.tier == CodeRiskTier.YELLOW
        assert "NETWORK" in result.capabilities


def test_wrapper_embeds_shared_socket_guard_manifest() -> None:
    """运行时 wrapper 应消费共享 socket 拦截清单。"""
    wrapper = generate_wrapper_script("GREEN", "/tmp")

    assert repr(SOCKET_CONSTRUCTOR_NAMES) in wrapper
    assert repr(SOCKET_MODULE_BLOCKED_CALLS) in wrapper
    assert repr(RAW_SOCKET_MODULE_BLOCKED_CALLS) in wrapper
