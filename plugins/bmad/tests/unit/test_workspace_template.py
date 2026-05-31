"""Tests for Story 6.1 — AGENTS.md template extraction and rendering.

ACs:
- AC-6.1.1: Required sections present
- AC-6.1.2: CLAUDE.md aliasing
- AC-6.1.3: Multi-worktree layout
- AC-6.1.4: No regression on single-worktree case
- AC-6.1.5: Template lints clean (StrictUndefined)
"""

from __future__ import annotations

from pathlib import Path

import pytest


TEMPLATE_DIR = Path(__file__).parent.parent.parent / "templates"


class TestAgentsTemplateExists:
    """Verify template files exist."""

    def test_agents_md_j2_exists(self):
        assert (TEMPLATE_DIR / "AGENTS.md.j2").exists()

    def test_worktrees_md_j2_exists(self):
        assert (TEMPLATE_DIR / "WORKTREES.md.j2").exists()

    def test_envrc_example_exists(self):
        assert (TEMPLATE_DIR / ".envrc.example").exists()


class TestAgentsTemplateRendering:
    """Test Jinja2 rendering of AGENTS.md.j2."""

    def _render(self, **kwargs):
        from jinja2 import BaseLoader, Environment, StrictUndefined

        env = Environment(
            loader=BaseLoader(),
            undefined=StrictUndefined,
            keep_trailing_newline=True,
            autoescape=False,
        )
        template_text = (TEMPLATE_DIR / "AGENTS.md.j2").read_text()
        template = env.from_string(template_text)
        return template.render(**kwargs)

    def _default_vars(self, worktrees=None):
        if worktrees is None:
            worktrees = [{
                "name": "solo",
                "upstream": "/tmp/upstream",
                "branch": "feat/x",
                "path": "worktree/solo",
            }]
        return {
            "project_name": "test-project",
            "project_root": "/tmp/test-workspace",
            "date": "2026-05-31",
            "worktrees": worktrees,
            "has_runtime_mirror": False,
            "mission": "Test mission",
            "feature_description": "Test feature",
            "hard_invariants": "1. Test invariant",
            "canonical_helpers": "def helper(): pass",
            "provider_routing": "N/A",
            "anti_patterns": "None",
        }

    def test_single_worktree_renders(self):
        """AC-6.1.4: Single-worktree case renders without errors."""
        output = self._render(**self._default_vars())
        assert "test-project" in output
        assert "worktree/solo" in output
        assert "feat/x" in output

    def test_multi_worktree_renders(self):
        """AC-6.1.3: Multi-worktree layout renders both worktrees."""
        worktrees = [
            {"name": "repo-a", "upstream": "/tmp/a", "branch": "feat/a", "path": "worktree/repo-a"},
            {"name": "repo-b", "upstream": "/tmp/b", "branch": "feat/b", "path": "worktree/repo-b"},
        ]
        output = self._render(**self._default_vars(worktrees=worktrees))
        assert "repo-a" in output
        assert "repo-b" in output
        assert "feat/a" in output
        assert "feat/b" in output

    def test_required_sections_present(self):
        """AC-6.1.1: Required sections are in the output."""
        output = self._render(**self._default_vars())
        assert "Where to do the work" in output
        assert "Layout" in output
        assert "Mission" in output
        assert "Read order" in output
        assert "Hard invariants" in output
        assert "If you get stuck" in output

    def test_strict_undefined_raises(self):
        """AC-6.1.5: Missing required vars raise during render."""
        from jinja2 import BaseLoader, Environment, StrictUndefined

        env = Environment(
            loader=BaseLoader(),
            undefined=StrictUndefined,
            keep_trailing_newline=True,
            autoescape=False,
        )
        template_text = (TEMPLATE_DIR / "AGENTS.md.j2").read_text()
        template = env.from_string(template_text)

        with pytest.raises(Exception):
            template.render()  # Missing all required vars

    def test_runtime_mirror_section(self):
        """AC-6.1.1: Sync discipline section renders when has_runtime_mirror."""
        worktrees = [{
            "name": "a",
            "upstream": "/tmp/a",
            "branch": "feat/a",
            "path": "worktree/a",
            "runtime_mirror": "~/.hermes/a",
        }]
        vars = self._default_vars(worktrees=worktrees)
        vars["has_runtime_mirror"] = True
        output = self._render(**vars)
        assert "Sync discipline" in output


class TestWorktreesTemplateRendering:
    """Test WORKTREES.md.j2 rendering."""

    def test_renders_table(self):
        from jinja2 import BaseLoader, Environment, StrictUndefined

        env = Environment(
            loader=BaseLoader(),
            undefined=StrictUndefined,
            keep_trailing_newline=True,
            autoescape=False,
        )
        template_text = (TEMPLATE_DIR / "WORKTREES.md.j2").read_text()
        template = env.from_string(template_text)

        output = template.render(
            project_name="test",
            worktrees=[
                {"name": "a", "branch": "feat/a", "last_commit": "2026-05-31"},
            ],
        )
        assert "worktree/a" in output
        assert "feat/a" in output
        assert "idle" in output
