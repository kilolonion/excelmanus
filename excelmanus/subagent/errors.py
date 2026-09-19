"""子代理 fail-loud 错误。发布前失败走这里，不发 start、不返回 id。"""

from __future__ import annotations


class SubagentError(Exception):
    """具名能力/校验失败。code 供调用方映射，禁止接受后忽略。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
