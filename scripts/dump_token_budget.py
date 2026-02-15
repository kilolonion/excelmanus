"""分析 agent 单次请求的 token 预算分配。"""

import json
import os

os.chdir("/Users/jiangwenxuan/Desktop/excelagent")

from dotenv import load_dotenv

load_dotenv("/Users/jiangwenxuan/Desktop/excelagent/.env")

from excelmanus.config import load_config
from excelmanus.engine import AgentEngine, SkillMatchResult
from excelmanus.memory import TokenCounter
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
    skills_used=[], tool_scope=[], route_mode="fallback", system_contexts=[],
)

tool_scope = engine._get_current_tool_scope(route_result=route_result)
tools = engine._build_tools_for_scope(tool_scope=tool_scope)

# 逐个工具统计 token 数
total_tools_tokens = 0
tool_stats = []
for t in tools:
    name = t["function"]["name"]
    text = json.dumps(t, ensure_ascii=False)
    tokens = TokenCounter.count(text)
    total_tools_tokens += tokens
    tool_stats.append((name, tokens, len(text)))

# system prompt token
prompts, _ = engine._prepare_system_prompts_for_request(route_result.system_contexts)
system_tokens = sum(TokenCounter.count(p) for p in prompts)

# user message
user_msg = "将城市分组总金额汇总.xlsx的工作表复制一份"
user_tokens = TokenCounter.count(user_msg)

total = system_tokens + total_tools_tokens + user_tokens
print("=== Token 预算分析 ===")
print(f"max_context_tokens: {config.max_context_tokens}")
print()
print(f"System prompt:  {system_tokens:>6} tokens")
print(f"Tools schema:   {total_tools_tokens:>6} tokens ({len(tools)} 个工具)")
print(f"User message:   {user_tokens:>6} tokens")
print(f"---")
print(f"合计 input:     {total:>6} tokens")
pct = total / config.max_context_tokens * 100 if config.max_context_tokens > 0 else 0
print(f"占上下文窗口:   {pct:.1f}%")
print()
print("=== 每个工具 token 开销 (全部) ===")
tool_stats.sort(key=lambda x: -x[1])
for name, tokens, chars in tool_stats:
    print(f"  {name:45s} {tokens:>5} tokens  ({chars:>6} chars)")
print(f"  {'(合计)':45s} {total_tools_tokens:>5} tokens")
