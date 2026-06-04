"""Integration test for T-11 closure — predicate_runner wired to dev-story handler (Story 13.8).

Verifies:
- dev_story handler calls run_predicates when spec has predicate_module
- Results are written to sprint-status.yaml under predicate_results.<story_id>
- Backward-compat: handler works fine when spec has no predicate_module
- _write_predicate_results creates sprint-status.yaml if absent
- _write_predicate_results merges with existing sprint-status.yaml
"""

from __future__ import annotations

import pytest
import yaml
from pathlib import Path
from unittest.mock import patch, MagicMock

from plugins.bmad.lib.spec_schema import CommandSpec, VerificationItem


# ── Helper fixtures ─────────────────────────────────────────────────────────


@pytest.fixture
def project_dir_with_sprint_status(tmp_path):
    """Create a project dir with sprint-status.yaml already present."""
    (tmp_path / "planning-artifacts").mkdir(parents=True)
    (tmp_path / "bmad").mkdir()
    (tmp_path / "implementation-artifacts" / "stories").mkdir(parents=True)

    yaml.safe_dump({
        "project_name": "test",
        "project_type": "api",
        "project_level": 1,
        "user_name": "tester",
        "planning_artifacts": "planning-artifacts",
        "implementation_artifacts": "implementation-artifacts",
        "created": "2026-05-17",
    }, open(tmp_path / "bmad" / "config.yaml", "w"), sort_keys=False)

    yaml.safe_dump({
        "project": "test",
        "level": 1,
        "created": "2026-05-17",
        "last_updated": "2026-05-17",
        "phases": {
            "analysis": {"product-brief": "not-started"},
            "planning": {},
            "solutioning": {},
            "implementation": {"sprint-planning": "not-started"},
        },
    }, open(tmp_path / "planning-artifacts" / "workflow-status.yaml", "w"), sort_keys=False)

    # Existing sprint-status with some data
    existing = {
        "epic_id": "epic-12",
        "stories": {"7.1": {"status": "done"}},
    }
    sprint_path = tmp_path / "planning-artifacts" / "sprint-status.yaml"
    sprint_path.write_text(yaml.safe_dump(existing, sort_keys=False), encoding="utf-8")

    return tmp_path


@pytest.fixture
def project_dir_no_sprint_status(tmp_path):
    """Create a project dir without sprint-status.yaml."""
    (tmp_path / "planning-artifacts").mkdir(parents=True)
    (tmp_path / "bmad").mkdir()
    (tmp_path / "implementation-artifacts" / "stories").mkdir(parents=True)

    yaml.safe_dump({
        "project_name": "test",
        "project_type": "api",
        "project_level": 1,
        "user_name": "tester",
        "planning_artifacts": "planning-artifacts",
        "implementation_artifacts": "implementation-artifacts",
        "created": "2026-05-17",
    }, open(tmp_path / "bmad" / "config.yaml", "w"), sort_keys=False)

    yaml.safe_dump({
        "project": "test",
        "level": 1,
        "created": "2026-05-17",
        "last_updated": "2026-05-17",
        "phases": {
            "analysis": {"product-brief": "not-started"},
            "planning": {},
            "solutioning": {},
            "implementation": {"sprint-planning": "not-started"},
        },
    }, open(tmp_path / "planning-artifacts" / "workflow-status.yaml", "w"), sort_keys=False)

    return tmp_path


# ── _write_predicate_results tests ─────────────────────────────────────────


class TestWritePredicateResults:
    def test_creates_sprint_status_if_absent(self, project_dir_no_sprint_status):
        """T-11: Creates sprint-status.yaml when it doesn't exist."""
        from plugins.bmad.commands.dev_story import _write_predicate_results

        results = [
            {"description": "All tests pass", "passed": True, "reason": "All tests pass"},
            {"description": "AC verified", "passed": None, "reason": "deferred"},
        ]
        _write_predicate_results(project_dir_no_sprint_status, "13.8", results)

        path = project_dir_no_sprint_status / "planning-artifacts" / "sprint-status.yaml"
        assert path.exists()
        data = yaml.safe_load(path.read_text())
        assert "predicate_results" in data
        assert "13.8" in data["predicate_results"]
        assert data["predicate_results"]["13.8"]["all_tests_pass"]["passed"] is True
        assert data["predicate_results"]["13.8"]["ac_verified"]["passed"] is None

    def test_merges_with_existing_sprint_status(self, project_dir_with_sprint_status):
        """T-11: Existing sprint-status data is preserved."""
        from plugins.bmad.commands.dev_story import _write_predicate_results

        results = [
            {"description": "Diff is focused", "passed": True, "reason": "3 files"},
        ]
        _write_predicate_results(project_dir_with_sprint_status, "13.8", results)

        path = project_dir_with_sprint_status / "planning-artifacts" / "sprint-status.yaml"
        data = yaml.safe_load(path.read_text())

        # Original data preserved
        assert data["epic_id"] == "epic-12"
        assert data["stories"]["7.1"]["status"] == "done"

        # New predicate results added
        assert data["predicate_results"]["13.8"]["diff_is_focused"]["passed"] is True

    def test_sanitizes_description_to_key(self, project_dir_no_sprint_status):
        """T-11: Description is sanitized into a YAML-safe key."""
        from plugins.bmad.commands.dev_story import _write_predicate_results

        results = [
            {"description": "No regressions in existing test suite!", "passed": False, "reason": "2 failures"},
        ]
        _write_predicate_results(project_dir_no_sprint_status, "9.1", results)

        path = project_dir_no_sprint_status / "planning-artifacts" / "sprint-status.yaml"
        data = yaml.safe_load(path.read_text())
        key = "no_regressions_in_existing_test_suite"
        assert key in data["predicate_results"]["9.1"]
        assert data["predicate_results"]["9.1"][key]["passed"] is False


