"""Predicates for dev-story command verification.

Each check_* function returns (passed: bool | None, reason: str).
- True: check passed
- False: check failed
- None: check deferred (Epic 13 — needs LLM judge)
"""

from __future__ import annotations

import subprocess
import re
from pathlib import Path


def tests_pass(project_dir: Path, **kwargs) -> tuple[bool | None, str]:
    """Run the test suite and check for failures."""
    try:
        result = subprocess.run(
            ["python3", "-m", "pytest", "-x", "-q", "--tb=line"],
            cwd=project_dir,
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode == 0:
            return True, "All tests pass"
        return False, f"Tests failed (exit {result.returncode})"
    except subprocess.TimeoutExpired:
        return False, "Tests timed out after 120s"
    except FileNotFoundError:
        return None, "pytest not found — deferred"


def no_regressions(project_dir: Path, **kwargs) -> tuple[bool | None, str]:
    """Run full test suite and check for no new failures."""
    try:
        result = subprocess.run(
            ["python3", "-m", "pytest", "-q", "--tb=line"],
            cwd=project_dir,
            capture_output=True,
            text=True,
            timeout=300,
        )
        if result.returncode == 0:
            return True, "No regressions"
        return False, f"Regressions detected (exit {result.returncode})"
    except subprocess.TimeoutExpired:
        return False, "Test suite timed out after 300s"
    except FileNotFoundError:
        return None, "pytest not found — deferred"


def ac_verified(project_dir: Path, **kwargs) -> tuple[bool | None, str]:
    """Check that acceptance criteria are verified.

    Epic 13 deferred — needs LLM judge to evaluate AC satisfaction.
    """
    return None, "deferred — LLM judge needed (Epic 13)"


def code_conventions(project_dir: Path, **kwargs) -> tuple[bool | None, str]:
    """Check that code follows project conventions.

    Epic 13 deferred — needs LLM judge to evaluate style.
    """
    return None, "deferred — LLM judge needed (Epic 13)"


def diff_minimal(project_dir: Path, **kwargs) -> tuple[bool | None, str]:
    """Check that the diff is minimal and focused."""
    try:
        result = subprocess.run(
            ["git", "diff", "--stat"],
            cwd=project_dir,
            capture_output=True,
            text=True,
            timeout=10,
            env={**__import__("os").environ, "LC_ALL": "C"},
        )
        if result.returncode != 0:
            return None, "Not a git repo or no diff"
        stdout = result.stdout.strip()
        if not stdout:
            return None, "no diff — clean tree"
        lines = stdout.split("\n")
        # G-7: Filter out the git summary line (anchored regex)
        summary_re = re.compile(r"^\s*\d+ files? changed")
        file_lines = [l for l in lines if not summary_re.match(l)]
        count = len(file_lines)
        if count > 20:
            return False, f"Diff touches {count} files — may be unfocused"
        return True, f"Diff touches {count} files — focused"
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None, "git not available — deferred"
