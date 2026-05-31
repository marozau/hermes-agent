"""Tests for the Prefect bridge (Story 7.10)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from plugins.bmad.lib.epic_anchor import EpicSpec, StorySpec
from plugins.bmad.lib.orchestrator import OrchestrateReport, StoryResult
from plugins.bmad.lib.prefect_bridge import export_prefect_flow, launch_prefect


@pytest.fixture
def epic() -> EpicSpec:
    return EpicSpec(
        id="7",
        name="Test Epic",
        stories=[
            StorySpec(
                id="7.1",
                title="Foundation",
                dependencies=[],
                success_predicates=["file_exists:base.py"],
            ),
            StorySpec(
                id="7.2",
                title="Feature",
                dependencies=["7.1"],
                success_predicates=["file_exists:feature.py"],
            ),
        ],
        source_path="/tmp/epic-7.md",
    )


@pytest.fixture
def report() -> OrchestrateReport:
    return OrchestrateReport(
        epic_id="7",
        total_stories=2,
        waves=[["7.1"], ["7.2"]],
        results={
            "7.1": StoryResult(story_id="7.1", status="succeeded", attempts=1),
            "7.2": StoryResult(story_id="7.2", status="succeeded", attempts=1),
        },
    )


# ── Export ────────────────────────────────────────────────────────────────────


class TestExportPrefectFlow:
    def test_creates_output_file(self, epic, report, tmp_path):
        output = tmp_path / "flows" / "epic-7-flow.py"
        result = export_prefect_flow(epic, report, output)
        assert result == output
        assert output.exists()

    def test_flow_contains_epic_info(self, epic, report, tmp_path):
        output = tmp_path / "flow.py"
        export_prefect_flow(epic, report, output)
        content = output.read_text()
        assert "Epic 7" in content
        assert "Test Epic" in content

    def test_flow_contains_tasks(self, epic, report, tmp_path):
        output = tmp_path / "flow.py"
        export_prefect_flow(epic, report, output)
        content = output.read_text()
        assert "story_7_1" in content
        assert "story_7_2" in content

    def test_flow_has_prefect_imports(self, epic, report, tmp_path):
        output = tmp_path / "flow.py"
        export_prefect_flow(epic, report, output)
        content = output.read_text()
        assert "from prefect import flow, task" in content
        assert "@flow" in content
        assert "@task" in content

    def test_flow_has_dependency_wiring(self, epic, report, tmp_path):
        output = tmp_path / "flow.py"
        export_prefect_flow(epic, report, output)
        content = output.read_text()
        # 7.2 depends on 7.1
        assert "wait_for" in content

    def test_creates_parent_directories(self, epic, report, tmp_path):
        output = tmp_path / "deep" / "nested" / "flow.py"
        export_prefect_flow(epic, report, output)
        assert output.exists()

    def test_flow_contains_wave_comments(self, epic, report, tmp_path):
        output = tmp_path / "flow.py"
        export_prefect_flow(epic, report, output)
        content = output.read_text()
        assert "Wave 0" in content
        assert "Wave 1" in content


# ── Launch ────────────────────────────────────────────────────────────────────


class TestLaunchPrefect:
    def test_missing_file_returns_error(self, tmp_path):
        result = launch_prefect(tmp_path / "nonexistent.py")
        assert result["status"] == "error"
        assert "not found" in result["error"]

    @patch("plugins.bmad.lib.prefect_bridge.subprocess.run")
    def test_successful_run(self, mock_run, tmp_path):
        flow = tmp_path / "flow.py"
        flow.write_text("# flow")
        mock_run.return_value = type("R", (), {
            "returncode": 0, "stdout": "ok", "stderr": ""
        })()
        result = launch_prefect(flow)
        assert result["status"] == "success"

    @patch("plugins.bmad.lib.prefect_bridge.subprocess.run")
    def test_failed_run(self, mock_run, tmp_path):
        flow = tmp_path / "flow.py"
        flow.write_text("# flow")
        mock_run.return_value = type("R", (), {
            "returncode": 1, "stdout": "", "stderr": "error"
        })()
        result = launch_prefect(flow)
        assert result["status"] == "error"

    @patch("plugins.bmad.lib.prefect_bridge.subprocess.run")
    def test_prefect_not_found(self, mock_run, tmp_path):
        flow = tmp_path / "flow.py"
        flow.write_text("# flow")
        mock_run.side_effect = FileNotFoundError
        result = launch_prefect(flow)
        assert result["status"] == "error"
        assert "not found" in result["error"]
