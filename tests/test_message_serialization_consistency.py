from __future__ import annotations

import excelmanus.engine as engine_module
from excelmanus.message_serialization import assistant_message_to_dict, to_plain


def test_engine_serializer_aliases_shared_module() -> None:
    assert engine_module._assistant_message_to_dict is assistant_message_to_dict
    assert engine_module._to_plain is to_plain
