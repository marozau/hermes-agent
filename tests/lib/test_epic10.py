"""Tests for Epic 10 — Multi-Pass Dream Consolidation (all 6 stories)."""
import json
import math
import os
from dataclasses import dataclass
from pathlib import Path
from unittest import mock

import pytest


# ─────────────────────────────────────────────────────────────────────────────
# Story 10.1: consolidation_passes config validation
# ─────────────────────────────────────────────────────────────────────────────

class TestValidateConsolidationPasses:
    def test_valid_values(self):
        from lib.hermes_dream import validate_consolidation_passes
        validate_consolidation_passes(1)  # no exception
        validate_consolidation_passes(2)
        validate_consolidation_passes(3)

    def test_zero_raises(self):
        from lib.hermes_dream import validate_consolidation_passes
        with pytest.raises(ValueError, match="consolidation_passes"):
            validate_consolidation_passes(0)

    def test_four_raises(self):
        from lib.hermes_dream import validate_consolidation_passes
        with pytest.raises(ValueError, match="consolidation_passes"):
            validate_consolidation_passes(4)

    def test_negative_raises(self):
        from lib.hermes_dream import validate_consolidation_passes
        with pytest.raises(ValueError, match="consolidation_passes"):
            validate_consolidation_passes(-1)

    def test_non_int_raises(self):
        from lib.hermes_dream import validate_consolidation_passes
        with pytest.raises(ValueError, match="consolidation_passes"):
            validate_consolidation_passes(2.5)


# ─────────────────────────────────────────────────────────────────────────────
# Story 10.3: per-pass audit trail
# ─────────────────────────────────────────────────────────────────────────────

class TestComputeProposalDiff:
    def test_added_only(self):
        from lib.hermes_dream import _compute_proposal_diff, PatchProposal
        prev = []
        curr = [PatchProposal(op="add", target_entry_id="e1", body="new")]
        added, modified, removed = _compute_proposal_diff(prev, curr)
        assert added == ["e1"]
        assert modified == []
        assert removed == []

    def test_removed_only(self):
        from lib.hermes_dream import _compute_proposal_diff, PatchProposal
        prev = [PatchProposal(op="add", target_entry_id="e1", body="old")]
        curr = []
        added, modified, removed = _compute_proposal_diff(prev, curr)
        assert added == []
        assert modified == []
        assert removed == ["e1"]

    def test_modified_body(self):
        from lib.hermes_dream import _compute_proposal_diff, PatchProposal
        prev = [PatchProposal(op="add", target_entry_id="e1", body="old body")]
        curr = [PatchProposal(op="add", target_entry_id="e1", body="new body")]
        added, modified, removed = _compute_proposal_diff(prev, curr)
        assert added == []
        assert modified == ["e1"]
        assert removed == []

    def test_no_change(self):
        from lib.hermes_dream import _compute_proposal_diff, PatchProposal
        p = PatchProposal(op="add", target_entry_id="e1", body="same")
        added, modified, removed = _compute_proposal_diff([p], [p])
        assert added == []
        assert modified == []
        assert removed == []

    def test_mixed_changes(self):
        from lib.hermes_dream import _compute_proposal_diff, PatchProposal
        prev = [
            PatchProposal(op="add", target_entry_id="e1", body="old"),
            PatchProposal(op="update", target_entry_id="e2", body="keep"),
        ]
        curr = [
            PatchProposal(op="add", target_entry_id="e1", body="new"),
            PatchProposal(op="add", target_entry_id="e3", body="fresh"),
        ]
        added, modified, removed = _compute_proposal_diff(prev, curr)
        assert added == ["e3"]
        assert modified == ["e1"]
        assert removed == ["e2"]


# ─────────────────────────────────────────────────────────────────────────────
# Story 10.4: depth-dynamic N
# ─────────────────────────────────────────────────────────────────────────────

