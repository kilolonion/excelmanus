"""向后兼容 shim —— 实现已迁移至 excelmanus.format_utils。"""

from excelmanus.format_utils import infer_number_format  # noqa: F401

__all__ = ["infer_number_format"]
