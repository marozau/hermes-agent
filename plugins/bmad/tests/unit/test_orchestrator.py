"""Tests for the orchestrator (Story 7.3, 7.7).

Covers: wave building, predicate evaluation, halt-on-failure,
resume skipping, OI enforcement, sprint-status persistence.
"""

from __future__ import annotations

import os
import textwrap
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

from plugins.bmad.lib.orchestrator import (
    FORBIDDEN_PATHS,
    FORBIDDEN_VERBS,
    OrchestrateFlags,
    OrchestrateReport,
    StoryResult,
    _check_depth_guard,
    _check_mandatory_predicates,
    _validate_cross_epic_deps,
    build_worker_goal,
    load_sprint_status,
    save_sprint_status,
    run_predicates,
    _filter_waves,
)
from plugins.bmad.lib.epic_anchor import EpicSpec, StorySpec, parse_epic_text


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def simple_epic() -> EpicSpec:
    """A simple epic with 3 stories in 2 waves."""
    return EpicSpec(
        id="7",
        name="Test Epic",
        stories=[
            StorySpec(
                id="7.1", title="Foundation",
                dependencies=[],
                success_predicates=["file_exists:lib/base.py"],
            ),
            StorySpec(
                id="7.2", title="Feature A",
                dependencies=["7.1"],
                success_predicates=["file_exists:lib/feature_a.py"],
            ),
            StorySpec(
                id="7.3", title="Feature B",
                dependencies=["7.1"],
                success_predicates=["file_exists:lib/feature_b.py"],
            ),
        ],
    )


@pytest.fixture
def epic_with_cross_dep() -> EpicSpec:
    """Epic with a cross-epic dependency (OI-8 violation)."""
    return EpicSpec(
        id="7",
        name="Test Epic",
        stories=[
            StorySpec(
                id="7.1", title="Story with external dep",
                dependencies=["6.5"],  # not in this epic!
                success_predicates=["file_exists:foo.py"],
            ),
        ],
    )


@pytest.fixture
def epic_missing_predicates() -> EpicSpec:
    """Epic with stories missing success_predicates (OI-2 violation)."""
    return EpicSpec(
        id="7",
        name="Test Epic",
        stories=[
            StorySpec(id="7.1", title="No predicates", dependencies=[]),
            StorySpec(
                id="7.2", title="Has predicates",
                dependencies=[],
                success_predicates=["file_exists:bar.py"],
            ),
        ],
    )


@pytest.fixture
def flags() -> OrchestrateFlags:
    return OrchestrateFlags()


@pytest.fixture
def project_dir(tmp_path: Path) -> Path:
    """Create a minimal BMAD project directory."""
    (tmp_path / "bmad").mkdir()
    (tmp_path / "planning-artifacts").mkdir()
    return tmp_path


# ── OI-1: Depth guard ────────────────────────────────────────────────────────


class TestDepthGuard:
    def test_depth_guard_passes_by_default(self):
        """OI-1: No depth env var → guard passes."""
        os.environ.pop("BMAD_ORCHESTRATE_DEPTH", None)
        _check_depth_guard()  # Should not raise

    def test_depth_guard_blocks_at_depth_1(self):
        """OI-1: BMAD_ORCHESTRATE_DEPTH=1 → raises RuntimeError."""
        os.environ["BMAD_ORCHESTRATE_DEPTH"] = "1"
        try:
            with pytest.raises(RuntimeError, match="OI-1"):
                _check_depth_guard()
        finally:
            os.environ.pop("BMAD_ORCHESTRATE_DEPTH", None)

    def test_depth_guard_passes_at_depth_0(self):
        """OI-1: BMAD_ORCHESTRATE_DEPTH=0 → guard passes."""
        os.environ["BMAD_ORCHESTRATE_DEPTH"] = "0"
        try:
            _check_depth_guard()  # Should not raise
        finally:
            os.environ.pop("BMAD_ORCHESTRATE_DEPTH", None)


