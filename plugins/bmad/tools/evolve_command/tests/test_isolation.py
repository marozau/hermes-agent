"""Test isolation gates (TI-1/TI-2).

TI-1: No bare `dspy.configure` / `dspy.settings` calls outside module boundaries.
TI-2: No direct imports of upstream `evolution.*` paths — must use vendored `_vendor.*`.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

# Root of the evolve_command package
PACKAGE_DIR = Path(__file__).parent.parent


def _collect_python_files() -> list[Path]:
    """Collect all .py files in the package (excluding _vendor and tests)."""
    files: list[Path] = []
    for p in PACKAGE_DIR.rglob("*.py"):
        # Skip vendored code and tests
        if "_vendor" in p.parts or "tests" in p.parts:
            continue
        files.append(p)
    return files


def _collect_all_python_files() -> list[Path]:
    """Collect all .py files including vendor and tests."""
    return list(PACKAGE_DIR.rglob("*.py"))


# ── TI-1: No bare dspy.configure outside module boundaries ────────────────

class TestTI1NoBareDspyConfigure:
    """TI-1: No bare dspy.configure / dspy.settings calls."""

    def test_no_bare_dspy_configure(self) -> None:
        """Non-vendor code must not use bare dspy.configure()."""
        pattern = re.compile(r'dspy\.configure\s*\(')
        for f in _collect_python_files():
            content = f.read_text()
            matches = pattern.findall(content)
            assert not matches, (
                f"TI-1 violation in {f.relative_to(PACKAGE_DIR)}: "
                f"bare dspy.configure() call found"
            )

    def test_no_bare_dspy_settings(self) -> None:
        """Non-vendor code must not use bare dspy.settings."""
        pattern = re.compile(r'dspy\.settings\b')
        for f in _collect_python_files():
            content = f.read_text()
            matches = pattern.findall(content)
            assert not matches, (
                f"TI-1 violation in {f.relative_to(PACKAGE_DIR)}: "
                f"bare dspy.settings access found"
            )


# ── TI-2: No direct upstream imports ──────────────────────────────────────

class TestTI2NoUpstreamImports:
    """TI-2: No direct imports of upstream evolution.* paths."""

    FORBIDDEN_PATTERNS = [
        re.compile(r'from\s+evolution\.'),
        re.compile(r'import\s+evolution\.'),
    ]

    def test_no_upstream_imports(self) -> None:
        """All code must use _vendor.* not upstream evolution.*."""
        for f in _collect_all_python_files():
            content = f.read_text()
            for pattern in self.FORBIDDEN_PATTERNS:
                matches = pattern.findall(content)
                assert not matches, (
                    f"TI-2 violation in {f.relative_to(PACKAGE_DIR)}: "
                    f"upstream import found: {matches}"
                )


# ── TI-3: All files have from __future__ import annotations ───────────────

class TestTI3FutureAnnotations:
    """TI-3: All .py files must use from __future__ import annotations."""

    def test_future_annotations(self) -> None:
        """Every .py file should have from __future__ import annotations."""
        for f in _collect_python_files():
            content = f.read_text()
            assert "from __future__ import annotations" in content, (
                f"TI-3 violation in {f.relative_to(PACKAGE_DIR)}: "
                f"missing 'from __future__ import annotations'"
            )


# ── TI-4: Vendored files have attribution header ──────────────────────────

class TestTI4AttributionHeader:
    """TI-4: All vendored files must have the attribution header."""

    ATTRIBUTION_MARKER = "Vendored from NousResearch/hermes-agent-self-evolution"

    def test_vendor_attribution(self) -> None:
        """Vendored files must have attribution header."""
        vendor_dir = PACKAGE_DIR / "_vendor"
        if not vendor_dir.exists():
            pytest.skip("No _vendor directory")

        for f in vendor_dir.glob("*.py"):
            if f.name == "__init__.py":
                continue
            content = f.read_text()
            assert self.ATTRIBUTION_MARKER in content, (
                f"TI-4 violation: {f.name} missing attribution header"
            )