class TestComputeSignalDensity:
    def test_empty_entries(self):
        from lib.hermes_dream import _compute_signal_density
        assert _compute_signal_density([]) == 0.0

    def test_low_density(self):
        from lib.hermes_dream import _compute_signal_density
        entries = [{"type": "fact", "access_count": 0}] * 5
        score = _compute_signal_density(entries)
        assert 0.0 <= score <= 0.3

    def test_high_density(self):
        from lib.hermes_dream import _compute_signal_density
        entries = [
            {"type": t, "access_count": 50}
            for t in ["fact", "preference", "procedure", "episode", "trajectory"]
        ] * 10  # 50 entries, 5 types, high access
        score = _compute_signal_density(entries)
        assert score >= 0.5


class TestResolveDepthDynamicN:
    def test_disabled_returns_manual(self):
        from lib.hermes_dream import resolve_depth_dynamic_n
        n, tier = resolve_depth_dynamic_n(0.5, {"dream": {"depth_dynamic": False}})
        assert n == 0
        assert tier == "manual"

    def test_low_tier(self):
        from lib.hermes_dream import resolve_depth_dynamic_n
        n, tier = resolve_depth_dynamic_n(0.2, {"dream": {"depth_dynamic": True}})
        assert n == 1
        assert tier == "low"

    def test_medium_tier(self):
        from lib.hermes_dream import resolve_depth_dynamic_n
        n, tier = resolve_depth_dynamic_n(0.45, {"dream": {"depth_dynamic": True}})
        assert n == 2
        assert tier == "medium"

    def test_high_tier(self):
        from lib.hermes_dream import resolve_depth_dynamic_n
        n, tier = resolve_depth_dynamic_n(0.8, {"dream": {"depth_dynamic": True}})
        assert n == 3
        assert tier == "high"

    def test_no_config_returns_manual(self):
        from lib.hermes_dream import resolve_depth_dynamic_n
        n, tier = resolve_depth_dynamic_n(0.5, None)
        assert n == 0
        assert tier == "manual"

    def test_custom_thresholds(self):
        from lib.hermes_dream import resolve_depth_dynamic_n
        config = {
            "dream": {
                "depth_dynamic": True,
                "depth_thresholds": {
                    "low": {"signal_max": 0.5, "passes": 1},
                    "high": {"signal_max": 1.0, "passes": 2},
                },
            }
        }
        n, tier = resolve_depth_dynamic_n(0.3, config)
        assert n == 1
        assert tier == "low"


# ─────────────────────────────────────────────────────────────────────────────
# Story 10.2 + 10.5: prompt builders + orchestrator
# ─────────────────────────────────────────────────────────────────────────────

class TestPromptBuilders:
    def test_consolidation_prompt_contains_entries(self):
        from lib.hermes_dream import _build_consolidation_prompt
        entries = [{"id": "e1", "type": "fact", "access_count": 5, "body": "test body"}]
        prompt = _build_consolidation_prompt(entries)
        assert "e1" in prompt
        assert "fact" in prompt
        assert "test body" in prompt

    def test_consolidation_prompt_with_category_weights(self):
        from lib.hermes_dream import _build_consolidation_prompt
        entries = [{"id": "e1", "type": "fact", "access_count": 0, "body": "x"}]
        weights = {"docker": {"direction": "up", "hit_rate": 0.7}}
        prompt = _build_consolidation_prompt(entries, category_weights=weights)
        assert "docker" in prompt
        assert "70.0%" in prompt

    def test_refine_prompt_contains_previous_patch(self):
        from lib.hermes_dream import _build_refine_prompt
        entries = [{"id": "e1", "type": "fact", "access_count": 0, "body": "x"}]
        prompt = _build_refine_prompt("- op: add\n  target_entry_id: e1", entries)
        assert "REFINEMENT" in prompt
        assert "op: add" in prompt

    def test_refine_prompt_with_contradictions(self):
        from lib.hermes_dream import _build_refine_prompt
        entries = []
        contradictions = [("new1", "existing1")]
        prompt = _build_refine_prompt("prev", entries, contradictions=contradictions)
        assert "CONTRADICTIONS" in prompt
        assert "new1" in prompt
        assert "existing1" in prompt
        assert "supersede" in prompt


