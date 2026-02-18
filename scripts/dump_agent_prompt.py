"""Dump the complete system prompts + tools that the agent receives in a conversation."""

import json
import os

os.chdir("/Users/jiangwenxuan/Desktop/excelagent")

from dotenv import load_dotenv

load_dotenv("/Users/jiangwenxuan/Desktop/excelagent/.env")

from excelmanus.config import load_config
from excelmanus.engine import AgentEngine, SkillMatchResult
from excelmanus.skillpacks.loader import SkillpackLoader
from excelmanus.skillpacks.router import SkillRouter
from excelmanus.tools import ToolRegistry

config = load_config()
registry = ToolRegistry()
registry.register_builtin_tools(config.workspace_root)

loader = SkillpackLoader(config, registry)
loader.load_all()
router = SkillRouter(config, loader)
engine = AgentEngine(config, registry, skill_router=router)

route_result = SkillMatchResult(
    skills_used=[],
    route_mode="fallback",
    system_contexts=[],
)

# ── 1. System prompts ──
prompts, err = engine._prepare_system_prompts_for_request(route_result.system_contexts)

out = "/Users/jiangwenxuan/Desktop/excelagent/outputs/agent_full_prompt_dump.md"
os.makedirs(os.path.dirname(out), exist_ok=True)
with open(out, "w", encoding="utf-8") as f:
    sep = "=" * 80

    f.write(f"# Agent 完整指令 Dump\n\n")
    f.write(f"总共 **{len(prompts)}** 条 system message\n\n")
    for i, p in enumerate(prompts):
        f.write(f"## System Prompt [{i}] ({len(p)} chars)\n\n")
        f.write("```\n")
        f.write(p)
        f.write("\n```\n\n")

    # ── 2. Tools ──
    tools = engine._build_v5_tools()
    tool_names = [t["function"]["name"] for t in tools]
    f.write(f"## Tools ({len(tools)} 个工具)\n\n")
    for name in sorted(tool_names):
        f.write(f"- `{name}`\n")

    f.write(f"\n## Tools Schema ({len(tools)} 条)\n\n")
    f.write("```json\n")
    f.write(json.dumps(tools, ensure_ascii=False, indent=2))
    f.write("\n```\n\n")

    # ── 3. Simulated messages array ──
    engine._memory.add_user_message("将城市分组总金额汇总.xlsx的工作表复制一份")
    messages = engine._memory.trim_for_request(
        system_prompts=prompts,
        max_context_tokens=config.max_context_tokens,
    )
    f.write(f"## 完整 Messages 数组 ({len(messages)} 条)\n\n")
    for m in messages:
        role = m.get("role", "?")
        content = str(m.get("content", "") or "")
        f.write(f"### [{role}]\n\n```\n{content}\n```\n\n")

print(f"已写入: {out}")
print(f"文件大小: {os.path.getsize(out)} bytes")
