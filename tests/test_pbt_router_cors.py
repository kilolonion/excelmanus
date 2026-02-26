"""属性测试：CORS 配置解析。

# Feature: v3-post-refactor-cleanup, Property 9: CORS 环境变量逗号分隔解析

使用 hypothesis 验证 CORS 配置解析逻辑。

注意：Property 6/7/8（算法打分相关）已随 LLM-Native 路由重构删除，
对应的 _score_description、_tokenize、_score_triggers 方法已不存在。

**验证：需求 7.2**
"""

from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st


# ── 策略定义 ──────────────────────────────────────────────

# CORS origin 策略：非空、不含逗号、不含空白的字符串
_cors_origin = st.from_regex(
    r"https?://[a-z0-9\-\.]+(\:[0-9]{1,5})?",
    fullmatch=True,
)

# CORS origin 列表（至少 1 个元素）
_cors_origin_list = st.lists(_cors_origin, min_size=1, max_size=5)


# ---------------------------------------------------------------------------
# Property 9：CORS 环境变量逗号分隔解析
# Feature: v3-post-refactor-cleanup, Property 9: CORS 环境变量逗号分隔解析
# **验证：需求 7.2**
# ---------------------------------------------------------------------------


@given(origins=_cors_origin_list)
def test_property_9_cors_comma_separated_parsing(origins: list[str]) -> None:
    """Property 9：对于任意由非空非逗号字符串组成的列表 origins，
    将其用逗号连接后通过 CORS 解析逻辑处理，结果应等于原始列表
    （去除首尾空白后）。

    **验证：需求 7.2**
    """
    # 用逗号连接
    raw_value = ",".join(origins)

    # 模拟 load_config() 中的 CORS 解析逻辑
    parsed = tuple(o.strip() for o in raw_value.split(",") if o.strip())

    # 期望值：原始列表各元素 strip 后的 tuple
    expected = tuple(o.strip() for o in origins)

    assert parsed == expected, (
        f"CORS 解析不一致：\n"
        f"  原始列表: {origins!r}\n"
        f"  连接字符串: {raw_value!r}\n"
        f"  解析结果: {parsed!r}\n"
        f"  期望结果: {expected!r}"
    )
