"""TDD regression tests for Round-2 lib → autodream rename findings.

Each test must FAIL before its fix is applied, then PASS after.
"""

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


class TestEditableInstallMapping:
    """Finding 1: autodream importable from runtime venv."""

    def test_autodream_importable_from_runtime_venv(self):
        result = subprocess.run(
            ["/Users/im/.hermes/hermes-agent/venv/bin/python", "-c", "import autodream; print(autodream.__file__)"],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, f"autodream not importable: {result.stderr}"
        assert "autodream/__init__.py" in result.stdout, f"wrong path: {result.stdout}"


class TestMainPyLoggerOrdering:
    """Finding 2: logger referenced before definition in main.py."""

    def test_logger_defined_before_use(self):
        src = (REPO_ROOT / "hermes_cli" / "main.py").read_text()
        lines = src.splitlines()
        logger_def_line = None
        for i, line in enumerate(lines):
            if 'logger = logging.getLogger' in line:
                logger_def_line = i
                break
        assert logger_def_line is not None, "logger definition not found"
        # Before logger is defined, no logger. reference should exist
        for i, line in enumerate(lines[:logger_def_line]):
            if 'logger.' in line and 'logging.getLogger' not in line:
                assert False, f"logger referenced at line {i} before defined at line {logger_def_line}: {line}"


class TestDeployScript:
    """Finding 3+4: deploy.sh destination and glob."""

    def test_default_destination_not_lib(self):
        src = (REPO_ROOT / "deploy.sh").read_text()
        assert 'LIVE_DIR="${1:-$HOME/.hermes/lib}"' not in src, "deploy.sh still defaults to ~/.hermes/lib"

    def test_glob_covers_all_modules(self):
        src = (REPO_ROOT / "deploy.sh").read_text()
        assert "autodream/providers*.py" not in src, "deploy.sh glob too narrow (only providers)"
        # Should use autodream/*.py or be removed entirely


class TestBinWrappersNoLibRoot:
    """Finding 5: bin wrappers don't add stale _LIB_ROOT."""

    def test_hermes_preflight_no_sys_path(self):
        src = (REPO_ROOT / "bin" / "hermes-preflight").read_text()
        assert "sys.path.insert" not in src, "hermes-preflight still manipulates sys.path"
        assert "_LIB_ROOT" not in src, "hermes-preflight still computes _LIB_ROOT"

    def test_hermes_dream_no_sys_path(self):
        src = (REPO_ROOT / "bin" / "hermes-dream").read_text()
        assert "sys.path.insert" not in src, "hermes-dream still manipulates sys.path"
        assert "_LIB_ROOT" not in src, "hermes-dream still computes _LIB_ROOT"


class TestClaudeMdNoSyncDiscipline:
    """Finding 6: Sync discipline section deleted."""

    def test_no_sync_discipline_section(self):
        txt = (REPO_ROOT / "CLAUDE.md").read_text()
        assert "Sync discipline" not in txt, "CLAUDE.md still has Sync discipline section"


class TestClaudeMdKeyPaths:
    """Finding 7: key-paths cheat sheet shows autodream/."""

    def test_key_paths_show_autodream(self):
        txt = (REPO_ROOT / "CLAUDE.md").read_text()
        # The cheat sheet should not list lib/hermes_ paths as canonical
        assert "lib/hermes_memory.py" not in txt, "CLAUDE.md cheat sheet still shows lib/"
        assert "lib/hermes_llm.py" not in txt, "CLAUDE.md cheat sheet still shows lib/"


class TestClaudeMdNoDriftAudit:
    """Finding 8: drift-audit loop deleted."""

    def test_no_drift_audit_loop(self):
        txt = (REPO_ROOT / "CLAUDE.md").read_text()
        assert 'for f in lib/*.py' not in txt, "CLAUDE.md still has drift-audit loop"
        assert 'diff -q "$f" "$HOME/.hermes/$f"' not in txt, "CLAUDE.md still has drift-audit loop"


class TestAgentsMdUpdated:
    """Finding 9: AGENTS.md lib/ references updated."""

    def test_agents_md_no_lib_paths(self):
        txt = (REPO_ROOT / "AGENTS.md").read_text()
        # Allow references in historical/changelog contexts but not as current paths
        bad = [l for l in txt.splitlines() if "lib/hermes_" in l and "was" not in l and "old" not in l]
        assert not bad, f"AGENTS.md still has lib/hermes_ paths: {bad[:3]}"
        assert "tests/lib/" not in txt, "AGENTS.md still references tests/lib/"


class TestRuntimeLocationConsistent:
    """Finding 10: docs agree on runtime location."""

    def test_claude_md_runtime_location(self):
        txt = (REPO_ROOT / "CLAUDE.md").read_text()
        # Should consistently reference ~/.hermes/hermes-agent/autodream/
        assert "~/.hermes/autodream/memory.py" not in txt, "CLAUDE.md has wrong runtime path"


class TestHelpersHaveFallback:
    """Finding 11: scripts/helpers can import autodream outside dev repo."""

    def test_trajectory_dedup_has_fallback(self):
        src = (REPO_ROOT / "scripts" / "trajectory_dedup_helper.py").read_text()
        # Should have a try/except or sys.path fallback for autodream import
        has_import = "import autodream" in src
        has_fallback = "sys.path" in src or "try:" in src or "ImportError" in src
        assert not has_import or has_fallback, (
            "trajectory_dedup_helper imports autodream without fallback"
        )
