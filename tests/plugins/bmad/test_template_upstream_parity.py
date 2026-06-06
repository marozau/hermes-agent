"""Tests for E4 byte-identical template parity with upstream BMAD cache."""

from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]
UPSTREAM_BASE = Path("/Users/im/usr-local/hermes-bmad/worktree/hermes-epic-14/skills/bmad")


class TestTemplateUpstreamParity:
    """Templates must be byte-identical copies from BMAD upstream cache."""

    def test_research_template_byte_identical(self):
        """research.template.md must match upstream exactly."""
        ours = REPO_ROOT / "skills" / "bmad" / "templates" / "research.template.md"
        upstream = UPSTREAM_BASE / "bmm" / "research" / "research.template.md"
        assert upstream.exists(), f"upstream missing: {upstream}"
        assert ours.exists(), f"ours missing: {ours}"
        assert ours.read_bytes() == upstream.read_bytes(), (
            f"{ours.name} differs from upstream. E4 violation: "
            f"templates must be byte-identical copies, not ad-hoc."
        )

    def test_prd_template_byte_identical(self):
        """prd.md must match upstream exactly."""
        ours = REPO_ROOT / "skills" / "bmad" / "templates" / "prd.md"
        upstream = UPSTREAM_BASE / "templates" / "prd.md"
        assert upstream.exists(), f"upstream missing: {upstream}"
        assert ours.exists(), f"ours missing: {ours}"
        assert ours.read_bytes() == upstream.read_bytes(), (
            f"{ours.name} differs from upstream. E4 violation: "
            f"templates must be byte-identical copies, not ad-hoc."
        )

    def test_architecture_template_byte_identical(self):
        """architecture.md must match upstream exactly."""
        ours = REPO_ROOT / "skills" / "bmad" / "templates" / "architecture.md"
        upstream = UPSTREAM_BASE / "templates" / "architecture.md"
        assert upstream.exists(), f"upstream missing: {upstream}"
        assert ours.exists(), f"ours missing: {ours}"
        assert ours.read_bytes() == upstream.read_bytes(), (
            f"{ours.name} differs from upstream. E4 violation: "
            f"templates must be byte-identical copies, not ad-hoc."
        )

    def test_product_brief_template_byte_identical(self):
        """product-brief.template.md must match upstream exactly."""
        ours = REPO_ROOT / "skills" / "bmad" / "templates" / "product-brief.template.md"
        upstream = UPSTREAM_BASE / "templates" / "product-brief.template.md"
        assert upstream.exists(), f"upstream missing: {upstream}"
        assert ours.exists(), f"ours missing: {ours}"
        assert ours.read_bytes() == upstream.read_bytes(), (
            f"{ours.name} differs from upstream. E4 violation: "
            f"templates must be byte-identical copies, not ad-hoc."
        )

    def test_epics_stories_template_byte_identical(self):
        """epics-stories.template.md must match upstream exactly."""
        ours = REPO_ROOT / "skills" / "bmad" / "templates" / "epics-stories.template.md"
        upstream = UPSTREAM_BASE / "bmm" / "epics-stories" / "templates" / "epics-template.md"
        assert upstream.exists(), f"upstream missing: {upstream}"
        assert ours.exists(), f"ours missing: {ours}"
        assert ours.read_bytes() == upstream.read_bytes(), (
            f"{ours.name} differs from upstream. E4 violation: "
            f"templates must be byte-identical copies, not ad-hoc."
        )
