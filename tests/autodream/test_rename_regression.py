"""TDD regression tests for lib → autodream rename findings.

Each test must FAIL before its fix is applied, then PASS after.
"""

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


class TestNoLibImportsRemain:
    """Finding 1: No lib.hermes_* imports in .py files."""

    def test_no_lib_imports_in_py(self):
        result = subprocess.run(
            ["grep", "-rn", r"from lib\.\|import lib\.", str(REPO_ROOT)],
            capture_output=True, text=True,
        )
        lines = [l for l in result.stdout.splitlines()
                 if ".pyc" not in l and "__pycache__" not in l and "venv/" not in l and "test_rename_regression.py" not in l]
        # Comments are allowed
        code_lines = [l for l in lines if "# " not in l or l.strip().startswith("from lib.") or l.strip().startswith("import lib.")]
        assert not code_lines, f"lib. imports remaining: {code_lines[:5]}"


class TestDocsUpdated:
    """Finding 1: AGENTS.md and CLAUDE.md reference autodream."""

    def test_agents_md_no_lib_canonical(self):
        txt = (REPO_ROOT / "AGENTS.md").read_text()
        assert "from lib.hermes_memory import" not in txt, "AGENTS.md still has lib import example"
        assert "from lib.hermes_llm import" not in txt, "AGENTS.md still has lib import example"

    def test_claude_md_no_lib_canonical(self):
        txt = (REPO_ROOT / "CLAUDE.md").read_text()
        assert "from lib.hermes_memory import" not in txt, "CLAUDE.md still has lib import example"
        assert "from lib.hermes_llm import" not in txt, "CLAUDE.md still has lib import example"


class TestCiPathFilters:
    """Finding 2: CI path filters point to autodream/."""

    def test_preflight_ci_paths(self):
        yml = (REPO_ROOT / ".github" / "workflows" / "preflight-ci.yml").read_text()
        assert "lib/hermes_preflight.py" not in yml, "CI still filters on old lib path"
        assert "tests/lib/test_hermes_preflight" not in yml, "CI still filters on old test path"
        assert "autodream/preflight.py" in yml or "tests/autodream/test_hermes_preflight" in yml, "CI missing new autodream paths"


class TestCheckDeadHelpers:
    """Finding 3: check_dead_helpers points to autodream/."""

    def test_lib_dir_not_hardcoded(self):
        src = (REPO_ROOT / "scripts" / "check_dead_helpers.py").read_text()
        assert 'repo_root / "lib"' not in src, "check_dead_helpers still hardcodes lib/"


class TestBinWrappers:
    """Finding 4: bin/ wrappers import autodream."""

    def test_hermes_preflight_wrapper(self):
        src = (REPO_ROOT / "bin" / "hermes-preflight").read_text()
        assert "lib.hermes_preflight" not in src, "hermes-preflight wrapper still imports lib"
        assert "lib.hermes_llm" not in src, "hermes-preflight wrapper still imports lib"


class TestDeployScript:
    """Finding 5: deploy.sh glob updated."""

    def test_deploy_glob(self):
        src = (REPO_ROOT / "deploy.sh").read_text()
        assert 'lib/hermes_providers*.py' not in src, "deploy.sh still globs old lib path"


class TestMainPyLogging:
    """Finding 6: main.py import failure is logged."""

    def test_main_py_logs_import_failure(self):
        src = (REPO_ROOT / "hermes_cli" / "main.py").read_text()
        # The except block should contain logger.error or logging.error
        idx = src.find("import autodream.providers")
        assert idx != -1, "main.py missing autodream.providers import"
        block = src[idx:idx+500]
        assert "logger.error" in block or "logging.error" in block or "logger.warning" in block, "main.py silently catches import failure"


class TestLogStringsUpdated:
    """Finding 7: log messages reference autodream not hermes_llm."""

    def test_log_strings_in_preflight(self):
        src = (REPO_ROOT / "autodream" / "preflight.py").read_text()
        # Look for warning messages mentioning old module names
        bad = [l for l in src.splitlines() if '"hermes_llm' in l or "'hermes_llm" in l]
        assert not bad, f"preflight.py still logs hermes_llm: {bad[:3]}"


class TestInitPyTracked:
    """Finding 9: autodream/__init__.py is tracked."""

    def test_init_py_tracked(self):
        result = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "ls-files", "autodream/__init__.py"],
            capture_output=True, text=True,
        )
        assert result.stdout.strip() == "autodream/__init__.py", "autodream/__init__.py not tracked by git"


class TestHelpersUseResolveHermesHome:
    """Finding 8: scripts helpers use resolve_hermes_home."""

    def test_preflight_verify_helper(self):
        src = (REPO_ROOT / "scripts" / "preflight_verify_helper.py").read_text()
        assert "resolve_hermes_home" in src, "preflight_verify_helper doesn't use resolve_hermes_home"

    def test_trajectory_dedup_helper(self):
        src = (REPO_ROOT / "scripts" / "trajectory_dedup_helper.py").read_text()
        assert "resolve_hermes_home" in src, "trajectory_dedup_helper doesn't use resolve_hermes_home"


class TestNoRemainingLibRefs:
    """Comprehensive: no 'lib/' references in scripts/*.py."""

    def test_scripts_no_lib_refs(self):
        for f in (REPO_ROOT / "scripts").glob("*.py"):
            src = f.read_text()
            bad = [l for l in src.splitlines() if '"lib/' in l or "'lib/" in l]
            assert not bad, f"{f.name} still references lib/: {bad[:3]}"
