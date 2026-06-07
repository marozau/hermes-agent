"""TDD regression tests for Round-2 lib → autodream rename findings.

Each test must FAIL before its fix is applied, then PASS after.
"""

import ast
import pytest
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.skip(reason="post-merge: run pip install -e . --no-deps in runtime venv")
class TestEditableInstallMapping:
    """Finding 1: autodream importable from runtime venv."""

    def test_autodream_importable_from_runtime_venv(self):
        result = subprocess.run(
            [sys.executable, "-c", "import autodream; print(autodream.__file__)"],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, f"autodream not importable: {result.stderr}"
        assert "autodream/__init__.py" in result.stdout, f"wrong path: {result.stdout}"


class TestMainPyLoggerOrdering:
    """Finding 2: logger referenced before definition in main.py."""

    def test_logger_defined_before_use(self):
        src = (REPO_ROOT / "hermes_cli" / "main.py").read_text()
        tree = ast.parse(src)
        logger_def_line = None
        logger_use_lines = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id == "logger":
                        logger_def_line = node.lineno
            elif isinstance(node, ast.Attribute):
                if isinstance(node.value, ast.Name) and node.value.id == "logger":
                    logger_use_lines.append(node.lineno)
        assert logger_def_line is not None, "logger definition not found"
        for use_line in logger_use_lines:
            assert use_line >= logger_def_line, (
                f"logger used at line {use_line} before defined at line {logger_def_line}"
            )


class TestBinWrappersNoLibRoot:
    """Finding 5: bin wrappers don't add stale _LIB_ROOT."""

    def test_hermes_preflight_no_sys_path(self):
        src = (REPO_ROOT / "bin" / "hermes-preflight").read_text()
        # sys.path.insert allowed only for HERMES_PYTHON_SRC_ROOT fallback
        assert "_LIB_ROOT" not in src, "hermes-preflight still computes _LIB_ROOT"

    def test_hermes_dream_no_sys_path(self):
        src = (REPO_ROOT / "bin" / "hermes-dream").read_text()
        # sys.path.insert allowed only for HERMES_PYTHON_SRC_ROOT fallback
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
        lines = txt.splitlines()
        # Only check inside the source-tree layout block
        in_tree = False
        bad = []
        for l in lines:
            if "Source tree layout" in l:
                in_tree = True
            if in_tree and "```" in l:
                break
            if not in_tree:
                continue
            # Tree drawings showing lib/ as a directory for substrate helpers
            if "lib/" in l and "autodream" not in l and ("Substrate" in l or "helpers" in l or "← Substrate" in l):
                bad.append(l)
            # Source filenames (not test filenames)
            for fname in ["hermes_providers_anthropic.py", "hermes_providers_chat.py", "hermes_memory.py", "hermes_llm.py", "hermes_dream.py"]:
                if fname in l and "test_" not in l:
                    bad.append(l)
        assert not bad, f"AGENTS.md tree still has old lib/ paths: {bad[:3]}"
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
        lines = src.splitlines(); import_idx = next((i for i, l in enumerate(lines) if "from autodream.memory import" in l), None); has_fallback = False
        if import_idx is not None:
            block = "\n".join(lines[max(0, import_idx-3):import_idx+1])
            has_fallback = "try:" in block or "sys.path" in block
        assert not has_import or has_fallback, (
            "trajectory_dedup_helper imports autodream without fallback"
        )