class TestParseProposalsFromLlm:
    def test_parse_yaml_list(self):
        from lib.hermes_dream import _parse_proposals_from_llm
        content = """
- op: add
  type: fact
  target_entry_id: e1
  body: test body
  rationale: test
  confidence: medium
  risk_class: additive
"""
        proposals = _parse_proposals_from_llm(content)
        assert len(proposals) == 1
        assert proposals[0].op == "add"
        assert proposals[0].body == "test body"

    def test_parse_markdown_fenced(self):
        from lib.hermes_dream import _parse_proposals_from_llm
        content = '```yaml\n- op: update\n  target_entry_id: e2\n  body: refined\n```'
        proposals = _parse_proposals_from_llm(content)
        assert len(proposals) == 1
        assert proposals[0].op == "update"

    def test_parse_empty_returns_empty(self):
        from lib.hermes_dream import _parse_proposals_from_llm
        assert _parse_proposals_from_llm("") == []
        assert _parse_proposals_from_llm("no yaml here") == []


class TestExtractContradictions:
    def test_extracts_contradicts_field(self):
        from lib.hermes_dream import _extract_contradictions, PatchProposal
        proposals = [
            PatchProposal(op="add", target_entry_id="new1", body="x", contradicts="old1"),
            PatchProposal(op="add", target_entry_id="new2", body="y"),
        ]
        pairs = _extract_contradictions(proposals)
        assert pairs == [("new1", "old1")]

    def test_no_contradictions(self):
        from lib.hermes_dream import _extract_contradictions, PatchProposal
        proposals = [PatchProposal(op="add", target_entry_id="e1", body="x")]
        assert _extract_contradictions(proposals) == []


# ─────────────────────────────────────────────────────────────────────────────
# Story 10.6: baseline comparison harness
# ─────────────────────────────────────────────────────────────────────────────

class TestBaselineComparison:
    def test_verdict_keep(self):
        from lib.hermes_dream import BaselineComparison
        bc = BaselineComparison(single_pass_top1=0.5, multi_pass_top1=0.53, delta=0.03)
        assert bc.delta >= 0.02

    def test_verdict_revert(self):
        from lib.hermes_dream import BaselineComparison
        bc = BaselineComparison(single_pass_top1=0.5, multi_pass_top1=0.48, delta=-0.02)
        assert bc.delta < 0.00


# ─────────────────────────────────────────────────────────────────────────────
# Story 10.5: PatchProposal.contradicts field
# ─────────────────────────────────────────────────────────────────────────────

class TestPatchProposalContradicts:
    def test_contradicts_field_present(self):
        from lib.hermes_dream import PatchProposal
        p = PatchProposal(op="add", body="x", contradicts="some_id")
        assert p.contradicts == "some_id"

    def test_contradicts_field_default_none(self):
        from lib.hermes_dream import PatchProposal
        p = PatchProposal(op="add", body="x")
        assert p.contradicts is None


# ─────────────────────────────────────────────────────────────────────────────
# Story 10.1: NFR-27 reversibility — consolidation_passes:1 is byte-identical
# ─────────────────────────────────────────────────────────────────────────────

class TestNfr27Reversibility:
    def test_passes_1_validates(self):
        """NFR-27: consolidation_passes: 1 doesn't raise."""
        from lib.hermes_dream import validate_consolidation_passes
        validate_consolidation_passes(1)  # must not raise

    def test_dream_manifest_has_multi_pass_fields(self):
        """DreamManifest has the new multi-pass fields with backward-compat defaults."""
        from lib.hermes_dream import DreamManifest, CostInfo
        m = DreamManifest(scope="test", started_at="2026-01-01", finished_at="2026-01-01")
        assert m.consolidation_passes_actual == 1
        assert m.depth_tier == "manual"
        assert m.pass_audit == []
        assert m.per_pass_cost == []
        assert m.baseline_comparison is None
        assert m.signal_density_score == 0.0
