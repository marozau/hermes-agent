"""Story 8.6 — LLM reranker tests."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest import mock

import pytest

HERMES_ROOT = Path.home() / ".hermes"
if str(HERMES_ROOT) not in sys.path:
    sys.path.insert(0, str(HERMES_ROOT))

from autodream.preflight import (
    TrajectoryHit,
    RERANK_PROMPT,
    _build_rerank_candidates,
    _parse_rerank_indices,
    rerank_with_llm,
)


# ─────────────────────────────────────────────────────────────────────────────
# AC3: RERANK_PROMPT is grep-able
# ─────────────────────────────────────────────────────────────────────────────


class TestRerankPrompt:

    def test_prompt_is_grepable_constant(self):
        """AC3: RERANK_PROMPT is a module-level constant, grep-able."""
        assert isinstance(RERANK_PROMPT, str)
        assert "{intent_summary}" in RERANK_PROMPT
        assert "{candidates}" in RERANK_PROMPT
        assert "JSON array" in RERANK_PROMPT

    def test_candidate_builder(self):
        """Candidates are numbered [0], [1], etc."""
        hits = [
            TrajectoryHit(id="a", content="configure k3d", bm25_score=0.5),
            TrajectoryHit(id="b", content="debug docker", bm25_score=0.4),
        ]
        result = _build_rerank_candidates(hits)
        assert "[0] configure k3d" in result
        assert "[1] debug docker" in result


# ─────────────────────────────────────────────────────────────────────────────
# AC2: Pydantic-gated RerankIndices parsing
# ─────────────────────────────────────────────────────────────────────────────


class TestParseRerankIndices:

    def test_valid_json_array(self):
        """Valid JSON array of indices → parsed correctly."""
        result = _parse_rerank_indices("[0, 2, 5]", max_idx=8)
        assert result == [0, 2, 5]

    def test_max_length_enforced(self):
        """More than 3 indices → Pydantic rejects (max_length=3)."""
        result = _parse_rerank_indices("[0, 1, 2, 3, 4]", max_idx=8)
        # Pydantic schema enforces max_length=3, so 5 elements is rejected
        assert result is None  # schema violation → None (fail-open)

    def test_out_of_range_filtered(self):
        """Indices >= max_idx are filtered out."""
        result = _parse_rerank_indices("[0, 1]", max_idx=3)
        assert result is not None
        assert all(i < 3 for i in result)

    def test_malformed_json(self):
        """Malformed JSON → None (fail-open)."""
        result = _parse_rerank_indices("not json at all", max_idx=8)
        assert result is None

    def test_json_embedded_in_text(self):
        """JSON array embedded in surrounding text → found and parsed."""
        result = _parse_rerank_indices(
            "The most relevant are [1, 0, 3] based on similarity.",
            max_idx=8,
        )
        assert result == [1, 0, 3]

    def test_empty_array(self):
        """Empty JSON array → empty list."""
        result = _parse_rerank_indices("[]", max_idx=8)
        assert result is not None
        assert len(result) == 0

    def test_negative_index_rejected(self):
        """Negative indices → Pydantic rejects."""
        result = _parse_rerank_indices("[-1, 0, 1]", max_idx=8)
        assert result is None


# ─────────────────────────────────────────────────────────────────────────────
# AC1: rerank_with_llm function
# ─────────────────────────────────────────────────────────────────────────────


def _make_mock_spec(**kwargs):
    """Create a mock LLMSpec that behaves like the real one."""
    class MockSpec:
        def __init__(self, **kw):
            for k, v in kw.items():
                setattr(self, k, v)
    return MockSpec(**kwargs)


class TestRerankWithLLM:

    def test_disabled_by_default(self):
        """Default config (use_reranker: false) → disabled, returns top-K."""
        hits = [
            TrajectoryHit(id="a", content="test1", bm25_score=0.5),
            TrajectoryHit(id="b", content="test2", bm25_score=0.4),
            TrajectoryHit(id="c", content="test3", bm25_score=0.3),
            TrajectoryHit(id="d", content="test4", bm25_score=0.2),
        ]
        result, outcome = rerank_with_llm(hits, "test query", config={"recall": {"use_reranker": False}})
        assert outcome == "disabled"
        assert len(result) == 3  # top-K=3

    def test_not_enough_candidates(self):
        """Fewer than 2 candidates → not-enough-candidates."""
        hits = [TrajectoryHit(id="a", content="test", bm25_score=0.5)]
        result, outcome = rerank_with_llm(hits, "query", config={"recall": {"use_reranker": True}})
        assert outcome == "not-enough-candidates"

    def test_llm_call_failure_fail_open(self):
        """LLM call failure → fail-open to score-based."""
        hits = [
            TrajectoryHit(id="a", content="test1", bm25_score=0.5),
            TrajectoryHit(id="b", content="test2", bm25_score=0.4),
            TrajectoryHit(id="c", content="test3", bm25_score=0.3),
        ]
        with mock.patch("autodream.llm.llm_call", side_effect=RuntimeError("provider down")):
            with mock.patch("autodream.llm.LLMSpec", side_effect=_make_mock_spec):
                result, outcome = rerank_with_llm(hits, "query", config={"recall": {"use_reranker": True}})
        assert outcome == "failed"
        assert len(result) == 3  # falls back to score-based top-3

    def test_parse_failure_fail_open(self):
        """LLM returns garbage → parse-failed, score-based fallback."""
        hits = [
            TrajectoryHit(id="a", content="test1", bm25_score=0.5),
            TrajectoryHit(id="b", content="test2", bm25_score=0.4),
            TrajectoryHit(id="c", content="test3", bm25_score=0.3),
        ]
        mock_result = {"content": "I think the answer is blueberries", "model": "test"}
        with mock.patch("autodream.llm.llm_call", return_value=mock_result):
            with mock.patch("autodream.llm.LLMSpec", side_effect=_make_mock_spec):
                result, outcome = rerank_with_llm(hits, "query", config={"recall": {"use_reranker": True}})
        assert outcome == "parse-failed"
        assert len(result) == 3  # falls back to score-based top-3

    def test_valid_rerank(self):
        """Valid reranker response → reordered hits."""
        hits = [
            TrajectoryHit(id="a", content="test1", bm25_score=0.5),
            TrajectoryHit(id="b", content="test2", bm25_score=0.4),
            TrajectoryHit(id="c", content="test3", bm25_score=0.3),
            TrajectoryHit(id="d", content="test4", bm25_score=0.2),
        ]
        # Reranker picks indices 2, 0, 1
        mock_result = {"content": "[2, 0, 1]", "model": "test"}
        with mock.patch("autodream.llm.llm_call", return_value=mock_result):
            with mock.patch("autodream.llm.LLMSpec", side_effect=_make_mock_spec):
                result, outcome = rerank_with_llm(hits, "query", config={"recall": {"use_reranker": True}})
        assert outcome == "ok"
        assert len(result) == 3
        assert result[0].id == "c"  # index 2
        assert result[1].id == "a"  # index 0
        assert result[2].id == "b"  # index 1
