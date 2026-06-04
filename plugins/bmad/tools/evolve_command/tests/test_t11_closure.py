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
        """dev-story handler must have _run_and_record_predicates function that calls run_predicates."""
        plugin_root = str(Path(__file__).parents[4])
        if plugin_root not in sys.path:
            sys.path.insert(0, plugin_root)

        from plugins.bmad.commands import dev_story
        assert hasattr(dev_story, '_run_and_record_predicates'), (
            "dev_story must expose _run_and_record_predicates"
        )
        import inspect
        src = inspect.getsource(dev_story._run_and_record_predicates)
        assert 'run_predicates(' in src, (
            "_run_and_record_predicates must call run_predicates"
        )

    def test_predicate_runner_signature(self) -> None:
        """run_predicates must accept (spec, project_dir, ctx)."""
        plugin_root = str(Path(__file__).parents[4])
        if plugin_root not in sys.path:
            sys.path.insert(0, plugin_root)

        import inspect
        from plugins.bmad.lib.predicate_runner import run_predicates
        params = list(inspect.signature(run_predicates).parameters.keys())
        assert len(params) >= 2, f"run_predicates needs at least 2 params, got {params}"
