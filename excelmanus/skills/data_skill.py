"""兼容层：data skill 已迁移到 excelmanus.tools.data_tools。"""

from excelmanus.tools.data_tools import (
    SKILL_DESCRIPTION,
    SKILL_NAME,
    analyze_data,
    filter_data,
    get_tools,
    init_guard,
    read_excel,
    transform_data,
    write_excel,
)

__all__ = [
    "SKILL_DESCRIPTION",
    "SKILL_NAME",
    "analyze_data",
    "filter_data",
    "get_tools",
    "init_guard",
    "read_excel",
    "transform_data",
    "write_excel",
]
