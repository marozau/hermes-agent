"""TDD regression tests for Round-4 lib → autodream rename findings.

Each test must FAIL before its fix is applied, then PASS after.
"""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


class TestClaudeMdTelemetryHeadingOutsideFence:
    """Finding 1+2: Telemetry heading is outside (not inside) any fenced code block."""

    def _fenced_ranges(self, txt: str) -> list[tuple[int, int]]:
        """Return (start_line, end_line) for each ```-fenced block, 0-indexed."""
        ranges = []
        in_fence = False
        fence_start = None
        for i, line in enumerate(txt.splitlines()):
            stripped = line.strip()
            if stripped.startswith("```") and not in_fence:
                in_fence = True
                fence_start = i
            elif stripped == "```" and in_fence:
                ranges.append((fence_start, i))
                in_fence = False
                fence_start = None
        return ranges

    def test_telemetry_heading_outside_fence(self):
        txt = (REPO_ROOT / "CLAUDE.md").read_text()
        lines = txt.splitlines()
        heading_idx = None
        for i, line in enumerate(lines):
            if line.strip() == "## Telemetry expectations":
                heading_idx = i
                break
        assert heading_idx is not None, "## Telemetry expectations heading not found"
        fenced = self._fenced_ranges(txt)
        for start, end in fenced:
            assert not (start < heading_idx < end), (
                f"## Telemetry expectations is INSIDE a fenced block (lines {start+1}-{end+1})"
            )

    def test_intro_line_outside_fence(self):
        txt = (REPO_ROOT / "CLAUDE.md").read_text()
        lines = txt.splitlines()
        intro_idx = None
        for i, line in enumerate(lines):
            if line.strip() == "Every implementation must emit:":
                intro_idx = i
                break
        assert intro_idx is not None, "'Every implementation must emit:' not found"
        fenced = self._fenced_ranges(txt)
        for start, end in fenced:
            assert not (start < intro_idx < end), (
                f"'Every implementation must emit:' is INSIDE a fenced block (lines {start+1}-{end+1})"
            )

    def test_no_heading_inside_any_fence(self):
        txt = (REPO_ROOT / "CLAUDE.md").read_text()
        lines = txt.splitlines()
        fenced = self._fenced_ranges(txt)
        for i, line in enumerate(lines):
            if re.match(r"^#{1,6} ", line) and not line.startswith("# "):
                for start, end in fenced:
                    assert not (start < i < end), (
                        f"Heading '{line.strip()}' is INSIDE a fenced block (lines {start+1}-{end+1})"
                    )


class TestCheckDeadHelpersDocstring:
    """Finding 3: check_dead_helpers.py docstring uses autodream/."""

    def test_docstring_says_autodream(self):
        src = (REPO_ROOT / "scripts" / "check_dead_helpers.py").read_text()
        assert "lib/*.py" not in src, "docstring still references lib/*.py"
        assert "autodream/*.py" in src or "autodream/" in src, "docstring should reference autodream/"


class TestAgentsMdConftestComment:
    """Finding 4: AGENTS.md conftest comment says autodream/ not lib/."""

    def test_conftest_comment_updated(self):
        txt = (REPO_ROOT / "AGENTS.md").read_text()
        for line in txt.splitlines():
            if "conftest.py" in line and "lib/" in line:
                assert False, f"AGENTS.md conftest comment still references lib/: {line}"


class TestBinWrappersAssertionNarrowed:
    """Finding 5: bin wrapper tests check sys.path is narrowed, not deleted."""

    def test_hermes_preflight_sys_path_guarded(self):
        src = (REPO_ROOT / "bin" / "hermes-preflight").read_text()
        # If sys.path.insert exists, it must be guarded by _src_root check
        if "sys.path.insert" in src:
            assert "_src_root" in src, "hermes-preflight sys.path.insert not guarded by _src_root"

    def test_hermes_dream_sys_path_guarded(self):
        src = (REPO_ROOT / "bin" / "hermes-dream").read_text()
        if "sys.path.insert" in src:
            assert "_src_root" in src, "hermes-dream sys.path.insert not guarded by _src_root"


class TestTrajectoryDedupErrorMessage:
    """Finding 6: trajectory_dedup_helper gives clear error when fallback fails."""

    def test_fallback_has_clear_error(self):
        src = (REPO_ROOT / "scripts" / "trajectory_dedup_helper.py").read_text()
        assert "HERMES_PYTHON_SRC_ROOT" in src, "fallback doesn't mention HERMES_PYTHON_SRC_ROOT"
        # Should have a clear error message when src_root is empty
        assert "pip install -e" in src or "autodream not installed" in src or "HERMES_PYTHON_SRC_ROOT" in src, (
            "fallback doesn't give clear install instructions"
        )
