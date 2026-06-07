"""TDD regression tests for Round-5 lib → autodream rename findings.

Each test must FAIL before its fix is applied, then PASS after.
"""

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


class TestCheckDeadHelpersRuns:
    """Finding 1: check_dead_helpers.py runs without NameError."""

    def test_script_runs_without_crash(self):
        result = subprocess.run(
            [sys.executable, str(REPO_ROOT / "scripts" / "check_dead_helpers.py"), "--help"],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, (
            f"check_dead_helpers.py crashed: {result.stderr}"
        )


class TestAgentsMdLibHermesPreflight:
    """Finding 2+3: AGENTS.md has no lib.hermes_preflight references."""

    def test_no_lib_hermes_preflight_in_body(self):
        txt = (REPO_ROOT / "AGENTS.md").read_text()
        assert "lib.hermes_preflight" not in txt, (
            f"AGENTS.md still references lib.hermes_preflight"
        )
