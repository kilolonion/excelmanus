"""语义检索：cosine similarity 计算与 top-k 排序。"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class SearchResult:
    """单条检索结果。"""

    index: int
    score: float


def cosine_top_k(
    query_vec: np.ndarray,
    corpus_vecs: np.ndarray,
    k: int = 5,
    threshold: float = 0.0,
) -> list[SearchResult]:
    """计算 query 向量与语料库向量的 cosine similarity，返回 top-k 结果。

    Args:
        query_vec: (D,) 查询向量。
        corpus_vecs: (N, D) 语料库向量矩阵。
        k: 返回最多 k 条结果。
        threshold: 最低相似度阈值，低于此值的结果被过滤。

    Returns:
        按相似度降序排列的 SearchResult 列表。
    """
    if corpus_vecs.shape[0] == 0 or query_vec.shape[0] == 0:
        return []

    # 归一化
    query_norm = np.linalg.norm(query_vec)
    if query_norm < 1e-9:
        return []

    corpus_norms = np.linalg.norm(corpus_vecs, axis=1)
    # 避免除零
    safe_norms = np.where(corpus_norms < 1e-9, 1.0, corpus_norms)

    similarities = (corpus_vecs @ query_vec) / (safe_norms * query_norm)

    # 过滤低于阈值的结果
    valid_mask = similarities >= threshold
    if not np.any(valid_mask):
        return []

    # 获取 top-k 索引
    valid_indices = np.where(valid_mask)[0]
    valid_sims = similarities[valid_indices]

    if len(valid_indices) <= k:
        sorted_order = np.argsort(valid_sims)[::-1]
    else:
        # 部分排序，只取 top-k
        top_k_order = np.argpartition(valid_sims, -k)[-k:]
        sorted_order = top_k_order[np.argsort(valid_sims[top_k_order])[::-1]]

    results: list[SearchResult] = []
    for pos in sorted_order:
        idx = int(valid_indices[pos])
        score = float(similarities[idx])
        results.append(SearchResult(index=idx, score=score))

    return results


async def semantic_search(
    query_vec: np.ndarray,
    corpus_vecs: np.ndarray,
    k: int = 5,
    threshold: float = 0.0,
) -> list[SearchResult]:
    """异步语义检索（实际计算是 CPU 操作，封装为 async 以统一接口）。"""
    return cosine_top_k(query_vec, corpus_vecs, k=k, threshold=threshold)
