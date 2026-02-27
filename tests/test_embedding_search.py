"""embedding search (cosine_top_k) 单元测试。"""

from __future__ import annotations

import numpy as np
import pytest

from excelmanus.embedding.search import SearchResult, cosine_top_k


class TestCosineTopK:
    """cosine_top_k 核心逻辑测试。"""

    def test_basic_ranking(self):
        """与 query 方向一致的向量排在前面。"""
        query = np.array([1.0, 0.0, 0.0], dtype=np.float32)
        corpus = np.array([
            [1.0, 0.0, 0.0],  # 完全一致
            [0.0, 1.0, 0.0],  # 正交
            [0.7, 0.7, 0.0],  # 部分一致
        ], dtype=np.float32)
        results = cosine_top_k(query, corpus, k=3)
        assert len(results) == 3
        assert results[0].index == 0
        assert results[0].score == pytest.approx(1.0, abs=0.01)
        assert results[1].index == 2  # 部分一致排第二

    def test_threshold_filter(self):
        """低于阈值的结果被过滤。"""
        query = np.array([1.0, 0.0], dtype=np.float32)
        corpus = np.array([
            [1.0, 0.0],   # sim=1.0
            [0.0, 1.0],   # sim=0.0
            [-1.0, 0.0],  # sim=-1.0
        ], dtype=np.float32)
        results = cosine_top_k(query, corpus, k=3, threshold=0.5)
        assert len(results) == 1
        assert results[0].index == 0

    def test_k_limit(self):
        """只返回 top-k 条。"""
        query = np.array([1.0, 0.0], dtype=np.float32)
        corpus = np.array([
            [1.0, 0.0],
            [0.9, 0.1],
            [0.8, 0.2],
            [0.7, 0.3],
        ], dtype=np.float32)
        results = cosine_top_k(query, corpus, k=2)
        assert len(results) == 2

    def test_empty_corpus(self):
        query = np.array([1.0, 0.0], dtype=np.float32)
        corpus = np.empty((0, 2), dtype=np.float32)
        results = cosine_top_k(query, corpus, k=5)
        assert results == []

    def test_empty_query(self):
        query = np.array([], dtype=np.float32)
        corpus = np.array([[1.0, 0.0]], dtype=np.float32)
        results = cosine_top_k(query, corpus, k=5)
        assert results == []

    def test_zero_query_vector(self):
        """全零 query 返回空结果。"""
        query = np.zeros(3, dtype=np.float32)
        corpus = np.array([[1.0, 0.0, 0.0]], dtype=np.float32)
        results = cosine_top_k(query, corpus, k=5)
        assert results == []

    def test_descending_order(self):
        """结果严格按相似度降序排列。"""
        query = np.array([1.0, 0.0], dtype=np.float32)
        corpus = np.array([
            [0.5, 0.5],
            [1.0, 0.0],
            [0.8, 0.2],
        ], dtype=np.float32)
        results = cosine_top_k(query, corpus, k=3)
        scores = [r.score for r in results]
        assert scores == sorted(scores, reverse=True)


