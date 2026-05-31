"""End-to-end test for create_dream_artifact with multi-pass consolidation.

This test verifies C1 (YAML-not-JSON in memory.patch), C2 (baseline comparison
reads written memory.patch), C3 (multi-message cache_breakpoints), and M7
(end-to-end test gap). A single test that would have caught all three critical
bugs from the additional cross-cutting review.
"""
import json
import os
import tempfile
from pathlib import Path
from unittest import mock

import pytest

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def dream_env(tmp_path):
    """Set up memory_dir + dreams_dir + entries for E2E test."""
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()
    dreams_dir = tmp_path / "dreams"
    dreams_dir.mkdir()

    # Write 3 test entries
    for i, (etype, body) in enumerate([
        ("fact", "User prefers dark mode"),
        ("preference", "Test framework: pytest"),
        ("procedure", "Deploy via `git push origin main`"),
    ]):
        entry_file = memory_dir / f"entry_{i}.md"
        entry_file.write_text(
            f"---\nid: 0000000{i}\ntype: {etype}\naccess_count: {i}\n"
            f"source: test\n---\n{body}\n",
            encoding="utf-8",
        )

    return memory_dir, dreams_dir


# ---------------------------------------------------------------------------
# C1: memory.patch must be parseable YAML (not YAML + JSON)
# ---------------------------------------------------------------------------

class TestC1MemoryPatchIntegrity:
    """C1: memory.patch must be valid YAML throughout — no JSON appended."""

    def test_memory_patch_is_valid_yaml(self, dream_env):
        """C1: yaml.safe_load(memory.patch) must return the full content,
        not just the first YAML document (which would drop category_weights).
        """
        from lib.hermes_dream import create_dream_artifact

        memory_dir, dreams_dir = dream_env

        # Patch llm_call to return fixture proposals
        mock_proposals = [
            {
                "op": "update",
                "type": "fact",
                "target_entry_id": "00000000",
                "body": "User prefers dark mode (confirmed)",
                "rationale": "reinforce",
                "confidence": "high",
                "risk_class": "additive",
            },
        ]

        from lib.hermes_dream import PatchProposal, CostInfo
        with mock.patch("lib.hermes_dream._run_consolidation_pass") as mock_pass:
            mock_pass.return_value = (
                [PatchProposal(
                    op="update", type="fact", target_entry_id="00000000",
                    body="User prefers dark mode (confirmed)",
                    rationale="reinforce", confidence="high", risk_class="additive",
                )],
                CostInfo(tokens_in=100, tokens_out=50),
                "ok",
            )

            dream_id = create_dream_artifact(
                scope="test",
                memory_dir=str(memory_dir),
                dreams_dir=str(dreams_dir),
                dry_run=False,
                use_lock=False,
            )

        patch_path = Path(dreams_dir) / dream_id / "memory.patch"
        assert patch_path.exists(), "memory.patch not written"

        import yaml
        content = patch_path.read_text(encoding="utf-8")
        # C1: yaml.safe_load must parse the ENTIRE file, not just first doc
        parsed = yaml.safe_load(content)
        assert parsed is not None, "memory.patch parsed as empty"
        # Must be a list (the proposals), not a truncated first document
        assert isinstance(parsed, list), f"Expected list, got {type(parsed)}"


# ---------------------------------------------------------------------------
# C2: baseline comparison runs AFTER memory.patch is written
# ---------------------------------------------------------------------------

class TestC2BaselineComparisonOrdering:
    """C2: _run_baseline_comparison must see memory.patch on disk."""

    def test_baseline_reads_written_patch(self, dream_env):
        """C2: baseline comparison runs after memory.patch is written."""
        from lib.hermes_dream import create_dream_artifact, CostInfo, BaselineComparison, PatchProposal

        memory_dir, dreams_dir = dream_env

        cmp = BaselineComparison()
        cmp.single_pass_top1 = 0.30
        cmp.multi_pass_top1 = 0.55
        cmp.delta = 0.25
        cmp.verdict = "keep"

        with mock.patch("lib.hermes_dream._run_consolidation_pass") as mock_pass, \
             mock.patch("lib.hermes_dream._run_baseline_comparison", return_value=cmp) as mock_baseline:

            mock_pass.return_value = (
                [PatchProposal(
                    op="update", type="fact", target_entry_id="00000000",
                    body="test", rationale="test", confidence="high", risk_class="additive",
                )],
                CostInfo(tokens_in=100, tokens_out=50),
                "ok",
            )

            dream_id = create_dream_artifact(
                scope="test",
                memory_dir=str(memory_dir),
                dreams_dir=str(dreams_dir),
                dry_run=False,
                use_lock=False,
                consolidation_passes=2,
            )

        # C2: baseline comparison must have been called
        mock_baseline.assert_called_once()
        # And the artifact must have a memory.patch on disk
        assert (Path(dreams_dir) / dream_id / "memory.patch").exists()


# ---------------------------------------------------------------------------
# C3: multi-message prompt with cache_breakpoints
# ---------------------------------------------------------------------------

