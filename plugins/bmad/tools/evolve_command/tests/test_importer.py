"""Tests for importer.py."""

from __future__ import annotations

from pathlib import Path

import pytest

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from importer import (
    BMADTrace,
    TraceFile,
    TraceMetadata,
    build_trace,
    parse_test_results,
)


class TestBMADTrace:
    """Test the BMADTrace dataclass."""

    def test_to_files_returns_8_files(self) -> None:
        """to_files() should return exactly 8 TraceFile objects."""
        trace = BMADTrace()
        files = trace.to_files()
        assert len(files) == 8

    def test_to_files_canonical_order(self) -> None:
        """Files should be in canonical order."""
        trace = BMADTrace()
        names = [f.name for f in trace.to_files()]
        expected = [
            "story.md",
            "command_body.md",
            "project_context.yaml",
            "diff.patch",
            "test_results.txt",
            "status_update.yaml",
            "success_predicates.yaml",
            "metadata.yaml",
        ]
        assert names == expected

    def test_save_and_load_roundtrip(self, tmp_path: Path) -> None:
        """Save then load should preserve content."""
        trace = BMADTrace(
            story_md="# My Story\nAcceptance: foo",
            command_body_md="# Command Body\ndo stuff",
            project_context_yaml="repo: test\nbranch: main\n",
            diff_patch="--- a/foo\n+++ b/foo\n+new\n",
            test_results_txt="1 passed\n",
            status_update_yaml="status: done\n",
            success_predicates_yaml="predicates:\n  - foo works\n",
            metadata_yaml="trace_id: test123\n",
        )

        saved_dir = trace.save(tmp_path)
        assert saved_dir.exists()
        assert (saved_dir / "story.md").read_text() == "# My Story\nAcceptance: foo"

        loaded = BMADTrace.load(saved_dir)
        assert loaded.story_md == trace.story_md
        assert loaded.diff_patch == trace.diff_patch
        assert loaded.metadata_yaml == trace.metadata_yaml

    def test_load_missing_files(self, tmp_path: Path) -> None:
        """Loading from empty dir should return empty strings."""
        trace_dir = tmp_path / "empty_trace"
        trace_dir.mkdir()
        loaded = BMADTrace.load(trace_dir)
        assert loaded.story_md == ""
        assert loaded.diff_patch == ""


class TestBuildTrace:
    """Test the build_trace function."""

    def test_build_basic_trace(self) -> None:
        """Should produce a valid BMADTrace."""
        trace = build_trace(
            story_md="# Story",
            command_body_md="# Body",
            project_context={"repo": "test"},
            diff="--- a/foo\n+++ b/foo\n+new",
            test_results="1 passed",
            status_update={"status": "done"},
            success_predicates={"predicates": ["foo works"]},
            story_id="S-001",
        )
        assert trace.story_md == "# Story"
        assert trace.diff_patch.startswith("---")
        assert "repo: test" in trace.project_context_yaml
        assert "trace_id:" in trace.metadata_yaml
        assert "story_id: S-001" in trace.metadata_yaml

    def test_build_trace_auto_generates_id(self) -> None:
        """Should auto-generate trace_id if not provided."""
        trace = build_trace(
            story_md="s",
            command_body_md="c",
            project_context={},
            diff="d",
            test_results="t",
            status_update={},
            success_predicates={},
        )
        assert "trace_id: trace_" in trace.metadata_yaml

    def test_build_trace_iteration_and_variant(self) -> None:
        """Should include iteration and variant_id in metadata."""
        trace = build_trace(
            story_md="s",
            command_body_md="c",
            project_context={},
            diff="d",
            test_results="t",
            status_update={},
            success_predicates={},
            iteration=5,
            variant_id="v-abc",
        )
        assert "iteration: 5" in trace.metadata_yaml
        assert "variant_id: v-abc" in trace.metadata_yaml


class TestTraceFile:
    """Test the TraceFile dataclass."""

    def test_frozen(self) -> None:
        """TraceFile should be frozen."""
        tf = TraceFile(name="test.md", content="hello")
        with pytest.raises(AttributeError):
            tf.name = "other"  # type: ignore[misc]


class TestParseTestResults:
    """Test parse_test_results helper."""

    def test_pytest_output(self) -> None:
        result = parse_test_results("5 passed, 2 failed in 1.5s")
        assert result["passed"] == 5
        assert result["failed"] == 2
        assert result["total"] == 7
        assert result["pass_rate"] == pytest.approx(5 / 7)

    def test_all_pass(self) -> None:
        result = parse_test_results("10 passed in 0.5s")
        assert result["passed"] == 10
        assert result["failed"] == 0
        assert result["pass_rate"] == pytest.approx(1.0)

    def test_empty_output(self) -> None:
        result = parse_test_results("")
        assert result["passed"] == 0
        assert result["total"] == 0
        assert result["pass_rate"] == 0.0

    def test_raw_preserved(self) -> None:
        raw = "some test output\nwith multiple lines"
        result = parse_test_results(raw)
        assert result["raw"] == raw


class TestVendoredImports:
    """Test that vendored modules are importable."""

    def test_import_secret_patterns(self) -> None:
        from importer import SECRET_PATTERNS, _contains_secret

        assert SECRET_PATTERNS is not None
        assert _contains_secret("my key is sk-ant-api1234567890") is True
        assert _contains_secret("hello world") is False

    def test_import_relevance(self) -> None:
        from importer import _is_relevant_to_skill

        assert _is_relevant_to_skill("run pytest tests", "pytest", "run tests with pytest") is True
        assert _is_relevant_to_skill("hello world", "deploy", "deploy to kubernetes") is False