# ── OI-2: Mandatory predicates ───────────────────────────────────────────────


class TestMandatoryPredicates:
    def test_all_stories_have_predicates(self, simple_epic):
        missing = _check_mandatory_predicates(simple_epic)
        assert missing == []

    def test_stories_without_predicates_detected(self, epic_missing_predicates):
        missing = _check_mandatory_predicates(epic_missing_predicates)
        assert "7.1" in missing
        assert "7.2" not in missing


# ── OI-8: Cross-epic deps ────────────────────────────────────────────────────


class TestCrossEpicDeps:
    def test_no_cross_deps_in_simple_epic(self, simple_epic):
        cross = _validate_cross_epic_deps(simple_epic)
        assert cross == []

    def test_cross_dep_detected(self, epic_with_cross_dep):
        cross = _validate_cross_epic_deps(epic_with_cross_dep)
        assert len(cross) == 1
        assert "6.5" in cross[0]
        assert "7.1" in cross[0]


# ── Sprint-status persistence ────────────────────────────────────────────────


class TestSprintStatus:
    def test_load_nonexistent_returns_empty(self, project_dir):
        data = load_sprint_status(project_dir)
        assert data == {}

    def test_save_and_load_round_trip(self, project_dir):
        data = {"epic_id": "7", "stories": {"7.1": {"status": "done"}}}
        save_sprint_status(project_dir, data)
        loaded = load_sprint_status(project_dir)
        assert loaded["epic_id"] == "7"
        assert loaded["stories"]["7.1"]["status"] == "done"

    def test_atomic_write_no_corruption(self, project_dir):
        """Verify .tmp file is cleaned up after successful write."""
        data = {"test": True}
        save_sprint_status(project_dir, data)
        tmp = project_dir / "sprint-status.yaml.tmp"
        assert not tmp.exists()

    def test_load_malformed_returns_empty(self, project_dir):
        (project_dir / "sprint-status.yaml").write_text("not: valid: yaml: [")
        # Should not crash
        load_sprint_status(project_dir)


# ── Wave building ─────────────────────────────────────────────────────────────


class TestWaveBuilding:
    def test_topological_waves(self, simple_epic):
        waves = simple_epic.topological_waves()
        assert len(waves) == 2
        assert waves[0] == ["7.1"]  # no deps
        assert set(waves[1]) == {"7.2", "7.3"}  # depend on 7.1

    def test_filter_by_wave(self, simple_epic, flags):
        waves = simple_epic.topological_waves()
        flags.wave_filter = 0
        filtered = _filter_waves(waves, flags)
        assert len(filtered) == 1
        assert filtered[0] == ["7.1"]

    def test_filter_by_story(self, simple_epic, flags):
        waves = simple_epic.topological_waves()
        flags.story_filter = "7.2"
        filtered = _filter_waves(waves, flags)
        assert len(filtered) == 1
        assert filtered[0] == ["7.2"]

    def test_filter_out_of_range_wave(self, simple_epic, flags):
        waves = simple_epic.topological_waves()
        flags.wave_filter = 99
        filtered = _filter_waves(waves, flags)
        assert filtered == []


# ── Predicate evaluation ─────────────────────────────────────────────────────


