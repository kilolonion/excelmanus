#!/usr/bin/env python3
"""拦截 ExcelManus agent 发送给 LLM API 的完整请求 payload。

用法：
    python scripts/dump_api_request.py "你的提问内容"

会在 outputs/ 目录下生成：
- api_request_dump.json  — 完整请求（model + messages + tools）
- system_prompt_dump.txt — 单独的 system prompt 文本
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# 确保项目根目录在 sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
os.chdir(PROJECT_ROOT)


def _serialize(obj: Any) -> Any:
    """递归序列化，确保可 JSON dump。"""
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, dict):
        return {str(k): _serialize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_serialize(item) for item in obj]
    return str(obj)


class _InterceptDone(Exception):
    """拦截完成的信号异常。"""
    pass


async def main() -> None:
    user_message = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else "你好，请介绍一下你的能力"

    from dotenv import load_dotenv
    load_dotenv(PROJECT_ROOT / ".env")

    from excelmanus.config import load_config
    from excelmanus.engine import AgentEngine
    from excelmanus.tools.registry import ToolRegistry
    from excelmanus.skillpacks.loader import SkillpackLoader
    from excelmanus.skillpacks.router import SkillRouter

    config = load_config()

    # 复制 CLI 的初始化流程
    registry = ToolRegistry()
    registry.register_builtin_tools(config.workspace_root)
    print(f"已注册 {len(registry.get_tool_names())} 个内置工具")

    loader = SkillpackLoader(config, registry)
    loader.load_all()
    router = SkillRouter(config, loader)
    print(f"已加载 {len(loader.list_skillpacks())} 个 Skillpack")

    # 持久记忆（可选）
    persistent_memory = None
    memory_extractor = None
    if config.memory_enabled:
        try:
            from excelmanus.persistent_memory import PersistentMemory
            from excelmanus.memory_extractor import MemoryExtractor
            from excelmanus.providers import create_client as _create_client

            persistent_memory = PersistentMemory(
                memory_dir=config.memory_dir,
                auto_load_lines=config.memory_auto_load_lines,
            )
            _client = _create_client(
                api_key=config.api_key,
                base_url=config.base_url,
            )
            memory_extractor = MemoryExtractor(client=_client, model=config.model)
            print("持久记忆已启用")
        except Exception as e:
            print(f"持久记忆初始化失败（跳过）: {e}")

    engine = AgentEngine(
        config,
        registry,
        skill_router=router,
        persistent_memory=persistent_memory,
        memory_extractor=memory_extractor,
    )

    # Monkey-patch: 拦截实际 API 调用
    async def intercepted_create(kwargs: dict[str, Any]) -> Any:
        output_dir = PROJECT_ROOT / "outputs"
        output_dir.mkdir(exist_ok=True)

        # 构建可序列化的 dump
        dump = {
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "user_input": user_message,
            "model": kwargs.get("model", ""),
            "message_count": len(kwargs.get("messages", [])),
            "messages": _serialize(kwargs.get("messages", [])),
        }
        if "tools" in kwargs:
            dump["tool_count"] = len(kwargs["tools"])
            dump["tool_names"] = [
                t.get("function", {}).get("name", "?") for t in kwargs["tools"]
            ]
            dump["tools"] = _serialize(kwargs["tools"])

        # 写入完整 dump
        dump_path = output_dir / "api_request_dump.json"
        with open(dump_path, "w", encoding="utf-8") as f:
            json.dump(dump, f, ensure_ascii=False, indent=2)

        # 单独写 system prompt
        system_dump_path = output_dir / "system_prompt_dump.txt"
        with open(system_dump_path, "w", encoding="utf-8") as f:
            for i, msg in enumerate(kwargs.get("messages", [])):
                if isinstance(msg, dict) and msg.get("role") == "system":
                    f.write(f"=== System Message #{i} ===\n")
                    f.write(msg.get("content", "") + "\n\n")

        print(f"\n{'='*60}")
        print(f"✅ 完整 API 请求已 dump 到: {dump_path}")
        print(f"   System prompts 已保存到: {system_dump_path}")
        print(f"{'='*60}")
        print(f"模型: {kwargs.get('model', '?')}")
        msgs = kwargs.get("messages", [])
        print(f"消息数: {len(msgs)}")
        for i, msg in enumerate(msgs):
            if isinstance(msg, dict):
                role = msg.get("role", "?")
                content = str(msg.get("content", ""))
                preview = (content[:100] + "...") if len(content) > 100 else content
                preview = preview.replace("\n", "\\n")
                print(f"  [{i}] role={role:<10} len={len(content):>6}  preview={preview}")
            else:
                print(f"  [{i}] {type(msg).__name__}")
        if "tools" in kwargs:
            print(f"工具数: {len(kwargs['tools'])}")
            names = dump.get("tool_names", [])
            for n in names:
                print(f"  - {n}")
        print(f"{'='*60}")

        raise _InterceptDone()

    engine._create_chat_completion_with_system_fallback = intercepted_create

    try:
        await engine.chat(user_message)
    except _InterceptDone:
        print("\n拦截完成，未实际调用 LLM API。请查看 outputs/ 目录。")
    except Exception as e:
        # _InterceptDone 可能被包装在其他异常中
        cause = e
        while cause is not None:
            if isinstance(cause, _InterceptDone):
                print("\n拦截完成，未实际调用 LLM API。请查看 outputs/ 目录。")
                return
            cause = getattr(cause, "__cause__", None) or getattr(cause, "__context__", None)
            if cause is e:
                break
        print(f"\n执行过程中出错: {e}")
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())
