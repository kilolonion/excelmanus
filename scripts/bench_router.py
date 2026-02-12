"""路由子代理基准测试：对比主模型 vs 小模型的路由确认耗时。

用法：
    python scripts/bench_router.py
"""

from __future__ import annotations

import asyncio
import os
import sys
import time

# 确保项目根目录在 sys.path 中
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import openai
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.environ["EXCELMANUS_API_KEY"]
BASE_URL = os.environ["EXCELMANUS_BASE_URL"]
MAIN_MODEL = os.environ["EXCELMANUS_MODEL"]
ROUTER_MODEL = os.environ.get("EXCELMANUS_ROUTER_MODEL", MAIN_MODEL)

# 模拟真实的候选 Skillpack 列表
CANDIDATE_SKILLS = [
    ("data_basic", "数据读取、筛选、排序、透视表、公式填充等基础数据分析操作"),
    ("chart_basic", "柱状图、折线图、饼图、散点图、雷达图等图表生成"),
    ("format_basic", "单元格格式化、条件格式、合并单元格、列宽行高调整"),
    ("file_ops", "文件读取、创建、复制、工作表管理等文件操作"),
    ("general_excel", "通用 Excel 操作的兜底技能包"),
]

# 测试用例：不同类型的用户请求
TEST_QUERIES = [
    "帮我读取 sales.xlsx 的数据，按月份汇总销售额",
    "画一个柱状图展示各部门的业绩对比",
    "把表头加粗，设置背景色为蓝色",
    "创建一个新的工作表，把筛选后的数据复制过去",
    "分析这个表格的数据分布情况",
]

ROUNDS = 3  # 每个查询重复次数


def _build_prompt(user_message: str) -> str:
    skill_lines = [f"- {name}: {desc}" for name, desc in CANDIDATE_SKILLS]
    return (
        "请从候选 Skillpack 中选择最合适的最多 3 个名称，"
        "仅输出 JSON 数组，例如 [\"data_basic\", \"chart_basic\"]。\n"
        "候选列表：\n"
        + "\n".join(skill_lines)
        + "\n\n用户请求："
        + user_message
    )


async def _call_once(
    client: openai.AsyncOpenAI, model: str, user_message: str
) -> tuple[float, str]:
    """单次路由调用，返回 (耗时秒, 响应内容)。"""
    prompt = _build_prompt(user_message)
    t0 = time.perf_counter()
    resp = await client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": "你是路由器，仅输出 JSON 数组，不要输出解释。"},
            {"role": "user", "content": prompt},
        ],
    )
    elapsed = time.perf_counter() - t0
    content = str(resp.choices[0].message.content or "").strip()
    return elapsed, content


async def bench_model(
    label: str, client: openai.AsyncOpenAI, model: str
) -> list[float]:
    """对一个模型跑全部测试用例，返回所有耗时。"""
    all_times: list[float] = []
    print(f"\n{'='*60}")
    print(f"  模型: {label} ({model})")
    print(f"{'='*60}")

    for query in TEST_QUERIES:
        times_for_query: list[float] = []
        for r in range(ROUNDS):
            elapsed, content = await _call_once(client, model, query)
            times_for_query.append(elapsed)
            all_times.append(elapsed)
            if r == 0:
                print(f"\n  查询: {query[:30]}...")
                print(f"    响应: {content[:80]}")
            print(f"    第{r+1}轮: {elapsed:.3f}s")
        avg = sum(times_for_query) / len(times_for_query)
        print(f"    平均: {avg:.3f}s")

    return all_times


async def main() -> None:
    print("路由子代理基准测试")
    print(f"主模型: {MAIN_MODEL}")
    print(f"路由模型: {ROUTER_MODEL}")
    print(f"测试查询数: {len(TEST_QUERIES)}, 每查询重复: {ROUNDS} 轮")

    client = openai.AsyncOpenAI(api_key=API_KEY, base_url=BASE_URL)

    # 预热：各调一次
    print("\n预热中...")
    await _call_once(client, MAIN_MODEL, "测试")
    if ROUTER_MODEL != MAIN_MODEL:
        await _call_once(client, ROUTER_MODEL, "测试")
    print("预热完成")

    main_times = await bench_model("主模型", client, MAIN_MODEL)
    if ROUTER_MODEL != MAIN_MODEL:
        router_times = await bench_model("路由模型", client, ROUTER_MODEL)
    else:
        print("\n路由模型与主模型相同，跳过对比。")
        router_times = main_times

    # 汇总
    main_avg = sum(main_times) / len(main_times)
    router_avg = sum(router_times) / len(router_times)

    print(f"\n{'='*60}")
    print(f"  汇总结果")
    print(f"{'='*60}")
    print(f"  主模型 ({MAIN_MODEL}):")
    print(f"    平均耗时: {main_avg:.3f}s")
    print(f"    最快: {min(main_times):.3f}s / 最慢: {max(main_times):.3f}s")
    print(f"  路由模型 ({ROUTER_MODEL}):")
    print(f"    平均耗时: {router_avg:.3f}s")
    print(f"    最快: {min(router_times):.3f}s / 最慢: {max(router_times):.3f}s")

    if ROUTER_MODEL != MAIN_MODEL:
        speedup = main_avg / router_avg if router_avg > 0 else float("inf")
        saved = main_avg - router_avg
        print(f"\n  加速比: {speedup:.2f}x")
        print(f"  每次路由节省: {saved:.3f}s")
        if speedup > 1:
            print(f"  ✅ 路由模型有效减少了路由判断耗时")
        else:
            print(f"  ⚠️ 路由模型未带来明显加速")


if __name__ == "__main__":
    asyncio.run(main())