class TestPredicateEvaluation:
    def test_file_exists_passes(self, project_dir):
        (project_dir / "README.md").write_text("hello")
        passed, total, failures = run_predicates(
            ["file_exists:README.md"], project_dir
        )
        assert passed == 1
        assert total == 1
        assert failures == []

    def test_file_exists_fails(self, project_dir):
        passed, total, failures = run_predicates(
            ["file_exists:nonexistent.txt"], project_dir
        )
        assert passed == 0
        assert total == 1
        assert len(failures) == 1

    def test_grep_passes(self, project_dir):
        (project_dir / "config.py").write_text("DEBUG = True")
        passed, total, failures = run_predicates(
            ["grep:DEBUG:config.py"], project_dir
        )
        assert passed == 1

    def test_grep_fails(self, project_dir):
        (project_dir / "config.py").write_text("DEBUG = True")
        passed, total, failures = run_predicates(
            ["grep:PRODUCTION:config.py"], project_dir
        )
        assert passed == 0

    def test_shell_command_passes(self, project_dir):
        passed, total, failures = run_predicates(
            ["shell:true"], project_dir
        )
        assert passed == 1

    def test_shell_command_fails(self, project_dir):
        passed, total, failures = run_predicates(
            ["false"], project_dir
        )
        assert passed == 0

    def test_empty_predicates(self, project_dir):
        passed, total, failures = run_predicates([], project_dir)
        assert passed == 0
        assert total == 0

    def test_mixed_predicates(self, project_dir):
        (project_dir / "exists.txt").write_text("ok")
        passed, total, failures = run_predicates(
            ["file_exists:exists.txt", "file_exists:nope.txt", "shell:true"],
            project_dir,
        )
        assert passed == 2
        assert total == 3


# ── Worker goal construction ─────────────────────────────────────────────────


class TestWorkerGoal:
    def test_goal_includes_story_info(self, simple_epic, flags):
        story = simple_epic.stories[0]
        goal = build_worker_goal(story, simple_epic, flags)
        assert "Story 7.1" in goal
        assert "Foundation" in goal

    def test_goal_includes_forbidden_verbs(self, simple_epic, flags):
        story = simple_epic.stories[0]
        goal = build_worker_goal(story, simple_epic, flags)
        for verb in FORBIDDEN_VERBS:
            assert verb in goal

    def test_goal_includes_forbidden_paths(self, simple_epic, flags):
        story = simple_epic.stories[0]
        goal = build_worker_goal(story, simple_epic, flags)
        for path in FORBIDDEN_PATHS:
            assert path in goal

    def test_goal_includes_predicates(self, simple_epic, flags):
        story = simple_epic.stories[0]
        goal = build_worker_goal(story, simple_epic, flags)
        assert "file_exists:lib/base.py" in goal

    def test_goal_includes_oi_constraints(self, simple_epic, flags):
        story = simple_epic.stories[0]
        goal = build_worker_goal(story, simple_epic, flags)
        assert "OI-3" in goal
        assert "OI-4" in goal
        assert "OI-5" in goal


# ── StoryResult / OrchestrateReport data classes ─────────────────────────────


class TestDataClasses:
    def test_story_result_defaults(self):
        r = StoryResult(story_id="7.1", status="succeeded")
        assert r.attempts == 0
        assert r.predicates_passed == 0
        assert r.error == ""
        assert r.delegation_result == {}

    def test_orchestrate_report_defaults(self):
        r = OrchestrateReport(epic_id="7", total_stories=3, waves=[["7.1"]])
        assert r.results == {}
        assert r.halted is False
        assert r.halt_reason == ""

    def test_flags_defaults(self):
        f = OrchestrateFlags()
        assert f.resume is False
        assert f.dry_run is False
        assert f.max_retries == 2
        assert f.no_halt is False


# ── Full orchestrate with mocked delegation ───────────────────────────────────


