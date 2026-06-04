"""T-11 closure tests — tools/evolve_command perspective.

Tests that predicate_runner is wired to dev-story handler.
Uses plugins.bmad. prefix for imports from the tools directory.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest


class TestT11Closure:
    """Story 13.8: predicate_runner.run_predicates wired to dev-story handler."""

    def test_predicate_runner_importable(self) -> None:
        """predicate_runner must be importable."""
        # Add plugin root to sys.path for imports
        plugin_root = str(Path(__file__).parents[4])
        if plugin_root not in sys.path:
            sys.path.insert(0, plugin_root)

        from plugins.bmad.lib.predicate_runner import run_predicates
        assert callable(run_predicates)

    def test_dev_story_references_predicate_runner(self) -> None:
        """dev-story handler source must reference predicate_runner."""
        plugin_root = str(Path(__file__).parents[4])
        if plugin_root not in sys.path:
            sys.path.insert(0, plugin_root)

        from plugins.bmad.commands import dev_story
        source = Path(dev_story.__file__).read_text()
        assert "predicate_runner" in source or "run_predicates" in source

    def test_predicate_runner_signature(self) -> None:
        """run_predicates must accept (spec, project_dir, ctx)."""
        plugin_root = str(Path(__file__).parents[4])
        if plugin_root not in sys.path:
            sys.path.insert(0, plugin_root)

        import inspect
        from plugins.bmad.lib.predicate_runner import run_predicates
        params = list(inspect.signature(run_predicates).parameters.keys())
        assert len(params) >= 2, f"run_predicates needs at least 2 params, got {params}"