# ── _run_and_record_predicates tests ────────────────────────────────────────


class TestRunAndRecordPredicates:
    def test_calls_run_predicates(self, project_dir_no_sprint_status):
        """T-11: _run_and_record_predicates delegates to run_predicates."""
        from plugins.bmad.commands.dev_story import _run_and_record_predicates

        spec = CommandSpec(
            persona="Dev",
            phase="implementation",
            verification=(
                VerificationItem(
                    description="AC verified",
                    predicate="predicates.dev_story.ac_verified",
                ),
            ),
            predicate_module="plugins.bmad.predicates.dev_story",
        )

        _run_and_record_predicates(project_dir_no_sprint_status, spec, "13.8")

        path = project_dir_no_sprint_status / "planning-artifacts" / "sprint-status.yaml"
        data = yaml.safe_load(path.read_text())
        assert "predicate_results" in data
        assert "13.8" in data["predicate_results"]

    def test_no_predicate_module_skips(self, project_dir_no_sprint_status):
        """T-11: Backward-compat — spec without predicate_module is skipped."""
        from plugins.bmad.commands.dev_story import _run_and_record_predicates

        spec = CommandSpec(
            persona="Dev",
            phase="implementation",
            verification=(VerificationItem(description="Manual check"),),
            # predicate_module is None (default)
        )

        # Should not crash, should not create sprint-status.yaml
        _run_and_record_predicates(project_dir_no_sprint_status, spec, "13.8")

        path = project_dir_no_sprint_status / "planning-artifacts" / "sprint-status.yaml"
        assert not path.exists()

    def test_run_predicates_error_is_graceful(self, project_dir_no_sprint_status):
        """T-11: Errors in run_predicates don't crash the handler."""
        from unittest.mock import patch
        from plugins.bmad.commands.dev_story import _run_and_record_predicates

        spec = CommandSpec(
            persona="Dev",
            phase="implementation",
            verification=(
                VerificationItem(
                    description="Broken",
                    predicate="nonexistent.module.func",
                ),
            ),
            predicate_module="nonexistent",
        )

        # Mock run_predicates to raise — handler must catch and NOT propagate
        with patch(
            "plugins.bmad.lib.predicate_runner.run_predicates",
            side_effect=RuntimeError("boom"),
        ):
            _run_and_record_predicates(project_dir_no_sprint_status, spec, "13.8")

        # Verify: no sprint-status.yaml written (error was caught, not written)
        status_path = project_dir_no_sprint_status / "planning-artifacts" / "sprint-status.yaml"
        assert not status_path.exists(), "Broken predicate must not write sprint-status.yaml"


# ── predicate_runner.run_predicates integration ─────────────────────────────


class TestPredicateRunnerIntegration:
    def test_run_predicates_with_dev_story_spec(self, tmp_path):
        """T-11: Full integration — run predicates from dev-story spec."""
        from plugins.bmad.lib.predicate_runner import run_predicates
        from plugins.bmad.lib.spec_parser import parse_command_body
        from pathlib import Path as P

        # Read the actual dev-story.md spec
        md_path = P(__file__).parent.parent.parent / "commands" / "dev-story.md"
        content = md_path.read_text()
        spec, _ = parse_command_body(content)

        assert spec is not None
        assert spec.predicate_module == "plugins.bmad.predicates.dev_story"

        results = run_predicates(spec, tmp_path)  # type: ignore[arg-type]
        assert len(results) == 5  # 5 verification items in dev-story.md
        # All should return something (True, False, or None)
        for r in results:
            assert "description" in r
            assert "passed" in r
            assert "reason" in r

    def test_full_pipeline_write_to_sprint_status(self, project_dir_no_sprint_status):
        """T-11: End-to-end — run predicates and write to sprint-status.yaml."""
        from plugins.bmad.commands.dev_story import _write_predicate_results
        from plugins.bmad.lib.predicate_runner import run_predicates
        from plugins.bmad.lib.spec_parser import parse_command_body
        from pathlib import Path as P

        md_path = P(__file__).parent.parent.parent / "commands" / "dev-story.md"
        content = md_path.read_text()
        spec, _ = parse_command_body(content)

        results = run_predicates(spec, project_dir_no_sprint_status)  # type: ignore[arg-type]
        _write_predicate_results(project_dir_no_sprint_status, "13.8", results)

        path = project_dir_no_sprint_status / "planning-artifacts" / "sprint-status.yaml"
        data = yaml.safe_load(path.read_text())
        assert "predicate_results" in data
        story_results = data["predicate_results"]["13.8"]
        assert len(story_results) == 5
