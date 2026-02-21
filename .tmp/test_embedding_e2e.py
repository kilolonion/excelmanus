"""端到端测试：验证 Qwen Embedding 完整链路。"""

import asyncio
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path

# 加载 .env
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

import openai
import numpy as np

from excelmanus.embedding.client import EmbeddingClient
from excelmanus.embedding.store import VectorStore
from excelmanus.embedding.search import cosine_top_k
from excelmanus.embedding.semantic_memory import SemanticMemory
from excelmanus.persistent_memory import PersistentMemory
from excelmanus.memory_models import MemoryCategory, MemoryEntry


async def test_1_basic_embed():
    """测试 1：基础向量化"""
    print("=" * 60)
    print("测试 1：基础向量化（Qwen text-embedding-v3）")
    print("=" * 60)

    api_key = os.environ.get("EXCELMANUS_EMBEDDING_API_KEY")
    base_url = os.environ.get("EXCELMANUS_EMBEDDING_BASE_URL")
    model = os.environ.get("EXCELMANUS_EMBEDDING_MODEL", "text-embedding-v3")
    dims = int(os.environ.get("EXCELMANUS_EMBEDDING_DIMENSIONS", "1024"))

    print(f"  API: {base_url}")
    print(f"  Model: {model}")
    print(f"  Dimensions: {dims}")

    client = openai.AsyncOpenAI(api_key=api_key, base_url=base_url)
    ec = EmbeddingClient(client=client, model=model, dimensions=dims)

    texts = [
        "销售数据分析报表",
        "用户偏好设置为蓝色图表",
        "openpyxl 读取加密文件报错",
        "帮我创建一个柱状图",
        "上个月的财务报表在哪里",
    ]

    print(f"\n  向量化 {len(texts)} 条文本...")
    vectors = await ec.embed(texts)
    print(f"  ✅ 返回矩阵 shape: {vectors.shape}")
    assert vectors.shape == (len(texts), dims), f"shape 不匹配: {vectors.shape}"

    # 验证非零
    norms = np.linalg.norm(vectors, axis=1)
    print(f"  向量范数: {[f'{n:.4f}' for n in norms]}")
    assert all(n > 0.1 for n in norms), "存在零向量"
    print("  ✅ 所有向量非零\n")
    return ec, vectors, texts


async def test_2_cosine_search(ec, vectors, texts):
    """测试 2：语义检索"""
    print("=" * 60)
    print("测试 2：语义检索（cosine similarity）")
    print("=" * 60)

    queries = [
        ("销售报表", "应匹配'销售数据分析报表'"),
        ("图表样式", "应匹配'用户偏好设置为蓝色图表'或'帮我创建一个柱状图'"),
        ("文件读取错误", "应匹配'openpyxl 读取加密文件报错'"),
        ("财务数据", "应匹配'上个月的财务报表在哪里'或'销售数据分析报表'"),
    ]

    for query, expected_hint in queries:
        query_vec = await ec.embed_single(query)
        results = cosine_top_k(query_vec, vectors, k=3, threshold=0.0)

        print(f"\n  查询: \"{query}\" ({expected_hint})")
        for r in results:
            print(f"    [{r.score:.4f}] {texts[r.index]}")

    print("\n  ✅ 语义检索完成\n")


async def test_3_semantic_memory(ec):
    """测试 3：语义记忆完整链路"""
    print("=" * 60)
    print("测试 3：语义记忆完整链路（SemanticMemory）")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmpdir:
        pm = PersistentMemory(tmpdir, auto_load_lines=200)

        # 写入测试记忆
        entries = [
            MemoryEntry(content="销售数据.xlsx 包含日期、产品、数量、金额四列", category=MemoryCategory.FILE_PATTERN, timestamp=datetime.now()),
            MemoryEntry(content="用户希望图表使用蓝色主题", category=MemoryCategory.USER_PREF, timestamp=datetime.now()),
            MemoryEntry(content="openpyxl 无法打开受密码保护的文件，需先解密", category=MemoryCategory.ERROR_SOLUTION, timestamp=datetime.now()),
            MemoryEntry(content="月度汇总报表在 reports/ 目录下", category=MemoryCategory.FILE_PATTERN, timestamp=datetime.now()),
            MemoryEntry(content="用户习惯先看数据概览再做详细分析", category=MemoryCategory.USER_PREF, timestamp=datetime.now()),
            MemoryEntry(content="大文件超过 10MB 时建议用 run_code 读取", category=MemoryCategory.GENERAL, timestamp=datetime.now()),
        ]
        pm.save_entries(entries)
        print(f"  写入 {len(entries)} 条测试记忆")

        sm = SemanticMemory(pm, ec, top_k=3, threshold=0.2, fallback_recent=2)

        # 同步索引
        added = await sm.sync_index()
        print(f"  索引同步: 新增 {added} 条向量")

        # 语义检索
        queries = ["销售报表结构", "图表配色", "文件打不开"]
        for q in queries:
            result = await sm.search(q)
            print(f"\n  查询: \"{q}\"")
            for line in result.split("\n")[:6]:
                if line.strip():
                    print(f"    {line}")

        # search_entries
        print("\n  search_entries 测试:")
        scored = await sm.search_entries("报表文件路径")
        for entry, score in scored:
            print(f"    [{score:.4f}] [{entry.category.value}] {entry.content[:40]}")

    print("\n  ✅ 语义记忆完整链路通过\n")


async def test_4_vector_store_persistence(ec):
    """测试 4：向量存储持久化"""
    print("=" * 60)
    print("测试 4：向量存储持久化")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmpdir:
        store_dir = Path(tmpdir) / "vectors"

        # 写入
        store1 = VectorStore(store_dir, dimensions=ec.dimensions)
        texts = ["测试文本A", "测试文本B"]
        vecs = await ec.embed(texts)
        store1.add_batch(texts, vecs)
        store1.save()
        print(f"  写入 {store1.size} 条向量并持久化")

        # 重新加载
        store2 = VectorStore(store_dir, dimensions=ec.dimensions)
        print(f"  重新加载: {store2.size} 条向量")
        assert store2.size == 2

        # 验证向量一致
        diff = np.max(np.abs(store1.matrix - store2.matrix))
        print(f"  向量差异: {diff:.10f}")
        assert diff < 1e-6, f"向量不一致: diff={diff}"

    print("  ✅ 持久化验证通过\n")


async def main():
    print("\n🚀 ExcelManus Embedding 端到端测试\n")

    try:
        ec, vectors, texts = await test_1_basic_embed()
        await test_2_cosine_search(ec, vectors, texts)
        await test_3_semantic_memory(ec)
        await test_4_vector_store_persistence(ec)
        print("=" * 60)
        print("🎉 全部测试通过！")
        print("=" * 60)
    except Exception as e:
        print(f"\n❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
