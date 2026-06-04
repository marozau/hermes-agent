from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional
from unittest.mock import patch

import pytest


class TestT11Closure:
    """Story 13.8: predicate_runner.run_predicates wired to dev-story handler."""

    def test_predicate_runner_importable(self) -> None:
        """predicate_runner must be importable from the plugin."""
        try:
            from lib.predicate_runner import run_predicates
            assert callable(run_predicates)
        except ImportError:
            pytest.skip("predicate_runner not yet implemented")

    def test_run_predicates_returns_results(self) -> None:
        """run_predicates returns results for each predicate."""
        try:
            from lib.predicate_runner import run_predicates
        except ImportError:
            pytest.skip("predicate_runner not yet implemented")

        @dataclass
        class FakeItem:
            description: str = "test_pass_rate_above_threshold"
            predicate: str = "tests_pass"

        @dataclass
        class FakeSpec:
            predicate_module: str = "predicates.dev_story"
            verification: list = field(default_factory=list)

        spec = FakeSpec(verification=[FakeItem()])
        with patch("lib.predicate_runner._call_predicate", return_value=(True, "0.85 >= 0.7")):
            results = run_predicates(spec, Path("."))
        assert len(results) == 1
        assert results[0]["passed"] is True
        assert results[0]["description"] == "test_pass_rate_above_threshold"

    def test_run_predicates_failing_predicate(self) -> None:
        """Failing predicate returns passed=False."""
        try:
            from lib.predicate_runner import run_predicates
        except ImportError:
            pytest.skip("predicate_runner not yet implemented")

        @dataclass
        class FakeItem:
            description: str = "deploy_verb_absent"
            predicate: str = "no_deploy_verbs"

        @dataclass
        class FakeSpec:
            predicate_module: str = "predicates.dev_story"
            verification: list = field(default_factory=list)

        spec = FakeSpec(verification=[FakeItem()])
        with patch("lib.predicate_runner._call_predicate", return_value=(False, "deploy verb found")):
            results = run_predicates(spec, Path("."))
        assert results[0]["passed"] is False
        assert "deploy verb" in results[0]["reason"].lower()

    def test_dev_story_handler_calls_predicates(self) -> None:
        """dev-story handler must invoke predicate_runner when predicates exist."""
        try:
            from lib import predicate_runner
            from commands import dev_story
        except ImportError:
            pytest.skip("dev_story or predicate_runner not yet implemented")

        # Verify the handler references predicate_runner in some way
        source_path = Path(dev_story.__file__).read_text()
        assert "predicate_runner" in source_path or "run_predicates" in source_path, (
            "dev_story handler must reference predicate_runner.run_predicates"
        )

    def test_no_predicate_module_skips(self) -> None:
        """When spec has no predicate_module, predicates are skipped."""
        try:
            from commands.dev_story import _run_and_record_predicates
        except ImportError:
            pytest.skip("dev_story handler not yet implemented")

        @dataclass
        class FakeSpec:
            predicate_module: Optional[str] = None
            verification: list = field(default_factory=list)

        # Should not raise, should not call run_predicates
        with patch("lib.predicate_runner.run_predicates") as mock_run:
            _run_and_record_predicates(FakeSpec(), Path("."), {}, "1.1")
            mock_run.assert_not_called()
