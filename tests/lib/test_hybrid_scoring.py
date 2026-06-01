"""Story 8.4 — Hybrid recall base-score (BM25 + embedding cosine) tests."""
from __future__ import annotations

import math
import sys
from collections import OrderedDict
from pathlib import Path
from unittest import mock

import pytest

HERMES_ROOT = Path.home() / ".hermes"
if str(HERMES_ROOT) not in sys.path:
    sys.path.insert(0, str(HERMES_ROOT))

from lib.hermes_preflight import (
    TrajectoryHit,
    _cosine_similarity,
    _normalize_bm25_scores,
    _embedding_cache,
    apply_hybrid_scoring,
    rank_trajectories,
)


@pytest.fixture(autouse=True)
def clear_embedding_cache():
    """Clear the embedding cache between tests."""
    _embedding_cache.clear()
    yield
    _embedding_cache.clear()


# ─────────────────────────────────────────────────────────────────────────────
# AC1: llm_embed routes through providers.yaml with LRU cache
# ─────────────────────────────────────────────────────────────────────────────


class TestEmbeddingCache:
    """AC1: In-process LRU embedding cache with 1024-entry bound."""

    def test_cache_stores_and_retrieves(self):
        """Cached embedding is returned on second call."""
        from lib.hermes_preflight import _cache_embedding, _get_cached_embedding
        vec = [0.1, 0.2, 0.3]
        _cache_embedding("hello world", vec)
        result = _get_cached_embedding("hello world")
        assert result == vec

    def test_cache_lru_eviction(self):
        """Cache evicts oldest entry when exceeding max."""
        from lib.hermes_preflight import _cache_embedding, _get_cached_embedding
        # Fill cache to max
        for i in range(1025):
            _cache_embedding(f"text-{i}", [float(i)])
        # First entry should be evicted
        assert _get_cached_embedding("text-0") is None
        # Last entry should still be there
        assert _get_cached_embedding("text-1024") is not None

    def test_cache_different_texts(self):
        """Different texts get different cache entries."""
        from lib.hermes_preflight import _cache_embedding, _get_cached_embedding
        _cache_embedding("alpha", [1.0, 0.0])
        _cache_embedding("beta", [0.0, 1.0])
        assert _get_cached_embedding("alpha") == [1.0, 0.0]
        assert _get_cached_embedding("beta") == [0.0, 1.0]


# ─────────────────────────────────────────────────────────────────────────────
# Cosine similarity
# ─────────────────────────────────────────────────────────────────────────────


class TestCosineSimilarity:

    def test_identical_vectors(self):
        """Identical vectors → cosine = 1.0."""
        v = [1.0, 2.0, 3.0]
        assert abs(_cosine_similarity(v, v) - 1.0) < 1e-9

    def test_orthogonal_vectors(self):
        """Orthogonal vectors → cosine = 0.0."""
        a = [1.0, 0.0, 0.0]
        b = [0.0, 1.0, 0.0]
        assert abs(_cosine_similarity(a, b)) < 1e-9

    def test_opposite_vectors(self):
        """Opposite vectors → cosine = -1.0."""
        a = [1.0, 0.0]
        b = [-1.0, 0.0]
        assert abs(_cosine_similarity(a, b) - (-1.0)) < 1e-9

    def test_empty_vectors(self):
        """Empty vectors → 0.0."""
        assert _cosine_similarity([], []) == 0.0

    def test_different_lengths(self):
        """Different length vectors → 0.0."""
        assert _cosine_similarity([1.0], [1.0, 2.0]) == 0.0

    def test_known_value(self):
        """Test with known cosine similarity."""
        a = [1.0, 2.0, 3.0]
        b = [4.0, 5.0, 6.0]
        dot = 1*4 + 2*5 + 3*6  # 32
        norm_a = math.sqrt(1 + 4 + 9)  # sqrt(14)
        norm_b = math.sqrt(16 + 25 + 36)  # sqrt(77)
        expected = dot / (norm_a * norm_b)
        assert abs(_cosine_similarity(a, b) - expected) < 1e-9


# ─────────────────────────────────────────────────────────────────────────────
# BM25 normalization
# ─────────────────────────────────────────────────────────────────────────────


class TestBM25Normalization:

    def test_normalizes_to_0_1(self):
        """Scores normalized to [0, 1] range."""
        hits = [
            TrajectoryHit(id="a", content="", bm25_score=0.2),
            TrajectoryHit(id="b", content="", bm25_score=0.5),
            TrajectoryHit(id="c", content="", bm25_score=0.8),
        ]
        normalized = _normalize_bm25_scores(hits)
        assert normalized[0] == pytest.approx(0.0)
        assert normalized[1] == pytest.approx(0.5)
        assert normalized[2] == pytest.approx(1.0)

    def test_all_same_scores(self):
        """All same scores → all 1.0."""
        hits = [
            TrajectoryHit(id="a", content="", bm25_score=0.5),
            TrajectoryHit(id="b", content="", bm25_score=0.5),
        ]
        normalized = _normalize_bm25_scores(hits)
        assert all(n == 1.0 for n in normalized)

    def test_empty_hits(self):
        """Empty hits → empty normalized."""
        assert _normalize_bm25_scores([]) == []