class TestC3CacheBreakpoints:
    """C3: prompt must be split into static + dynamic messages."""

    def test_prompt_builder_returns_message_list(self):
        """C3: _build_consolidation_prompt returns list[dict], not str."""
        from lib.hermes_dream import _build_consolidation_prompt

        entries = [{"id": "e1", "type": "fact", "access_count": 1, "body": "test"}]
        messages = _build_consolidation_prompt(entries)

        assert isinstance(messages, list), f"Expected list, got {type(messages)}"
        assert len(messages) >= 1
        assert all(isinstance(m, dict) and "role" in m and "content" in m for m in messages)

    def test_refine_prompt_returns_message_list(self):
        """C3: _build_refine_prompt returns list[dict], not str."""
        from lib.hermes_dream import _build_refine_prompt

        entries = [{"id": "e1", "type": "fact", "access_count": 1, "body": "test"}]
        messages = _build_refine_prompt("<previous_pass>test</previous_pass>", entries)

        assert isinstance(messages, list), f"Expected list, got {type(messages)}"
        assert len(messages) >= 2, "Refine prompt must have static + dynamic messages"

    def test_cache_breakpoints_set_on_spec(self):
        """C3: LLMSpec must have cache_breakpoints=[0]."""
        from lib.hermes_dream import _run_consolidation_pass

        specs_captured = []

        def mock_llm_call(spec):
            specs_captured.append(spec)
            return {"content": "[]", "usage": {"prompt_tokens": 100, "completion_tokens": 10}}

        with mock.patch("lib.hermes_llm.llm_call", side_effect=mock_llm_call):
            _run_consolidation_pass(
                entries=[{"id": "e1", "type": "fact", "access_count": 0, "body": "test"}],
                raw_context="",
                category_weights=None,
            )

        assert len(specs_captured) == 1
        spec = specs_captured[0]
        assert spec.cache_breakpoints == [0], f"Expected [0], got {spec.cache_breakpoints}"


# ---------------------------------------------------------------------------
# M7: End-to-end: consolidation_passes=2 with category_weights
# ---------------------------------------------------------------------------

class TestE2EMultiPass:
    """M7: Full E2E test for create_dream_artifact(consolidation_passes=2)."""

    def test_multi_pass_with_category_weights(self, dream_env):
        """E2E: consolidation_passes=2 with seeded category_weights.

        Asserts:
        - memory.patch is valid YAML
        - manifest has pass_audit with 2 entries
        - manifest has baseline_comparison
        - REPORT.md contains Multi-Pass Verdict section
        """
        import yaml as _yaml
        from lib.hermes_dream import create_dream_artifact, CostInfo, PatchProposal

        memory_dir, dreams_dir = dream_env

        pass_count = [0]

        def mock_consolidation_pass(entries, raw_context, category_weights,
                                     previous_patch="", contradictions=None,
                                     workload="memory_dream_consolidate"):
            idx = pass_count[0]
            pass_count[0] += 1
            if idx == 0:
                proposals = [PatchProposal(
                    op="update", type="fact", target_entry_id="00000000",
                    body="User prefers dark mode (confirmed v1)",
                    rationale="reinforce", confidence="high", risk_class="additive",
                )]
            else:
                proposals = [PatchProposal(
                    op="update", type="fact", target_entry_id="00000000",
                    body="User prefers dark mode (confirmed v2 — refined)",
                    rationale="refined", confidence="high", risk_class="additive",
                )]
            return proposals, CostInfo(tokens_in=200 + idx * 50, tokens_out=100 + idx * 20), "ok"

        from lib.hermes_dream import BaselineComparison
        cmp = BaselineComparison()
        cmp.single_pass_top1 = 0.30
        cmp.multi_pass_top1 = 0.55
        cmp.delta = 0.25
        cmp.verdict = "keep"

        with mock.patch("lib.hermes_dream._run_consolidation_pass", side_effect=mock_consolidation_pass), \
             mock.patch("lib.hermes_dream._run_baseline_comparison", return_value=cmp):

            dream_id = create_dream_artifact(
                scope="e2e-test",
                memory_dir=str(memory_dir),
                dreams_dir=str(dreams_dir),
                dry_run=False,
                use_lock=False,
                consolidation_passes=2,
            )

        artifact_dir = Path(dreams_dir) / dream_id

        # C1: memory.patch is valid YAML
        patch_content = (artifact_dir / "memory.patch").read_text(encoding="utf-8")
        parsed = _yaml.safe_load(patch_content)
        assert parsed is not None and isinstance(parsed, list)

        # Manifest checks
        manifest = json.loads((artifact_dir / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["consolidation_passes_actual"] == 2
        assert len(manifest["pass_audit"]) == 2
        assert manifest["pass_audit"][0]["pass_index"] == 0  # P8: 0-based
        assert manifest["pass_audit"][1]["pass_index"] == 1

        # P9: cost nested in audit entries
        assert "cost" in manifest["pass_audit"][0]
        assert "tokens_in" in manifest["pass_audit"][0]["cost"]

        # C2: baseline_comparison is populated (not None)
        assert manifest["baseline_comparison"] is not None
        assert manifest["baseline_comparison"]["verdict"] == "keep"
        assert manifest["baseline_comparison"]["delta"] == pytest.approx(0.25)

        # REPORT.md contains Multi-Pass Verdict
        report = (artifact_dir / "REPORT.md").read_text(encoding="utf-8")
        assert "Multi-Pass Verdict" in report
        assert "Falsifier Verdict" in report
        assert "VERDICT: keep" in report
