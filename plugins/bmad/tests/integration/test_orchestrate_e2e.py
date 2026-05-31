"""Integration tests for the orchestrate lifecycle (Story 7.7).

Full lifecycle: parse epic → build waves → dry-run report.
"""

from __future__ import annotations

import textwrap
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from plugins.bmad.lib.epic_anchor import parse_epic_file
from plugins.bmad.lib.orchestrator import (
    OrchestrateFlags,
    orchestrate_epic,
)
from plugins.bmad.commands.orchestrate import _parse_args, _resolve_epic_path


@pytest.fixture
def epic_project(tmp_path: Path) -> Path:
    """Create a BMAD project with an epic file."""
    project = tmp_path
    (project / "bmad").mkdir()
    (project / "bmad" / "config.yaml").write_text(
        "project_name: test\nproject_type: api\nproject_level: 1\n"
    )
    (project / "planning-artifacts").mkdir()

    epic_content = textwrap.dedent("""\
        # Epic 7: Orchestration

        ## Stories

        | 7.1 | Foundation module | 2h | — |
        | 7.2 | Feature A | 3h | 7.1 |
        | 7.3 | Feature B | 2h | 7.1 |
        | 7.4 | Integration | 1h | 7.2, 7.3 |

        ### 7.1 Foundation module

        success_predicates:
        - file_exists:lib/foundation.py

        ### 7.2 Feature A

        success_predicates:
        - file_exists:lib/feature_a.py
        - file_exists:tests/test_feature_a.py

        ### 7.3 Feature B

        success_predicates:
        - file_exists:lib/feature_b.py

        ### 7.4 Integration

        success_predicates:
        - file_exists:lib/integration.py
    """)

    epic_path = project / "planning-artifacts" / "epic-7.md"
    epic_path.write_text(epic_content)

    return project


# ── Epic parsing ──────────────────────────────────────────────────────────────


class TestEpicParsing:
    def test_parse_epic_file(self, epic_project):
        epic_path = epic_project / "planning-artifacts" / "epic-7.md"
        epic = parse_epic_file(epic_path)
        assert epic.id == "7"
        assert len(epic.stories) == 4

    def test_parse_dependencies(self, epic_project):
        epic_path = epic_project / "planning-artifacts" / "epic-7.md"
        epic = parse_epic_file(epic_path)
        s1 = epic.story_by_id("7.1")
        assert s1 is not None
        assert s1.dependencies == []

        s4 = epic.story_by_id("7.4")
        assert s4 is not None
        assert "7.2" in s4.dependencies
        assert "7.3" in s4.dependencies

    def test_parse_predicates(self, epic_project):
        epic_path = epic_project / "planning-artifacts" / "epic-7.md"
        epic = parse_epic_file(epic_path)
        s2 = epic.story_by_id("7.2")
        assert s2 is not None
        assert len(s2.success_predicates) == 2

    def test_topological_waves(self, epic_project):
        epic_path = epic_project / "planning-artifacts" / "epic-7.md"
        epic = parse_epic_file(epic_path)
        waves = epic.topological_waves()
        assert len(waves) == 3
        assert waves[0] == ["7.1"]
        assert set(waves[1]) == {"7.2", "7.3"}
        assert waves[2] == ["7.4"]


# ── CLI argument parsing ─────────────────────────────────────────────────────


class TestArgParsing:
    def test_parse_epic_number(self):
        parsed = _parse_args("7")
        assert parsed["epic"] == "7"

    def test_parse_flags(self):
        parsed = _parse_args("7 --resume --dry-run --story 7.2 --wave 1")
        assert parsed["epic"] == "7"
        assert parsed["resume"] is True
        assert parsed["dry_run"] is True
        assert parsed["story"] == "7.2"
        assert parsed["wave"] == 1

    def test_parse_max_retries(self):
        parsed = _parse_args("7 --max-retries 5")
        assert parsed["max_retries"] == 5

    def test_parse_no_halt(self):
        parsed = _parse_args("7 --no-halt")
        assert parsed["no_halt"] is True

    def test_parse_prefect(self):
        parsed = _parse_args("7 --prefect")
        assert parsed["prefect"] is True

    def test_parse_path(self):
        parsed = _parse_args("planning-artifacts/epic-7.md")
        assert parsed["epic"] == "planning-artifacts/epic-7.md"


# ── Epic path resolution ─────────────────────────────────────────────────────


class TestEpicPathResolution:
    def test_resolve_by_number(self, epic_project):
        path = _resolve_epic_path(epic_project, "7")
        assert path is not None
        assert path.exists()
        assert "epic-7" in path.name

    def test_resolve_by_path(self, epic_project):
        path = _resolve_epic_path(
            epic_project, "planning-artifacts/epic-7.md"
        )
        assert path is not None
        assert path.exists()

    def test_resolve_nonexistent(self, epic_project):
        path = _resolve_epic_path(epic_project, "99")
        assert path is None

    def test_resolve_empty(self, epic_project):
        path = _resolve_epic_path(epic_project, "")
        assert path is None


# ── Full orchestrate dry-run ──────────────────────────────────────────────────


class TestOrchestrateDryRun:
    def test_full_dry_run(self, epic_project):
        epic_path = epic_project / "planning-artifacts" / "epic-7.md"
        flags = OrchestrateFlags(dry_run=True, no_telemetry=True)
        ctx = MagicMock()

        report = orchestrate_epic(ctx, epic_project, epic_path, flags)

        assert not report.halted
        assert report.total_stories == 4
        assert len(report.results) == 4
        assert len(report.waves) == 3

        # All stories should be skipped in dry-run
        for result in report.results.values():
            assert result.status == "skipped"

    def test_dry_run_with_wave_filter(self, epic_project):
        epic_path = epic_project / "planning-artifacts" / "epic-7.md"
        flags = OrchestrateFlags(
            dry_run=True, wave_filter=0, no_telemetry=True
        )
        ctx = MagicMock()

        report = orchestrate_epic(ctx, epic_project, epic_path, flags)

        # Only wave 0 (story 7.1) should be present
        assert "7.1" in report.results
        assert "7.2" not in report.results

    def test_dry_run_with_story_filter(self, epic_project):
        epic_path = epic_project / "planning-artifacts" / "epic-7.md"
        flags = OrchestrateFlags(
            dry_run=True, story_filter="7.3", no_telemetry=True
        )
        ctx = MagicMock()

        report = orchestrate_epic(ctx, epic_project, epic_path, flags)

        assert "7.3" in report.results
        # Other stories should not be present
        assert "7.1" not in report.results