class TestOrchestrateEpic:
    def test_dry_run_does_not_delegate(self, project_dir, simple_epic, flags):
        """Dry run should not call delegate_one."""
        # Write the epic file
        epic_path = project_dir / "epic-7.md"
        epic_path.write_text(textwrap.dedent("""\
            # Epic 7: Test

            | 7.1 | Foundation | 1h | — |
            | 7.2 | Feature A | 2h | 7.1 |
            | 7.3 | Feature B | 2h | 7.1 |

            ### 7.1 Foundation
            success_predicates:
            - file_exists:lib/base.py

            ### 7.2 Feature A
            success_predicates:
            - file_exists:lib/feature_a.py

            ### 7.3 Feature B
            success_predicates:
            - file_exists:lib/feature_b.py
        """))

        flags.dry_run = True
        flags.no_telemetry = True

        from plugins.bmad.lib.orchestrator import orchestrate_epic

        mock_ctx = MagicMock()
        report = orchestrate_epic(mock_ctx, project_dir, epic_path, flags)

        assert not report.halted
        assert len(report.results) == 3
        for result in report.results.values():
            assert result.status == "skipped"

    def test_halt_on_cross_epic_dep(self, project_dir, flags):
        """OI-8: Cross-epic deps should halt immediately."""
        epic_path = project_dir / "epic-7.md"
        epic_path.write_text(textwrap.dedent("""\
            # Epic 7: Test

            | 7.1 | Story with external dep | 1h | 6.5 |

            ### 7.1 Story with external dep
            success_predicates:
            - file_exists:foo.py
        """))

        flags.no_telemetry = True

        from plugins.bmad.lib.orchestrator import orchestrate_epic

        mock_ctx = MagicMock()
        report = orchestrate_epic(mock_ctx, project_dir, epic_path, flags)

        assert report.halted
        assert "OI-8" in report.halt_reason

    def test_halt_on_missing_predicates(self, project_dir, flags):
        """OI-2: Missing predicates should halt."""
        epic_path = project_dir / "epic-7.md"
        epic_path.write_text(textwrap.dedent("""\
            # Epic 7: Test

            | 7.1 | No preds | 1h | — |
        """))

        flags.no_telemetry = True

        from plugins.bmad.lib.orchestrator import orchestrate_epic

        mock_ctx = MagicMock()
        report = orchestrate_epic(mock_ctx, project_dir, epic_path, flags)

        assert report.halted
        assert "OI-2" in report.halt_reason

    @patch("plugins.bmad.lib.orchestrator.run_predicates")
    @patch("plugins.bmad.lib.delegation.delegate_one")
    def test_resume_skips_done_stories(self, mock_delegate, mock_predicates, project_dir, flags):
        """OI-6: Resume should skip stories with status=done."""
        # Write epic
        epic_path = project_dir / "epic-7.md"
        epic_path.write_text(textwrap.dedent("""\
            # Epic 7: Test

            | 7.1 | Done story | 1h | — |
            | 7.2 | New story | 1h | — |

            ### 7.1 Done story
            success_predicates:
            - file_exists:lib/base.py

            ### 7.2 New story
            success_predicates:
            - file_exists:lib/feat.py
        """))

        # Write sprint status marking 7.1 as done
        save_sprint_status(project_dir, {
            "epic_id": "7",
            "stories": {
                "7.1": {"status": "done", "attempts": 1},
            },
        })

        # Mock: 7.2 succeeds
        mock_delegate.return_value = {"status": "success", "task_id": "t1"}
        mock_predicates.return_value = (1, 1, [])

        flags.resume = True
        flags.no_telemetry = True

        from plugins.bmad.lib.orchestrator import orchestrate_epic

        mock_ctx = MagicMock()
        report = orchestrate_epic(mock_ctx, project_dir, epic_path, flags)

        assert not report.halted
        assert report.results["7.1"].status == "skipped"
        assert report.results["7.2"].status == "succeeded"

    def test_depth_guard_at_entry(self, project_dir, flags):
        """OI-1: Orchestrate should refuse if depth=1."""
        os.environ["BMAD_ORCHESTRATE_DEPTH"] = "1"
        try:
            epic_path = project_dir / "epic-7.md"
            epic_path.write_text("# Epic 7\n")

            from plugins.bmad.lib.orchestrator import orchestrate_epic

            mock_ctx = MagicMock()
            with pytest.raises(RuntimeError, match="OI-1"):
                orchestrate_epic(mock_ctx, project_dir, epic_path, flags)
        finally:
            os.environ.pop("BMAD_ORCHESTRATE_DEPTH", None)