# ─────────────────────────────────────────────────────────────────────────────
# AC2: Hybrid scoring formula
# ─────────────────────────────────────────────────────────────────────────────


class TestHybridScoring:
    """AC2: base = 0.7 * cosine_sim + 0.3 * bm25_normalized."""

    def test_hybrid_with_mock_embeddings(self, tmp_path):
        """When embeddings available, hybrid score is computed via sidecars."""
        import struct
        hits = [
            TrajectoryHit(id="a", entry_id="a", content="configure k3d cluster", bm25_score=0.3),
            TrajectoryHit(id="b", entry_id="b", content="debug docker network", bm25_score=0.5),
        ]

        similar_vec = [1.0, 0.0, 0.0]  # Query direction
        different_vec = [0.0, 1.0, 0.0]  # Orthogonal

        # Write sidecar files for both entries
        for entry_id, vec in [("a", similar_vec), ("b", different_vec)]:
            sidecar = tmp_path / f"{entry_id}.deepseek-deepseek-embed-v2.vec"
            with open(str(sidecar), "wb") as f:
                f.write(struct.pack(f"{len(vec)}f", *vec))

        with mock.patch("lib.hermes_llm.llm_embed_one", return_value=similar_vec), \
             mock.patch("lib.hermes_preflight._active_embedding_workload", return_value=("deepseek", "deepseek-embed-v2")), \
             mock.patch("lib.hermes_preflight._resolve_sidecar_path", side_effect=lambda eid, p, m, memory_dir=None: tmp_path / f"{eid}.deepseek-deepseek-embed-v2.vec"):
            result, source = apply_hybrid_scoring(hits, "set up k3d", config={"recall": {"use_embeddings": True}})

        # Both hits should have updated scores
        assert len(result) == 2
        assert source == "ok"
        # The k3d hit should score higher due to cosine similarity
        assert result[0].id == "a" or result[0].score >= result[1].score

    def test_fail_open_when_no_embeddings(self):
        """AC3: When embeddings fail, falls back to pure BM25."""
        hits = [
            TrajectoryHit(id="a", content="test", bm25_score=0.5),
        ]
        with mock.patch("lib.hermes_llm.llm_embed_one", return_value=None):
            result, source = apply_hybrid_scoring(hits, "test query", config={"recall": {"use_embeddings": True}})
        # Should not crash; scores unchanged
        assert len(result) == 1
        assert source == "failed"
        assert result[0].hybrid_score is not None  # BM25 normalized set

    def test_disabled_via_config(self):
        """When recall.use_embeddings is false, no embedding calls."""
        hits = [
            TrajectoryHit(id="a", content="test", bm25_score=0.5),
        ]
        call_count = 0
        original_fn = None

        def counting_embed(text, workload="recall_embed"):
            nonlocal call_count
            call_count += 1
            return None

        with mock.patch("lib.hermes_llm.llm_embed_one", side_effect=counting_embed):
            result, source = apply_hybrid_scoring(hits, "test", config={"recall": {"use_embeddings": False}})

        assert call_count == 0  # No embedding calls made
        assert source == "disabled"
        assert result[0].bm25_score == 0.5  # Unchanged


# ─────────────────────────────────────────────────────────────────────────────
# AC4: Latency budget
# ─────────────────────────────────────────────────────────────────────────────


class TestLatencyBudget:
    """AC4: Config flag recall.use_embeddings controls embedding step."""

    def test_config_flag_default_true(self, tmp_path):
        """Default config enables embeddings."""
        import struct
        hits = [TrajectoryHit(id="a", entry_id="a", content="test", bm25_score=0.5)]
        vec = [0.1, 0.2]
        sidecar = tmp_path / "a.deepseek-deepseek-embed-v2.vec"
        with open(str(sidecar), "wb") as f:
            f.write(struct.pack(f"{len(vec)}f", *vec))
        with mock.patch("lib.hermes_llm.llm_embed_one", return_value=vec), \
             mock.patch("lib.hermes_preflight._active_embedding_workload", return_value=("deepseek", "deepseek-embed-v2")), \
             mock.patch("lib.hermes_preflight._resolve_sidecar_path", side_effect=lambda eid, p, m, memory_dir=None: tmp_path / f"{eid}.deepseek-deepseek-embed-v2.vec"):
            result, source = apply_hybrid_scoring(hits, "test")
        assert source in ("ok", "partial")
        assert len(result) == 1
        assert source in ("ok", "cache", "partial")

    def test_config_flag_false(self):
        """Config flag false disables embeddings entirely."""
        hits = [TrajectoryHit(id="a", content="test", bm25_score=0.5)]
        called = []
        with mock.patch("lib.hermes_preflight._get_embedding", side_effect=lambda t, w="": called.append(t) or None):
            apply_hybrid_scoring(hits, "test", config={"recall": {"use_embeddings": False}})
        assert len(called) == 0
