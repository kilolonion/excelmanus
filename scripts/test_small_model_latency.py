"""测试窗口感知小模型调用延迟 — 直接构造 messages，不依赖复杂 domain 对象。"""

import asyncio
import json
import time
import os

from dotenv import load_dotenv
load_dotenv()

from openai import AsyncOpenAI


TASK_TYPES = (
    "DATA_COMPARISON", "FORMAT_CHECK", "FORMULA_DEBUG",
    "DATA_ENTRY", "ANOMALY_SEARCH", "GENERAL_BROWSE",
)


def _build_messages() -> list[dict[str, str]]:
    """直接构造与 build_advisor_messages 等价的消息。"""
    payload = {
        "active_window_id": "sheet_1",
        "turn_context": {
            "turn_number": 5,
            "is_new_task": False,
            "window_count_changed": False,
            "user_intent_summary": "帮我分析 Sheet1 的销售数据",
            "agent_recent_output": "正在读取 Sheet1 数据...",
            "task_type_hint": "DATA_COMPARISON",
        },
        "budget": {
            "system_budget_tokens": 4000,
            "max_windows": 4,
            "minimized_tokens": 200,
            "background_after_idle": 2,
            "suspend_after_idle": 4,
            "terminate_after_idle": 6,
        },
        "windows": [
            {"id": "sheet_1", "type": "sheet", "file_path": "/tmp/test.xlsx",
             "sheet_name": "Sheet1", "idle_turns": 0, "last_access_seq": 10,
             "summary": "销售数据表，包含 A-Z 列",
             "viewport": {"range": "A1:Z100", "rows": 100, "cols": 26}},
            {"id": "sheet_2", "type": "sheet", "file_path": "/tmp/test.xlsx",
             "sheet_name": "Sheet2", "idle_turns": 3, "last_access_seq": 7,
             "summary": "汇总表",
             "viewport": {"range": "A1:E20", "rows": 20, "cols": 5}},
            {"id": "sheet_3", "type": "sheet", "file_path": "/tmp/report.xlsx",
             "sheet_name": "报表", "idle_turns": 6, "last_access_seq": 4,
             "summary": "月度报表",
             "viewport": {"range": "A1:H50", "rows": 50, "cols": 8}},
        ],
    }
    system_prompt = (
        "你是窗口生命周期顾问。只输出 JSON 对象，不要输出解释。"
        "你必须返回字段 task_type 和 advices。"
        "task_type 只能是 " + ", ".join(TASK_TYPES) + "。"
        "advices 是数组，每项包含 window_id、tier、reason、custom_summary。"
        "tier 只能是 active/background/suspended/terminated。"
    )
    user_prompt = (
        "请根据输入窗口状态给出下一轮窗口生命周期建议。\n"
        "输出 JSON 结构示例：\n"
        '{"task_type":"GENERAL_BROWSE","advices":[{"window_id":"sheet_1","tier":"background","reason":"idle=2","custom_summary":"已完成"}]}\n'
        "输入如下：\n" + json.dumps(payload, ensure_ascii=False)
    )
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


async def main():
    api_key = os.environ.get("EXCELMANUS_ROUTER_API_KEY") or os.environ.get("EXCELMANUS_API_KEY")
    base_url = os.environ.get("EXCELMANUS_ROUTER_BASE_URL") or os.environ.get("EXCELMANUS_BASE_URL")
    model = os.environ.get("EXCELMANUS_ROUTER_MODEL") or os.environ.get("EXCELMANUS_MODEL")

    print(f"模型: {model}")
    print(f"Base URL: {base_url}")
    print(f"---")

    client = AsyncOpenAI(api_key=api_key, base_url=base_url)
    messages = _build_messages()

    # 跑 5 次取平均
    NUM_RUNS = 5
    durations = []

    for i in range(NUM_RUNS):
        t0 = time.perf_counter()
        try:
            response = await client.chat.completions.create(
                model=model,
                messages=messages,
            )
            elapsed = time.perf_counter() - t0
            durations.append(elapsed)

            content = response.choices[0].message.content if response.choices else "(empty)"
            print(f"[{i+1}/{NUM_RUNS}] {elapsed:.3f}s | 响应长度: {len(content or '')} chars")
            if i == 0:
                print(f"  首次响应预览: {(content or '')[:200]}")
        except Exception as exc:
            elapsed = time.perf_counter() - t0
            print(f"[{i+1}/{NUM_RUNS}] {elapsed:.3f}s | 错误: {exc}")

    if durations:
        avg = sum(durations) / len(durations)
        mn = min(durations)
        mx = max(durations)
        print(f"\n--- 结果 ---")
        print(f"成功: {len(durations)}/{NUM_RUNS}")
        print(f"平均: {avg:.3f}s")
        print(f"最小: {mn:.3f}s")
        print(f"最大: {mx:.3f}s")
        print(f"当前超时设置: {os.environ.get('EXCELMANUS_WINDOW_PERCEPTION_ADVISOR_TIMEOUT_MS', '800')}ms")


if __name__ == "__main__":
    asyncio.run(main())
