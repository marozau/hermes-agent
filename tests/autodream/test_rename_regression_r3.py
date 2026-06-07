"""TDD regression tests for Round-3 lib → autodream rename findings.

Each test must FAIL before its fix is applied, then PASS after.
"""

import ast
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


class TestClaudeMdTelemetryHeading:
    """Finding 1: CLAUDE.md ## Telemetry expectations heading restored."""

    def test_telemetry_heading_exists(self):
        txt = (REPO_ROOT / "CLAUDE.md").read_text()
        assert "## Telemetry expectations" in txt, "CLAUDE.md missing ## Telemetry expectations heading"

    def test_telemetry_intro_line(self):
        txt = (REPO_ROOT / "CLAUDE.md").read_text()
        assert "Every implementation must emit:" in txt, "CLAUDE.md missing telemetry intro line"


class TestAgentsMdTreeDrawings:
    """Finding 2: AGENTS.md ASCII tree shows autodream/ not lib/."""

    def test_tree_shows_autodream_not_lib(self):
        txt = (REPO_ROOT / "AGENTS.md").read_text()
        # Find the source-tree layout block
        in_tree = False
        for i, line in enumerate(txt.splitlines()):
            if "Source tree layout" in line:
                in_tree = True
            if in_tree and "lib/" in line and "# " in line and "Substrate" in line:
                assert False, f"AGENTS.md tree still shows lib/ heading at line {i}: {line}"
            if in_tree and "hermes_memory.py" in line:
                assert False, f"AGENTS.md tree still shows hermes_memory.py at line {i}: {line}"
            if in_tree and "hermes_providers_anthropic.py" in line:
                assert False, f"AGENTS.md tree still shows hermes_providers_anthropic.py at line {i}: {line}"
            # Exit tree after tests/ block
            if in_tree and "tests/autodream/" in line:
                break


class TestEditableInstallPortable:
    """Finding 3+5: Test doesn't hardcode venv path."""

    def test_no_hardcoded_venv_path(self):
        src = (REPO_ROOT / "tests" / "autodream" / "test_rename_regression_r2.py").read_text()
        assert "/Users/im/.hermes/hermes-agent/venv/bin/python" not in src, (
            "test hardcodes venv path"
        )


class TestBinWrappersFallback:
    """Finding 4: bin wrappers have HERMES_PYTHON_SRC_ROOT fallback."""

    def test_hermes_preflight_fallback(self):
        src = (REPO_ROOT / "bin" / "hermes-preflight").read_text()
        assert "HERMES_PYTHON_SRC_ROOT" in src, "hermes-preflight missing HERMES_PYTHON_SRC_ROOT fallback"

    def test_hermes_dream_fallback(self):
        src = (REPO_ROOT / "bin" / "hermes-dream").read_text()
        assert "HERMES_PYTHON_SRC_ROOT" in src, "hermes-dream missing HERMES_PYTHON_SRC_ROOT fallback"


class TestLoggerOvermatch:
    """Finding 6: test_logger_defined_before_use doesn't over-match."""

    def test_uses_ast_not_substring(self):
        src = (REPO_ROOT / "tests" / "autodream" / "test_rename_regression_r2.py").read_text()
        # Should use AST, not substring matching
        assert "'logger.' in line" not in src, "test uses fragile substring match for logger."


class TestAgentsTestNarrow:
    """Finding 7: test_agents_md catches tree drawings."""

    def test_checks_for_hermes_providers_filenames(self):
        src = (REPO_ROOT / "tests" / "autodream" / "test_rename_regression_r2.py").read_text()
        assert "hermes_providers" in src, "test doesn't check for hermes_providers filenames"


class TestDedupFallbackNarrow:
    """Finding 8: trajectory_dedup fallback is narrowly scoped."""

    def test_import_guarded_or_prefixed(self):
        src = (REPO_ROOT / "scripts" / "trajectory_dedup_helper.py").read_text()
        lines = src.splitlines()
        import_idx = None
        for i, line in enumerate(lines):
            if "from autodream.memory import" in line or "import autodream.memory" in line:
                import_idx = i
                break
        if import_idx is None:
            pytest.skip("no autodream import found")
        # The import should be inside a try/except or preceded by sys.path setup
        block = "\n".join(lines[max(0, import_idx-5):import_idx+1])
        assert "try:" in block or "sys.path" in block, (
            "autodream import is not guarded by try/except or sys.path"
        )


class TestDeployScriptDeleted:
    """Finding 9: deploy.sh deleted."""

    def test_deploy_sh_absent(self):
        assert not (REPO_ROOT / "deploy.sh").exists(), "deploy.sh should be deleted"
